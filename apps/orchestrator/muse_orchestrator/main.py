from __future__ import annotations
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from contextvars import ContextVar
import json
import logging
import re
import sys
import time
import traceback
import uuid
from arq import create_pool
from arq.connections import RedisSettings
from typing import Any
from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.application.system_service import check_readiness
from muse_orchestrator.core.config import SecretRedactor, settings
from muse_orchestrator.core.database import get_db, init_db
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.providers.base import ErrorCategory, ProviderFailure
from muse_orchestrator.services.asset_storage import AssetStorageError
from muse_orchestrator.services.workflow_engine import WorkflowValidationError

from muse_orchestrator.api.projects import router as projects_router
from muse_orchestrator.api.connections import router as connections_router
from muse_orchestrator.api.assets import router as assets_router
from muse_orchestrator.api.pipeline import router as pipeline_router
from muse_orchestrator.api.workflows import router as workflows_router
from muse_orchestrator.api.queue import router as queue_router
from muse_orchestrator.api.batch import router as batch_router
from muse_orchestrator.api.timeline import router as timeline_router
from muse_orchestrator.api.connection_accounts import router as connection_accounts_router
from muse_orchestrator.api.metrics import router as metrics_router
from muse_orchestrator.api.maintenance import router as maintenance_router
from muse_orchestrator.api.backups import router as backups_router
from muse_orchestrator.api.system import router as system_router

logger = logging.getLogger("muse_orchestrator")

# --- request correlation and process logging ---------------------------------------------

_request_id_var: ContextVar[str] = ContextVar("muse_request_id", default="-")
_REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def current_request_id() -> str:
    return _request_id_var.get()


class _RedactingContextFilter(logging.Filter):
    """Attach the request id and redact secrets from every formatted log message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 -- never let logging crash a request
            message = str(record.msg)
        record.msg = SecretRedactor.redact_text(message)
        record.args = None
        if record.exc_info:
            record.exc_text = SecretRedactor.redact_text(
                "".join(traceback.format_exception(*record.exc_info))
            )
            record.exc_info = None
        return True


class _KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_text:
            fields["exc"] = record.exc_text
        return " ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


_LOG_HANDLER_MARKER = "_muse_structured_handler"


def configure_logging() -> None:
    """Install one redacting stdout handler on the application logger (idempotent)."""
    if any(getattr(h, _LOG_HANDLER_MARKER, False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, _LOG_HANDLER_MARKER, True)
    handler.addFilter(_RedactingContextFilter())
    handler.setFormatter(_JsonFormatter() if settings.log_format == "json" else _KeyValueFormatter())
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, settings.log_level.strip().upper(), logging.INFO))


class ErrorEnvelope(BaseModel):
    """Stable response contract shared by validation and HTTP/application errors."""

    detail: Any
    category: str
    retryable: bool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    if settings.insecure_dev_defaults:
        logger.warning(
            "\n"
            "================================================================================\n"
            "SECURITY WARNING: MuseStudio is running with INSECURE DEVELOPMENT DEFAULTS.\n"
            "The admin API is unauthenticated while STUDIO_ADMIN_TOKEN is unset, and\n"
            "provider secrets are encrypted with a PUBLICLY KNOWN development key.\n"
            "Do NOT expose this instance beyond your own machine. Set ENVIRONMENT to a\n"
            "non-development value with real secrets before deploying anywhere.\n"
            "================================================================================"
        )
    await init_db()
    arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.arq_pool = arq_pool
    try:
        yield
    finally:
        await arq_pool.aclose()


app = FastAPI(title="MuseStudio Orchestrator API", version="0.7.0", lifespan=lifespan)

# Keep the local dev UI origin explicit and allow only browser methods/headers
# used by the API. Production serves the UI through same-origin reverse proxying.
_CORS_ALLOWED_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_CORS_ALLOWED_HEADERS = ["Accept", "Authorization", "Content-Type", "X-Studio-Admin-Token", _REQUEST_ID_HEADER]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=_CORS_ALLOWED_METHODS,
    allow_headers=_CORS_ALLOWED_HEADERS,
    expose_headers=[_REQUEST_ID_HEADER],
)


def _host_name(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _cross_site_rejection_reason(request: Request) -> str | None:
    """CSRF check for the unauthenticated dev server.

    Browsers send "simple" cross-origin requests (form/multipart POST, text/plain bodies)
    without a CORS preflight, so CORS alone does not stop a malicious page from mutating
    a local studio. Reject state-changing requests whose Origin is not an allowed dev
    origin, or which the browser marks as cross-site. Non-browser clients (no Origin and
    no Sec-Fetch-Site) are unaffected.
    """
    if request.method.upper() in _SAFE_METHODS:
        return None
    origin = request.headers.get("origin")
    if origin is not None:
        if origin.strip().lower().rstrip("/") in settings.dev_allowed_origin_set:
            return None
        return "Origin is not allowed to modify an unauthenticated development server."
    if request.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        return "Cross-site requests may not modify an unauthenticated development server."
    return None


@app.middleware("http")
async def dev_host_guard(request: Request, call_next: Any) -> Any:
    """Mitigate DNS rebinding and CSRF while the API is unauthenticated with dev defaults.

    Only active when ``settings.insecure_dev_defaults`` is true AND no admin token is
    configured; production configs (which require a token) are unaffected.
    """
    if settings.insecure_dev_defaults and not (settings.studio_admin_token or "").strip():
        host = _host_name(request.headers.get("host", ""))
        if host not in settings.dev_allowed_host_set:
            return _error_envelope_response(
                status.HTTP_400_BAD_REQUEST,
                "Host header is not allowed for an unauthenticated development server.",
                category="invalid_request",
                retryable=False,
            )
        reason = _cross_site_rejection_reason(request)
        if reason is not None:
            return _error_envelope_response(
                status.HTTP_403_FORBIDDEN, reason, category="forbidden", retryable=False
            )
    return await call_next(request)


# Registered last so it is the OUTERMOST middleware: every response, including guard
# rejections, carries the request id.
@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Any) -> Any:
    supplied = request.headers.get(_REQUEST_ID_HEADER, "")
    request_id = supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
    token = _request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        # Path only: query strings may carry tokens.
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response
    finally:
        _request_id_var.reset(token)


_ERROR_STATUS_DEFAULTS: dict[int, tuple[str, bool]] = {
    400: ("invalid_request", False),
    401: ("auth_failed", False),
    403: ("forbidden", False),
    404: ("not_found", False),
    409: ("conflict", False),
    413: ("payload_too_large", False),
    422: ("validation", False),
    429: ("rate_limited", True),
    500: ("internal_error", False),
    502: ("upstream_5xx", True),
    503: ("service_unavailable", True),
    504: ("upstream_timeout", True),
}


def _error_envelope_response(
    status_code: int,
    detail: Any,
    *,
    category: str | None = None,
    retryable: bool | None = None,
) -> JSONResponse:
    default_cat, default_retry = _ERROR_STATUS_DEFAULTS.get(
        status_code,
        ("upstream_5xx" if status_code >= 500 else "invalid_request", status_code in {502, 503, 504}),
    )
    if isinstance(detail, str):
        safe_detail: Any = SecretRedactor.redact_text(detail)
    elif isinstance(detail, dict):
        safe_detail = SecretRedactor.redact_dict(detail)
    else:
        safe_detail = SecretRedactor.redact_value(jsonable_encoder(detail))
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": safe_detail,
            "category": category or default_cat,
            "retryable": default_retry if retryable is None else retryable,
        },
        headers={_REQUEST_ID_HEADER: _request_id_var.get()} if _request_id_var.get() != "-" else None,
    )


@app.exception_handler(ApplicationError)
async def application_error_handler(_request: Request, exc: ApplicationError) -> JSONResponse:
    return _error_envelope_response(
        exc.status_code, exc.detail, category=exc.category, retryable=exc.retryable
    )


@app.exception_handler(AssetStorageError)
async def asset_storage_exception_handler(_request: Request, exc: AssetStorageError) -> JSONResponse:
    return _error_envelope_response(
        status.HTTP_400_BAD_REQUEST,
        str(exc),
        category="invalid_request",
        retryable=False,
    )


@app.exception_handler(WorkflowValidationError)
async def workflow_validation_exception_handler(_request: Request, exc: WorkflowValidationError) -> JSONResponse:
    return _error_envelope_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        str(exc),
        category="validation",
        retryable=False,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error_envelope_response(exc.status_code, exc.detail)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Pydantic validation errors include the raw submitted value under "input".
    # Never echo it: a malformed field can contain an API key, password, or proxy URL.
    safe_errors = [
        {key: value for key, value in error.items() if key not in {"input", "ctx"}}
        for error in exc.errors()
    ]
    return _error_envelope_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        safe_errors,
        category="validation",
        retryable=False,
    )


_UNIQUE_VIOLATION_SQLSTATE = "23505"
_UNIQUE_VIOLATION_MARKERS = ("unique constraint failed", "duplicate key value violates unique constraint")


def is_unique_violation(exc: IntegrityError) -> bool:
    """True only for uniqueness conflicts (PostgreSQL 23505 / SQLite UNIQUE constraint)."""
    candidates: list[Any] = [exc.orig, getattr(exc.orig, "__cause__", None)]
    for candidate in candidates:
        if candidate is None:
            continue
        for attr in ("sqlstate", "pgcode"):
            if str(getattr(candidate, attr, "") or "") == _UNIQUE_VIOLATION_SQLSTATE:
                return True
        if type(candidate).__name__ == "UniqueViolationError":
            return True
    text = str(exc.orig or "").lower()
    return any(marker in text for marker in _UNIQUE_VIOLATION_MARKERS)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    if not is_unique_violation(exc):
        # FK / NOT NULL / CHECK violations are server bugs, not client conflicts.
        return await unhandled_exception_handler(request, exc)
    # A uniqueness race lost against a concurrent writer. Never echo the SQL statement or
    # bound parameters: they can contain encrypted secrets or user data.
    logger.warning(
        "Database uniqueness conflict: %s",
        SecretRedactor.redact_text(str(getattr(exc, "orig", "") or type(exc).__name__))[:500],
    )
    return _error_envelope_response(
        status.HTTP_409_CONFLICT,
        "The request conflicts with existing data.",
        category="conflict",
        retryable=False,
    )


def _sqlstate(exc: DBAPIError) -> str:
    for candidate in (exc.orig, getattr(exc.orig, "__cause__", None)):
        for attr in ("sqlstate", "pgcode"):
            value = str(getattr(candidate, attr, "") or "")
            if value:
                return value
    return ""


@app.exception_handler(DBAPIError)
async def dbapi_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
    # SQLAlchemy's asyncpg dialect does not translate asyncpg DataError subclasses
    # (e.g. StringDataRightTruncationError, SQLSTATE 22001) into sqlalchemy DataError,
    # so classify SQLSTATE class 22 ("data exception") here as a client error.
    if _sqlstate(exc).startswith("22"):
        return await data_error_handler(request, exc)
    return await unhandled_exception_handler(request, exc)


@app.exception_handler(DataError)
async def data_error_handler(_request: Request, exc: DBAPIError) -> JSONResponse:
    # e.g. a value longer than its VARCHAR column on PostgreSQL (SQLSTATE 22001).
    logger.warning(
        "Database rejected a value: %s",
        SecretRedactor.redact_text(str(getattr(exc, "orig", "") or type(exc).__name__))[:500],
    )
    return _error_envelope_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "A value was rejected by the database (too long or wrong type).",
        category="validation",
        retryable=False,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: stable envelope, no internal details in the response body."""
    redacted_trace = SecretRedactor.redact_text(
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    )
    logger.error(
        "Unhandled %s on %s %s\n%s",
        type(exc).__name__,
        request.method,
        request.url.path,
        redacted_trace,
    )
    return _error_envelope_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Internal server error.",
        category="internal_error",
        retryable=False,
    )


_PROVIDER_FAILURE_STATUS: dict[ErrorCategory, int] = {
    ErrorCategory.INVALID_REQUEST: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ErrorCategory.RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCategory.QUOTA_EXHAUSTED: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCategory.UPSTREAM_POLL_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
    ErrorCategory.AUTH_FAILED: status.HTTP_502_BAD_GATEWAY,
    ErrorCategory.UPSTREAM_5XX: status.HTTP_502_BAD_GATEWAY,
    ErrorCategory.TRANSIENT_NETWORK: status.HTTP_502_BAD_GATEWAY,
    ErrorCategory.INPUT_NOT_PENDING: status.HTTP_409_CONFLICT,
}
# The request may have reached the provider (read timeout / dropped connection after send).
if hasattr(ErrorCategory, "DISPATCH_OUTCOME_UNKNOWN"):
    _PROVIDER_FAILURE_STATUS[ErrorCategory.DISPATCH_OUTCOME_UNKNOWN] = status.HTTP_504_GATEWAY_TIMEOUT


def provider_failure_status(exc: ProviderFailure) -> int:
    if exc.category == ErrorCategory.TRANSIENT_NETWORK and "timeout" in exc.message.lower():
        return status.HTTP_504_GATEWAY_TIMEOUT
    return _PROVIDER_FAILURE_STATUS.get(exc.category, status.HTTP_502_BAD_GATEWAY)


@app.exception_handler(ProviderFailure)
async def provider_failure_exception_handler(request: Request, exc: ProviderFailure) -> JSONResponse:
    # Category and retryability come from the provider classification; the detail is the
    # redacted provider message only (no "(HTTP None, retryable=False)" decoration).
    return _error_envelope_response(
        provider_failure_status(exc),
        exc.message or exc.category.value,
        category=exc.category.value,
        retryable=exc.retryable,
    )


_protected_dependencies = [Depends(verify_single_admin)]
_ERROR_RESPONSE_DESCRIPTIONS = {
    400: "Invalid request",
    401: "Authentication failed",
    403: "Forbidden",
    404: "Resource not found",
    409: "Conflict",
    413: "Payload too large",
    422: "Validation Error",
    429: "Rate limited",
    500: "Internal server error",
    502: "Upstream provider error",
    503: "Service unavailable",
    504: "Upstream timeout",
}
_COMMON_ERROR_RESPONSES = {
    code: {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
            }
        },
    }
    for code, description in _ERROR_RESPONSE_DESCRIPTIONS.items()
}
app.include_router(projects_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(connections_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(assets_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(pipeline_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(workflows_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(queue_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(batch_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(timeline_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(connection_accounts_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(metrics_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(maintenance_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(backups_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)
app.include_router(system_router, dependencies=_protected_dependencies, responses=_COMMON_ERROR_RESPONSES)


def _custom_openapi() -> dict[str, Any]:
    """Publish the same ErrorEnvelope component used by the shared error responses."""
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema.setdefault("components", {}).setdefault("schemas", {})["ErrorEnvelope"] = (
        ErrorEnvelope.model_json_schema()
    )
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


@app.get("/healthz", responses=_COMMON_ERROR_RESPONSES)
async def healthz() -> dict[str, str]:
    """Liveness: the process is serving requests (no dependency checks)."""
    return {"status": "ok", "service": "muse-orchestrator", "milestone": "M7"}


class ReadinessOut(BaseModel):
    status: str
    checks: dict[str, str]


@app.get("/readyz", response_model=ReadinessOut, responses=_COMMON_ERROR_RESPONSES)
async def readyz(request: Request, db: AsyncSession = Depends(get_db)) -> Any:
    """Readiness: database and Redis are reachable. Public like /healthz; no secrets."""
    checks = await check_readiness(db, getattr(request.app.state, "arq_pool", None))
    if all(value == "ok" for value in checks.values()):
        return {"status": "ready", "checks": checks}
    return _error_envelope_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        {"status": "not_ready", "checks": checks},
        category="service_unavailable",
        retryable=True,
    )

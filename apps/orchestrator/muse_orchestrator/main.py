from __future__ import annotations
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
import logging
from arq import create_pool
from arq.connections import RedisSettings
from typing import Any
from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.core.config import SecretRedactor, settings
from muse_orchestrator.core.database import init_db
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.providers.base import ProviderFailure
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
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
_CORS_ALLOWED_HEADERS = ["Accept", "Authorization", "Content-Type", "X-Studio-Admin-Token"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=_CORS_ALLOWED_METHODS,
    allow_headers=_CORS_ALLOWED_HEADERS,
)


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
    )


@app.exception_handler(ApplicationError)
async def application_error_handler(_request: Request, exc: ApplicationError) -> JSONResponse:
    return _error_envelope_response(exc.status_code, exc.detail)


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


@app.exception_handler(ProviderFailure)
async def provider_failure_exception_handler(request: Request, exc: ProviderFailure) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "detail": SecretRedactor.redact_text(str(exc)),
            "category": exc.category.value,
            "retryable": exc.retryable,
        },
    )


_protected_dependencies = [Depends(verify_single_admin)]
app.include_router(projects_router, dependencies=_protected_dependencies)
app.include_router(connections_router, dependencies=_protected_dependencies)
app.include_router(assets_router, dependencies=_protected_dependencies)
app.include_router(pipeline_router, dependencies=_protected_dependencies)
app.include_router(workflows_router, dependencies=_protected_dependencies)
app.include_router(queue_router, dependencies=_protected_dependencies)
app.include_router(batch_router, dependencies=_protected_dependencies)
app.include_router(timeline_router, dependencies=_protected_dependencies)
app.include_router(connection_accounts_router, dependencies=_protected_dependencies)
app.include_router(metrics_router, dependencies=_protected_dependencies)
app.include_router(maintenance_router, dependencies=_protected_dependencies)
app.include_router(backups_router, dependencies=_protected_dependencies)
app.include_router(system_router, dependencies=_protected_dependencies)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "muse-orchestrator", "milestone": "M7"}
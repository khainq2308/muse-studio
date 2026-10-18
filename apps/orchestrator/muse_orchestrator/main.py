from __future__ import annotations
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.core.config import SecretRedactor
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield


app = FastAPI(title="MuseStudio Orchestrator API", version="0.7.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApplicationError)
async def application_error_handler(_request: Request, exc: ApplicationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(AssetStorageError)
async def asset_storage_exception_handler(request: Request, exc: AssetStorageError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": SecretRedactor.redact_text(str(exc))},
    )


@app.exception_handler(WorkflowValidationError)
async def workflow_validation_exception_handler(request: Request, exc: WorkflowValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": SecretRedactor.redact_text(str(exc))},
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
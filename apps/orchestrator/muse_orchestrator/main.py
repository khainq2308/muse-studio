from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from muse_orchestrator.api.assets import router as assets_router
from muse_orchestrator.api.connections import router as connections_router
from muse_orchestrator.api.pipeline import router as pipeline_router
from muse_orchestrator.api.projects import router as projects_router
from muse_orchestrator.api.workflows import router as workflows_router
from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.core.database import init_db
from muse_orchestrator.core.security import verify_single_admin


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield


app = FastAPI(title="MuseStudio Orchestrator API", version="0.3.1", lifespan=lifespan)
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


# Health is intentionally public for liveness checks. Every product API router uses
# the same admin-token dependency so reads, writes, assets and workflow actions
# cannot accidentally diverge in access control.
_protected_dependencies = [Depends(verify_single_admin)]
app.include_router(projects_router, dependencies=_protected_dependencies)
app.include_router(connections_router, dependencies=_protected_dependencies)
app.include_router(assets_router, dependencies=_protected_dependencies)
app.include_router(pipeline_router, dependencies=_protected_dependencies)
app.include_router(workflows_router, dependencies=_protected_dependencies)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "muse-orchestrator", "milestone": "M3"}

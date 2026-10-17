from __future__ import annotations
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from muse_orchestrator.core.database import init_db
from muse_orchestrator.api.projects import router as projects_router
from muse_orchestrator.api.connections import router as connections_router
from muse_orchestrator.api.assets import router as assets_router
from muse_orchestrator.api.pipeline import router as pipeline_router
from muse_orchestrator.api.workflows import router as workflows_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield


app = FastAPI(title="MuseStudio Orchestrator API", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router)
app.include_router(connections_router)
app.include_router(assets_router)
app.include_router(pipeline_router)
app.include_router(workflows_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "muse-orchestrator", "milestone": "M3"}

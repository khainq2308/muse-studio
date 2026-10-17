from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from muse_orchestrator.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.services.metrics_service import (
    collect_summary,
    render_prometheus,
)

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


@router.get("/summary")
async def metrics_summary(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """M7: JSON observability snapshot (queue depth, health, storage, ...)."""
    return await collect_summary(db)


@router.get("/prometheus", response_class=PlainTextResponse)
async def metrics_prometheus(db: AsyncSession = Depends(get_db)) -> str:
    """M7: same snapshot in Prometheus text exposition format for scraping."""
    return render_prometheus(await collect_summary(db))

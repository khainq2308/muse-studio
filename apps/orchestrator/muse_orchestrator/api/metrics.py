from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from muse_orchestrator.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.services.metrics_service import (
    collect_summary,
    render_prometheus,
)

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


class ServiceMetricsOut(BaseModel):
    name: str
    version: str
    environment: str
    uptime_seconds: float
    started_at: float


class CountOut(BaseModel):
    total: int


class ConnectionMetricsOut(BaseModel):
    total: int
    by_health: dict[str, int]
    accounts_total: int


class QueueMetricsOut(BaseModel):
    depth: int
    generation_tasks_by_state: dict[str, int]
    generation_tasks_total: int


class AssetMetricsOut(BaseModel):
    total: int
    total_bytes: int


class ExportMetricsOut(BaseModel):
    total: int
    by_state: dict[str, int]


class BackupMetricsOut(BaseModel):
    total: int
    total_bytes: int


class StorageMetricsOut(BaseModel):
    media_root: str
    total_bytes: int
    file_count: int


class MetricsSummaryOut(BaseModel):
    service: ServiceMetricsOut
    projects: CountOut
    connections: ConnectionMetricsOut
    queue: QueueMetricsOut
    assets: AssetMetricsOut
    exports: ExportMetricsOut
    backups: BackupMetricsOut
    storage: StorageMetricsOut


@router.get("/summary", response_model=MetricsSummaryOut)
async def metrics_summary(db: AsyncSession = Depends(get_db)) -> MetricsSummaryOut:
    """M7: JSON observability snapshot (queue depth, health, storage, ...)."""
    return MetricsSummaryOut.model_validate(await collect_summary(db))


@router.get("/prometheus", response_class=PlainTextResponse)
async def metrics_prometheus(db: AsyncSession = Depends(get_db)) -> str:
    """M7: same snapshot in Prometheus text exposition format for scraping."""
    return render_prometheus(await collect_summary(db))

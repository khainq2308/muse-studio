from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from muse_orchestrator.api.dependencies import get_system_service
from muse_orchestrator.application.system_service import SystemService
from muse_orchestrator.models.domain import StructuredLogRecord

router = APIRouter(prefix="/api/v1/system", tags=["M7 System Operations"])


class LogCreateIn(BaseModel):
    correlation_id: str | None = Field(default=None, max_length=64)
    level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR)$")
    event: str = Field(..., min_length=1, max_length=128)
    project_id: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def _to_log_out(entry: StructuredLogRecord) -> dict[str, Any]:
    return {
        "id": entry.id,
        "correlation_id": entry.correlation_id,
        "level": entry.level,
        "event": entry.event,
        "project_id": entry.project_id,
        "task_id": entry.task_id,
        "payload_redacted": entry.payload_redacted,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.post("/logs", status_code=status.HTTP_201_CREATED)
async def create_log(
    payload: LogCreateIn,
    service: SystemService = Depends(get_system_service),
) -> dict[str, Any]:
    """Record a structured operational log entry (payload is redacted at rest)."""
    entry = await service.record_log(payload.model_dump())
    return _to_log_out(entry)


@router.get("/logs")
async def list_logs(
    level: str | None = Query(default=None, pattern=r"^(DEBUG|INFO|WARNING|ERROR)$"),
    event: str | None = None,
    correlation_id: str | None = None,
    project_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    service: SystemService = Depends(get_system_service),
) -> list[dict[str, Any]]:
    """List structured log entries, newest first."""
    entries = await service.list_logs(
        level=level,
        event=event,
        correlation_id=correlation_id,
        project_id=project_id,
        limit=limit,
    )
    return [_to_log_out(e) for e in entries]


@router.get("/diagnostics")
async def get_diagnostics(
    service: SystemService = Depends(get_system_service),
) -> dict[str, Any]:
    """Redacted system diagnostics bundle: metrics summary, accounts, recent audits/logs."""
    return await service.get_diagnostics()

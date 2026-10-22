from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from muse_orchestrator.api.dependencies import get_system_service
from muse_orchestrator.api.metrics import MetricsSummaryOut
from muse_orchestrator.api.response_types import JsonValue
from muse_orchestrator.application.system_service import SystemService
from muse_orchestrator.models.domain import StructuredLogRecord

router = APIRouter(prefix="/api/v1/system", tags=["M7 System Operations"])


class LogCreateIn(BaseModel):
    correlation_id: str | None = Field(default=None, max_length=64)
    level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR)$")
    event: str = Field(..., min_length=1, max_length=128)
    project_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class StructuredLogOut(BaseModel):
    id: str
    correlation_id: str
    level: str
    event: str
    project_id: str | None
    task_id: str | None
    payload_redacted: dict[str, JsonValue]
    created_at: datetime | None


class DiagnosticAccountOut(BaseModel):
    id: str
    connection_id: str
    label: str
    api_key_masked: str
    proxy_display_host: str | None
    enabled: bool
    health_state: str


class DiagnosticAuditEventOut(BaseModel):
    id: str
    actor: str
    action: str
    target_type: str
    target_id: str
    details_redacted: dict[str, JsonValue]
    created_at: datetime | None


class DiagnosticRecentLogOut(BaseModel):
    id: str
    correlation_id: str
    level: str
    event: str
    project_id: str | None
    task_id: str | None
    created_at: datetime | None


class SystemDiagnosticsOut(BaseModel):
    generated_by: str
    summary: MetricsSummaryOut
    accounts: list[DiagnosticAccountOut]
    recent_audit_events: list[DiagnosticAuditEventOut]
    recent_logs: list[DiagnosticRecentLogOut]


def _to_log_out(entry: StructuredLogRecord) -> StructuredLogOut:
    return StructuredLogOut(
        id=entry.id,
        correlation_id=entry.correlation_id,
        level=entry.level,
        event=entry.event,
        project_id=entry.project_id,
        task_id=entry.task_id,
        payload_redacted=entry.payload_redacted,
        created_at=entry.created_at,
    )


@router.post("/logs", response_model=StructuredLogOut, status_code=status.HTTP_201_CREATED)
async def create_log(
    payload: LogCreateIn,
    service: SystemService = Depends(get_system_service),
) -> StructuredLogOut:
    """Record a structured operational log entry (payload is redacted at rest)."""
    entry = await service.record_log(payload.model_dump())
    return _to_log_out(entry)


@router.get("/logs", response_model=list[StructuredLogOut])
async def list_logs(
    level: str | None = Query(default=None, pattern=r"^(DEBUG|INFO|WARNING|ERROR)$"),
    event: str | None = Query(default=None, max_length=128),
    correlation_id: str | None = Query(default=None, max_length=64),
    project_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    service: SystemService = Depends(get_system_service),
) -> list[StructuredLogOut]:
    """List structured log entries, newest first."""
    entries = await service.list_logs(
        level=level,
        event=event,
        correlation_id=correlation_id,
        project_id=project_id,
        limit=limit,
    )
    return [_to_log_out(e) for e in entries]


@router.get("/diagnostics", response_model=SystemDiagnosticsOut)
async def get_diagnostics(
    service: SystemService = Depends(get_system_service),
) -> SystemDiagnosticsOut:
    """Redacted system diagnostics bundle: metrics summary, accounts, recent audits/logs."""
    return SystemDiagnosticsOut.model_validate(await service.get_diagnostics())

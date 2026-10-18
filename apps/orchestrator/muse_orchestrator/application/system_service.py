from __future__ import annotations

import uuid
from typing import Any

from muse_orchestrator.core.config import SecretRedactor
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import AuditEvent, StructuredLogRecord
from muse_orchestrator.services import metrics_service

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class SystemService:
    """M7: structured operational logs + redacted diagnostics (ported from PR #9)."""

    def __init__(self, repository: StudioRepository) -> None:
        self.repository = repository

    async def record_log(self, values: dict[str, Any]) -> StructuredLogRecord:
        level = str(values.get("level", "INFO")).upper()
        if level not in _LOG_LEVELS:
            level = "INFO"
        entry = StructuredLogRecord(
            id=f"log_{uuid.uuid4().hex[:10]}",
            correlation_id=str(values.get("correlation_id") or f"corr_{uuid.uuid4().hex[:10]}"),
            level=level,
            event=str(values.get("event", "")).strip()[:128],
            project_id=values.get("project_id"),
            task_id=values.get("task_id"),
            payload_redacted=SecretRedactor.redact_dict(dict(values.get("payload") or {})),
        )
        if not entry.event:
            from muse_orchestrator.application.services import ApplicationError

            raise ApplicationError(422, "Log event cannot be blank.")
        return await self.repository.create_structured_log(entry)

    async def list_logs(
        self,
        *,
        level: str | None = None,
        event: str | None = None,
        correlation_id: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[StructuredLogRecord]:
        return await self.repository.list_structured_logs(
            level=level,
            event=event,
            correlation_id=correlation_id,
            project_id=project_id,
            limit=limit,
        )

    async def get_diagnostics(self) -> dict[str, Any]:
        """Redacted system diagnostics bundle: never contains secrets."""
        db = self.repository.session
        summary = await metrics_service.collect_summary(db)
        accounts = await self.repository.list_all_accounts()
        audits = await self.repository.list_audit_events(limit=50)
        logs = await self.repository.list_structured_logs(limit=50)
        bundle = {
            "generated_by": "m7-system-service",
            "summary": summary,
            "accounts": [
                {
                    "id": a.id,
                    "connection_id": a.connection_id,
                    "label": a.label,
                    "api_key_masked": a.api_key_masked,
                    "proxy_display_host": a.proxy_display_host,
                    "enabled": a.enabled,
                    "health_state": a.health_state.value
                    if hasattr(a.health_state, "value")
                    else str(a.health_state),
                }
                for a in accounts
            ],
            "recent_audit_events": [
                {
                    "id": e.id,
                    "actor": e.actor,
                    "action": e.action,
                    "target_type": e.target_type,
                    "target_id": e.target_id,
                    "details_redacted": e.details_redacted,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in audits
            ],
            "recent_logs": [
                {
                    "id": e.id,
                    "correlation_id": e.correlation_id,
                    "level": e.level,
                    "event": e.event,
                    "project_id": e.project_id,
                    "task_id": e.task_id,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in logs
            ],
        }
        return SecretRedactor.redact_dict(bundle)


async def write_audit(
    repository: StudioRepository,
    *,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
    actor: str = "admin_operator",
) -> None:
    """M7: helper for audit trail writes (ported from PR #9)."""
    event = AuditEvent(
        id=f"aud_{uuid.uuid4().hex[:10]}",
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details_redacted=SecretRedactor.redact_dict(details or {}),
    )
    await repository.create_audit_event(event)

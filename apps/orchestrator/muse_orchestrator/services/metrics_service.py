from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import (
    Asset,
    BackupRecord,
    ConnectionAccount,
    ExportJobRecord,
    GenerationTaskRecord,
    Project,
    ProviderConnection,
    QueueTaskState,
)

_PROCESS_START_MONOTONIC = time.monotonic()
_PROCESS_START_WALL = time.time()


async def _count(db: AsyncSession, model: Any) -> int:
    return (await db.execute(select(func.count(model.id)))).scalar_one()


async def _count_by(
    db: AsyncSession, model: Any, column: Any
) -> dict[str, int]:
    rows = (await db.execute(select(column, func.count(model.id)).group_by(column))).all()
    result: dict[str, int] = {}
    for value, count in rows:
        key = value.value if hasattr(value, "value") else str(value)
        result[key] = int(count)
    return result


async def _sum_column(db: AsyncSession, model: Any, column: Any) -> int:
    value = (await db.execute(select(func.coalesce(func.sum(column), 0)))).scalar_one()
    return int(value)


def _media_root_usage() -> dict[str, int]:
    total_bytes = 0
    file_count = 0
    root = settings.media_root
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total_bytes += os.path.getsize(os.path.join(dirpath, name))
                file_count += 1
            except OSError:
                continue
    return {"total_bytes": total_bytes, "file_count": file_count}


async def collect_summary(db: AsyncSession) -> dict[str, Any]:
    """M7: gather a point-in-time observability snapshot of the orchestrator."""
    task_states = await _count_by(db, GenerationTaskRecord, GenerationTaskRecord.state)
    queue_depth = sum(
        task_states.get(state, 0) for state in (QueueTaskState.QUEUED.value, QueueTaskState.PAUSED.value)
    )
    return {
        "service": {
            "name": settings.app_name,
            "version": "0.7.0",
            "environment": settings.environment,
            "uptime_seconds": round(time.monotonic() - _PROCESS_START_MONOTONIC, 1),
            "started_at": _PROCESS_START_WALL,
        },
        "projects": {"total": await _count(db, Project)},
        "connections": {
            "total": await _count(db, ProviderConnection),
            "by_health": await _count_by(db, ProviderConnection, ProviderConnection.health_state),
            "accounts_total": await _count(db, ConnectionAccount),
        },
        "queue": {
            "depth": queue_depth,
            "generation_tasks_by_state": task_states,
            "generation_tasks_total": await _count(db, GenerationTaskRecord),
        },
        "assets": {
            "total": await _count(db, Asset),
            "total_bytes": await _sum_column(db, Asset, Asset.size_bytes),
        },
        "exports": {
            "total": await _count(db, ExportJobRecord),
            "by_state": await _count_by(db, ExportJobRecord, ExportJobRecord.state),
        },
        "backups": {
            "total": await _count(db, BackupRecord),
            "total_bytes": await _sum_column(db, BackupRecord, BackupRecord.size_bytes),
        },
        "storage": {
            "media_root": settings.media_root,
            **(await asyncio.to_thread(_media_root_usage)),
        },
    }


def render_prometheus(summary: dict[str, Any]) -> str:
    """Render the summary in Prometheus text exposition format (no extra deps)."""
    lines: list[str] = []

    def gauge(name: str, help_text: str, value: float, labels: dict[str, str] | None = None) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        if labels:
            label_str = ",".join(f'{key}="{val}"' for key, val in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")

    svc = summary["service"]
    gauge("musestudio_uptime_seconds", "Orchestrator process uptime.", svc["uptime_seconds"])
    gauge("musestudio_projects_total", "Total projects.", summary["projects"]["total"])
    gauge("musestudio_connections_total", "Total provider connections.", summary["connections"]["total"])
    for health, count in summary["connections"]["by_health"].items():
        gauge(
            "musestudio_connections_by_health",
            "Provider connections by health state.",
            count,
            {"health": health},
        )
    gauge(
        "musestudio_connection_accounts_total",
        "Total connection accounts.",
        summary["connections"]["accounts_total"],
    )
    gauge("musestudio_queue_depth", "Queued or paused generation tasks.", summary["queue"]["depth"])
    for state, count in summary["queue"]["generation_tasks_by_state"].items():
        gauge(
            "musestudio_generation_tasks_by_state",
            "Generation tasks by state.",
            count,
            {"state": state},
        )
    gauge("musestudio_assets_total", "Total assets.", summary["assets"]["total"])
    gauge("musestudio_assets_bytes", "Total asset bytes.", summary["assets"]["total_bytes"])
    for state, count in summary["exports"]["by_state"].items():
        gauge(
            "musestudio_exports_by_state", "Export jobs by state.", count, {"state": state}
        )
    gauge("musestudio_backups_total", "Total project backups.", summary["backups"]["total"])
    gauge("musestudio_backups_bytes", "Total backup bytes.", summary["backups"]["total_bytes"])
    gauge(
        "musestudio_media_root_bytes",
        "Bytes on disk under media root.",
        summary["storage"]["total_bytes"],
    )
    gauge(
        "musestudio_media_root_files",
        "Files on disk under media root.",
        summary["storage"]["file_count"],
    )
    return "\n".join(lines) + "\n"

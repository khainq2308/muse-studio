from __future__ import annotations

from typing import Any

from arq import cron
from muse_orchestrator.core.database import AsyncSessionLocal

from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import QueueTaskState
from muse_orchestrator.services.generation_queue import GenerationQueueService
from muse_orchestrator.services.maintenance_service import cleanup_orphan_media


async def process_generation_task_step(ctx: dict[str, Any], task_id: str) -> dict[str, Any]:
    """
    ARQ Worker Job: chạy 1 bước non-blocking và tự hẹn giờ (defer_by) bước poll tiếp theo
    nếu task vẫn đang ở trạng thái QUEUED hoặc RUNNING.
    """
    async with AsyncSessionLocal() as db:
        task = await GenerationQueueService.step_task(db, task_id)
        redis = ctx.get("redis")
        if redis and task.state in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
            await redis.enqueue_job(
                "process_generation_task_step",
                task.id,
                _defer_by=task.next_poll_delay_seconds,
            )
        return {
            "task_id": task.id,
            "state": task.state.value,
            "external_task_id": task.external_task_id,
            "next_poll_delay_seconds": task.next_poll_delay_seconds,
        }


async def cleanup_orphan_media_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """M7 (cron): delete orphan media files older than the retention window."""
    async with AsyncSessionLocal() as db:
        report = await cleanup_orphan_media(
            db,
            settings.media_root,
            settings.orphan_cleanup_retention_hours,
            dry_run=False,
        )
        return {
            "deleted": report["deleted"],
            "freed_bytes": report["freed_bytes"],
            "scanned_orphans": report["scanned_orphans"],
            "errors": report["errors"],
        }


class WorkerSettings:
    functions = [process_generation_task_step, cleanup_orphan_media_job]
    # Daily orphan cleanup at 02:30 local worker time; disable via env.
    cron_jobs = (
        [cron(cleanup_orphan_media_job, hour=2, minute=30)]
        if settings.orphan_cleanup_enabled
        else []
    )
    max_jobs = 20
    job_timeout = 120

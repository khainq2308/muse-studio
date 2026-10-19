from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from muse_orchestrator.core.config import settings
from muse_orchestrator.core.database import AsyncSessionLocal
from muse_orchestrator.models.domain import GenerationTaskRecord, QueueTaskState
from muse_orchestrator.services.arq_dispatch import enqueue_generation_step
from muse_orchestrator.services.generation_queue import GenerationQueueService
from muse_orchestrator.services.maintenance_service import cleanup_orphan_media


async def process_generation_task_step(ctx: dict[str, Any], task_id: str) -> dict[str, Any]:
    """
    ARQ Worker Job: chạy một bước của task và lên lịch poll tiếp theo theo backoff.
    """
    async with AsyncSessionLocal() as db:
        task = await GenerationQueueService.step_task(db, task_id)
        redis = ctx.get("redis")
        if redis:
            await enqueue_generation_step(redis, task)
        return {
            "task_id": task.id,
            "state": task.state.value,
            "external_task_id": task.external_task_id,
            "next_poll_delay_seconds": task.next_poll_delay_seconds,
        }


async def recover_pending_generation_tasks_job(ctx: dict[str, Any]) -> dict[str, int]:
    """Recover queued/running DB tasks after a worker restart or a failed enqueue."""
    redis = ctx.get("redis")
    if redis is None:
        return {"found": 0, "scheduled": 0}

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(GenerationTaskRecord)
            .where(GenerationTaskRecord.state.in_((QueueTaskState.QUEUED, QueueTaskState.RUNNING)))
            .order_by(GenerationTaskRecord.priority.desc(), GenerationTaskRecord.created_at.asc())
        )
        tasks = rows.scalars().all()
        scheduled = 0
        for task in tasks:
            if await enqueue_generation_step(redis, task):
                scheduled += 1
        return {"found": len(tasks), "scheduled": scheduled}


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
    functions = [
        process_generation_task_step,
        recover_pending_generation_tasks_job,
        cleanup_orphan_media_job,
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    cron_jobs = [
        # Re-enqueue DB tasks once per minute. Stable job IDs prevent duplicate queue entries.
        cron(recover_pending_generation_tasks_job, second=0),
        *(
            [cron(cleanup_orphan_media_job, hour=2, minute=30)]
            if settings.orphan_cleanup_enabled
            else []
        ),
    ]
    max_jobs = 20
    job_timeout = 120

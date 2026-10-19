from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from muse_orchestrator.models.domain import GenerationTaskRecord, QueueTaskState
from muse_orchestrator.services.arq_dispatch import enqueue_generation_step

logger = logging.getLogger(__name__)


async def schedule_generation_task(request: Request, task: GenerationTaskRecord) -> None:
    """Publish an already-persisted task; expose a safe 503 if Redis cannot schedule it."""
    if task.state not in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
        return

    redis = getattr(request.app.state, "arq_pool", None)
    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "task_id": task.id,
                "message": "Task is saved, but the ARQ scheduler is not initialized. Retry the same idempotent request.",
            },
        )

    try:
        await enqueue_generation_step(
            redis,
            task,
            immediate=(task.state == QueueTaskState.QUEUED and not task.external_task_id),
        )
    except Exception as exc:
        logger.exception("Could not schedule generation task %s in ARQ", task.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "task_id": task.id,
                "message": "Task is saved, but Redis scheduling failed. Retry the same idempotent request.",
            },
        ) from exc

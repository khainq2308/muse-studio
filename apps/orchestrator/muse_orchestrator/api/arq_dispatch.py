from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from muse_orchestrator.models.domain import (
    SCHEDULABLE_GENERATION_TASK_STATES,
    GenerationTaskRecord,
    QueueTaskState,
)
from muse_orchestrator.services.arq_dispatch import enqueue_export_job, enqueue_generation_step

logger = logging.getLogger(__name__)


async def schedule_generation_task(request: Request, task: GenerationTaskRecord) -> None:
    """Publish an already-persisted task; expose a safe 503 if Redis cannot schedule it."""
    if task.state not in SCHEDULABLE_GENERATION_TASK_STATES:
        return

    redis = getattr(request.app.state, "arq_pool", None)
    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "task_id": task.id,
                "message": "Task is saved, but the ARQ scheduler is not initialized. Background recovery will retry after Redis is reachable; do not create a new generation just to reschedule.",
            },
        )

    try:
        await enqueue_generation_step(
            redis,
            task,
            immediate=(task.state == QueueTaskState.QUEUED and not task.external_task_id),
        )
    except Exception as exc:
        logger.error(
            "Could not schedule generation task %s in ARQ (error type %s)",
            task.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "task_id": task.id,
                "message": "Task is saved, but Redis scheduling failed. Background recovery will retry after Redis is reachable; do not create a new generation just to reschedule.",
            },
        ) from exc


async def schedule_generation_tasks(request: Request, tasks: list[GenerationTaskRecord]) -> None:
    """Publish several persisted tasks; try all of them before reporting one safe 503.

    Call only after every database change of the request is committed, so a Redis outage
    never leaves the work half done: unscheduled tasks stay persisted and the worker's
    recovery cron re-enqueues them.
    """
    unscheduled: list[str] = []
    for task in tasks:
        try:
            await schedule_generation_task(request, task)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_503_SERVICE_UNAVAILABLE:
                raise
            unscheduled.append(task.id)
    if unscheduled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "task_ids": unscheduled,
                "message": "Tasks are saved, but Redis scheduling failed. Background recovery will retry after Redis is reachable; do not repeat the request just to reschedule.",
            },
        )


async def schedule_export_job(request: Request, export_id: str) -> None:
    """Publish an already-persisted QUEUED export job; expose a safe 503 if Redis cannot schedule it.

    The job row stays QUEUED on failure and the worker's recovery cron re-enqueues it
    once Redis is reachable again, so a 503 here never loses the export.
    """
    redis = getattr(request.app.state, "arq_pool", None)
    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "export_id": export_id,
                "message": "Export is saved as QUEUED, but the ARQ scheduler is not initialized. Background recovery will retry after Redis is reachable; do not create a new export just to reschedule.",
            },
        )

    try:
        await enqueue_export_job(redis, export_id)
    except Exception as exc:
        logger.error(
            "Could not schedule export job %s in ARQ (error type %s)",
            export_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "export_id": export_id,
                "message": "Export is saved as QUEUED, but Redis scheduling failed. Background recovery will retry after Redis is reachable; do not create a new export just to reschedule.",
            },
        ) from exc

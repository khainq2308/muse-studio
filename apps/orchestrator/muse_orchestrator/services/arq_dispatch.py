from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from muse_orchestrator.models.domain import (
    SCHEDULABLE_GENERATION_TASK_STATES,
    GenerationTaskRecord,
)


def generation_step_token(task: GenerationTaskRecord) -> str:
    """Schedule token of the task's current persisted version.

    Every committed step changes ``updated_at``. A step job carries the token of the row
    version it was scheduled for; when the row has moved on (another job, a manual
    tick/step or a resume stepped/scheduled it) the job is stale and exits without
    stepping or scheduling. That keeps exactly one live step chain per task even when
    HTTP tick/step and the recovery cron also schedule jobs.

    Normalised to integer UTC microseconds so tz-aware Python values and naive SQLite
    values of the same instant produce the same token.
    """
    stamp = task.updated_at or task.created_at
    if stamp is None:
        return "unknown"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    delta = stamp.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return str((delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds)


def generation_step_job_id(task: GenerationTaskRecord) -> str:
    """Stable ID for one persisted queue state, allowing idempotent re-scheduling.

    Every step that wants a follow-up job must change the row (``updated_at``), otherwise
    the follow-up would reuse the ID of the job that is still running and ARQ would drop
    it. The worker registers the step function with ``keep_result=0`` so a finished job's
    ID is released immediately for recovery re-enqueueing.
    """
    return (
        f"generation-step:{task.id}:{task.state.value}:{task.poll_count}:"
        f"{generation_step_token(task)}"
    )


async def enqueue_generation_step(
    redis: Any,
    task: GenerationTaskRecord,
    *,
    immediate: bool = False,
) -> bool:
    """Schedule one active task; duplicate calls for the same DB state are deduplicated.

    API-created tasks can opt into an immediate first dispatch. Worker/recovery callers
    preserve the task's persisted backoff or lease wait to avoid a tight retry loop.
    The job carries ``schedule_token`` so a superseded job exits without forking a chain.
    """
    if task.state not in SCHEDULABLE_GENERATION_TASK_STATES:
        return False

    delay = 0.0 if immediate else max(0.0, float(task.next_poll_delay_seconds))
    job = await redis.enqueue_job(
        "process_generation_task_step",
        task.id,
        schedule_token=generation_step_token(task),
        _job_id=generation_step_job_id(task),
        _defer_by=delay,
    )
    return job is not None


def export_job_id(export_id: str) -> str:
    """Stable ARQ job ID for one export; re-enqueueing the same export dedupes."""
    return f"export-job:{export_id}"


async def enqueue_export_job(redis: Any, export_id: str) -> bool:
    """Schedule the FFmpeg export worker for a persisted QUEUED export job."""
    job = await redis.enqueue_job(
        "process_export_job",
        export_id,
        _job_id=export_job_id(export_id),
    )
    return job is not None

from __future__ import annotations

from typing import Any

from muse_orchestrator.models.domain import GenerationTaskRecord, QueueTaskState


def generation_step_job_id(task: GenerationTaskRecord) -> str:
    """Stable ID for one persisted queue state, allowing idempotent re-scheduling.

    Every step that wants a follow-up job must change the row (``updated_at``), otherwise
    the follow-up would reuse the ID of the job that is still running and ARQ would drop
    it. The worker registers the step function with ``keep_result=0`` so a finished job's
    ID is released immediately for recovery re-enqueueing.
    """
    updated_at = task.updated_at or task.created_at
    updated_token = updated_at.isoformat() if updated_at else "unknown"
    return f"generation-step:{task.id}:{task.state.value}:{task.poll_count}:{updated_token}"


async def enqueue_generation_step(
    redis: Any,
    task: GenerationTaskRecord,
    *,
    immediate: bool = False,
) -> bool:
    """Schedule one active task; duplicate calls for the same DB state are deduplicated.

    API-created tasks can opt into an immediate first dispatch. Worker/recovery callers
    preserve the task's persisted backoff or lease wait to avoid a tight retry loop.
    """
    if task.state not in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
        return False

    delay = 0.0 if immediate else max(0.0, float(task.next_poll_delay_seconds))
    job = await redis.enqueue_job(
        "process_generation_task_step",
        task.id,
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

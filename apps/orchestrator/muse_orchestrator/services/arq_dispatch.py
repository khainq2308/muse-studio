from __future__ import annotations

from typing import Any

from muse_orchestrator.models.domain import GenerationTaskRecord, QueueTaskState


def generation_step_job_id(task: GenerationTaskRecord) -> str:
    """Stable ID for one persisted queue state, allowing idempotent re-scheduling."""
    updated_at = task.updated_at or task.created_at
    updated_token = updated_at.isoformat() if updated_at else "unknown"
    return f"generation-step:{task.id}:{task.state.value}:{task.poll_count}:{updated_token}"


async def enqueue_generation_step(redis: Any, task: GenerationTaskRecord) -> bool:
    """Schedule one active task; duplicate calls for the same DB state are deduplicated."""
    if task.state not in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
        return False

    delay = 0 if task.state == QueueTaskState.QUEUED else max(0.0, float(task.next_poll_delay_seconds))
    job = await redis.enqueue_job(
        "process_generation_task_step",
        task.id,
        _job_id=generation_step_job_id(task),
        _defer_by=delay,
    )
    return job is not None

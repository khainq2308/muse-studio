from __future__ import annotations
from typing import Any
from muse_orchestrator.core.database import AsyncSessionLocal
from muse_orchestrator.models.domain import QueueTaskState
from muse_orchestrator.services.generation_queue import GenerationQueueService


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


class WorkerSettings:
    functions = [process_generation_task_step]
    max_jobs = 20
    job_timeout = 120

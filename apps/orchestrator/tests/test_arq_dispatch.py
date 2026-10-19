from __future__ import annotations

from datetime import datetime, timezone

import pytest
from arq.connections import RedisSettings

from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import GenerationTaskRecord, QueueTaskState
from muse_orchestrator.services.arq_dispatch import enqueue_generation_step
from muse_orchestrator.workers.queue_worker import WorkerSettings


class _FakeRedis:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []
        self.job_ids: set[str] = set()

    async def enqueue_job(self, function: str, *args, **kwargs):
        job_id = kwargs.get("_job_id")
        if job_id and job_id in self.job_ids:
            return None
        if job_id:
            self.job_ids.add(job_id)
        self.jobs.append((function, args, kwargs))
        return object()


def _task(
    *,
    state: QueueTaskState = QueueTaskState.QUEUED,
    poll_count: int = 0,
    delay: float = 7.0,
) -> GenerationTaskRecord:
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    return GenerationTaskRecord(
        id="task_arq_test",
        project_id="project_arq_test",
        shot_id="shot_arq_test",
        connection_id="connection_arq_test",
        task_type="video",
        model_id="test-model",
        idempotency_key="idem-arq-test",
        state=state,
        poll_count=poll_count,
        next_poll_delay_seconds=delay,
        created_at=now,
        updated_at=now,
    )


def test_worker_settings_has_explicit_redis_settings() -> None:
    assert isinstance(WorkerSettings.redis_settings, RedisSettings)
    assert WorkerSettings.redis_settings.host
    assert WorkerSettings.redis_settings.port > 0


@pytest.mark.asyncio
async def test_newly_queued_task_can_be_scheduled_immediately_and_deduplicated() -> None:
    redis = _FakeRedis()
    task = _task()

    assert await enqueue_generation_step(redis, task, immediate=True) is True
    assert await enqueue_generation_step(redis, task, immediate=True) is False

    assert len(redis.jobs) == 1
    function, args, kwargs = redis.jobs[0]
    assert function == "process_generation_task_step"
    assert args == (task.id,)
    assert kwargs["_defer_by"] == 0
    assert kwargs["_job_id"].startswith(f"generation-step:{task.id}:queued:0:")


@pytest.mark.asyncio
async def test_worker_rescheduling_preserves_backoff_for_running_tasks() -> None:
    redis = _FakeRedis()
    task = _task(state=QueueTaskState.RUNNING, poll_count=3, delay=12.5)

    assert await enqueue_generation_step(redis, task) is True

    assert redis.jobs[0][2]["_defer_by"] == 12.5
    assert ":running:3:" in redis.jobs[0][2]["_job_id"]


@pytest.mark.asyncio
async def test_terminal_task_is_not_queued() -> None:
    redis = _FakeRedis()
    task = _task(state=QueueTaskState.SUCCEEDED)

    assert await enqueue_generation_step(redis, task, immediate=True) is False
    assert redis.jobs == []

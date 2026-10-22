"""TEST-006: ``queue_dispatch_unavailable`` (503) when ARQ/Redis cannot schedule work.

The API persists the task/export first and only then publishes it to ARQ. When Redis is
down (or the pool was never initialised) the client gets a 503 envelope, the row stays
QUEUED, and the worker's recovery cron schedules it once Redis is reachable again.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="muse_dispatch_unavailable_test_"))

from muse_orchestrator.core.config import settings  # noqa: E402 -- env must be set before import
from muse_orchestrator.core.database import get_db  # noqa: E402
from muse_orchestrator.main import app  # noqa: E402
from muse_orchestrator.models.domain import (  # noqa: E402
    Base,
    BatchRenderJobRecord,
    ExportJobRecord,
    ExportJobState,
    GenerationTaskRecord,
    QueueTaskState,
    ShotGenerationState,
    ShotRecord,
)
from muse_orchestrator.services.arq_dispatch import export_job_id, generation_step_job_id  # noqa: E402
from muse_orchestrator.workers import queue_worker  # noqa: E402

# A Redis DSN with a password inside the exception text must never reach the response.
REDIS_PASSWORD = "redis-pw-should-not-leak"
MP4_PLACEHOLDER = b"\x00\x00\x00\x18ftypmp42" + b"placeholder, not decodable"
SCRIPT = (
    "SCENE 1: Roof\n"
    "SHOT 1: Hero looks out\n- Action: Wind\n"
    "SHOT 2: Hero jumps\n- Action: Leap\n"
)


class _RedisDownPool:
    """ARQ pool whose Redis connection is gone."""

    def __init__(self) -> None:
        self.attempts = 0

    async def enqueue_job(self, function: str, *args, **kwargs):
        self.attempts += 1
        raise ConnectionError(f"Error 111 connecting to redis://:{REDIS_PASSWORD}@redis:6379/0")


class _WorkingPool:
    """In-memory ARQ pool with job-ID de-duplication like Redis."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []
        self._ids: set[str] = set()

    async def enqueue_job(self, function: str, *args, **kwargs):
        job_id = kwargs.get("_job_id")
        if job_id in self._ids:
            return None
        if job_id:
            self._ids.add(job_id)
        self.jobs.append((function, args, kwargs))
        return object()


@dataclass
class Env:
    client: AsyncClient
    maker: async_sessionmaker
    project_id: str
    connection_id: str
    shot_ids: list[str]


_UNSET = object()


@pytest.fixture()
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Same session options as core/database.AsyncSessionLocal.
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setattr(settings, "media_root", str(media_root))
    monkeypatch.setattr(queue_worker, "AsyncSessionLocal", maker)

    previous_pool = getattr(app.state, "arq_pool", _UNSET)
    app.state.arq_pool = _WorkingPool()
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            project_id = (await client.post("/api/v1/projects", json={"name": "redis-down"})).json()["id"]
            connection_id = (
                await client.post(
                    "/api/v1/connections",
                    json={
                        "name": "mock-redis-down",
                        "provider_type": "mock",
                        "base_url": "http://localhost:8080",
                        "max_concurrency": 2,
                    },
                )
            ).json()["id"]
            await client.post(
                f"/api/v1/projects/{project_id}/scripts/import",
                json={"raw_content": SCRIPT, "auto_apply_proposal": True},
            )
            shots = (await client.get(f"/api/v1/projects/{project_id}/shots")).json()
            yield Env(client, maker, project_id, connection_id, [shot["id"] for shot in shots])
    finally:
        app.dependency_overrides.clear()
        if previous_pool is _UNSET:
            if hasattr(app.state, "arq_pool"):
                delattr(app.state, "arq_pool")
        else:
            app.state.arq_pool = previous_pool


def assert_dispatch_unavailable(response, id_field: str) -> str:
    assert response.status_code == 503, response.text
    body = response.json()
    assert body["category"] == "service_unavailable"
    assert body["retryable"] is True
    assert body["detail"]["code"] == "queue_dispatch_unavailable"
    assert REDIS_PASSWORD not in response.text
    assert "redis://" not in response.text
    return body["detail"][id_field]


async def post_task(env: Env, idempotency_key: str = "redis-down-1"):
    return await env.client.post(
        "/api/v1/queue/tasks",
        json={
            "shot_id": env.shot_ids[0],
            "connection_id": env.connection_id,
            "task_type": "video",
            "idempotency_key": idempotency_key,
        },
    )


async def tasks_in_db(env: Env) -> list[GenerationTaskRecord]:
    async with env.maker() as db:
        return list((await db.execute(select(GenerationTaskRecord))).scalars().all())


# --- generation tasks --------------------------------------------------------------------------


async def test_enqueue_task_redis_down_returns_503_and_persists_queued_task(env: Env) -> None:
    down = _RedisDownPool()
    app.state.arq_pool = down
    response = await post_task(env)
    task_id = assert_dispatch_unavailable(response, "task_id")
    assert down.attempts == 1
    tasks = await tasks_in_db(env)
    assert [task.id for task in tasks] == [task_id]
    assert tasks[0].state == QueueTaskState.QUEUED
    assert tasks[0].external_task_id is None
    async with env.maker() as db:
        shot = await db.get(ShotRecord, env.shot_ids[0])
        assert shot.generation_state == ShotGenerationState.QUEUED


async def test_enqueue_task_without_arq_pool_returns_503_and_persists_queued_task(env: Env) -> None:
    delattr(app.state, "arq_pool")
    response = await post_task(env)
    task_id = assert_dispatch_unavailable(response, "task_id")
    tasks = await tasks_in_db(env)
    assert [(task.id, task.state) for task in tasks] == [(task_id, QueueTaskState.QUEUED)]


async def test_recovery_schedules_task_saved_during_redis_outage_exactly_once(env: Env) -> None:
    app.state.arq_pool = _RedisDownPool()
    task_id = assert_dispatch_unavailable(await post_task(env), "task_id")

    redis = _WorkingPool()
    first = await queue_worker.recover_pending_generation_tasks_job({"redis": redis})
    assert first == {"found": 1, "scheduled": 1}
    [(function, args, kwargs)] = redis.jobs
    assert function == "process_generation_task_step"
    assert args == (task_id,)
    async with env.maker() as db:
        task = await db.get(GenerationTaskRecord, task_id)
        assert kwargs["_job_id"] == generation_step_job_id(task)
    # A second cron run for the same DB state is de-duplicated by the job ID.
    second = await queue_worker.recover_pending_generation_tasks_job({"redis": redis})
    assert second == {"found": 1, "scheduled": 0}
    assert len(redis.jobs) == 1


async def test_retry_after_503_with_same_idempotency_key_reuses_saved_task(env: Env) -> None:
    app.state.arq_pool = _RedisDownPool()
    task_id = assert_dispatch_unavailable(await post_task(env), "task_id")

    redis = _WorkingPool()
    app.state.arq_pool = redis
    retry = await post_task(env)
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == task_id
    assert retry.json()["created_new"] is False
    assert len(await tasks_in_db(env)) == 1
    assert [job[1] for job in redis.jobs] == [(task_id,)]


async def test_recovery_without_redis_schedules_nothing(env: Env) -> None:
    app.state.arq_pool = _RedisDownPool()
    assert_dispatch_unavailable(await post_task(env), "task_id")
    assert await queue_worker.recover_pending_generation_tasks_job({}) == {"found": 0, "scheduled": 0}
    tasks = await tasks_in_db(env)
    assert [task.state for task in tasks] == [QueueTaskState.QUEUED]


async def test_batch_launch_redis_down_returns_503_and_recovery_schedules_every_task(env: Env) -> None:
    app.state.arq_pool = _RedisDownPool()
    response = await env.client.post(
        f"/api/v1/projects/{env.project_id}/batch/launch", json={"connection_id": env.connection_id}
    )
    assert_dispatch_unavailable(response, "batch_id")
    tasks = await tasks_in_db(env)
    assert len(tasks) == len(env.shot_ids) == 2
    assert {task.state for task in tasks} == {QueueTaskState.QUEUED}
    # Every task is attempted and reported, with the persisted batch id.
    assert sorted(response.json()["detail"]["task_ids"]) == sorted(t.id for t in tasks)
    async with env.maker() as db:
        batches = list((await db.execute(select(BatchRenderJobRecord))).scalars().all())
        assert len(batches) == 1 and sorted(batches[0].task_ids_json) == sorted(t.id for t in tasks)
        assert response.json()["detail"]["batch_id"] == batches[0].id

    redis = _WorkingPool()
    result = await queue_worker.recover_pending_generation_tasks_job({"redis": redis})
    assert result == {"found": 2, "scheduled": 2}
    assert sorted(job[1][0] for job in redis.jobs) == sorted(task.id for task in tasks)


# --- exports -----------------------------------------------------------------------------------


async def prepare_timeline(env: Env) -> None:
    upload = await env.client.post(
        "/api/v1/assets/upload",
        data={"project_id": env.project_id, "asset_type": "video"},
        files={"file": ("clip.mp4", MP4_PLACEHOLDER, "video/mp4")},
    )
    assert upload.status_code == 201, upload.text
    assembled = await env.client.post(
        f"/api/v1/projects/{env.project_id}/timeline/assemble",
        json={
            "name": "Cut",
            "custom_clips": [
                {"asset_id": upload.json()["id"], "order_index": 1, "source_duration_seconds": 2.0}
            ],
        },
    )
    assert assembled.status_code == 201, assembled.text


async def exports_in_db(env: Env) -> list[ExportJobRecord]:
    async with env.maker() as db:
        return list((await db.execute(select(ExportJobRecord))).scalars().all())


async def test_export_launch_redis_down_returns_503_and_persists_queued_job(env: Env) -> None:
    await prepare_timeline(env)
    app.state.arq_pool = _RedisDownPool()
    response = await env.client.post(f"/api/v1/projects/{env.project_id}/timeline/exports", json={})
    export_id = assert_dispatch_unavailable(response, "export_id")
    jobs = await exports_in_db(env)
    assert [(job.id, job.state) for job in jobs] == [(export_id, ExportJobState.QUEUED)]
    assert jobs[0].output_asset_id is None


async def test_export_launch_without_arq_pool_returns_503_and_persists_queued_job(env: Env) -> None:
    await prepare_timeline(env)
    delattr(app.state, "arq_pool")
    response = await env.client.post(f"/api/v1/projects/{env.project_id}/timeline/exports", json={})
    export_id = assert_dispatch_unavailable(response, "export_id")
    jobs = await exports_in_db(env)
    assert [(job.id, job.state) for job in jobs] == [(export_id, ExportJobState.QUEUED)]


async def test_export_recovery_schedules_job_saved_during_redis_outage(env: Env) -> None:
    await prepare_timeline(env)
    app.state.arq_pool = _RedisDownPool()
    response = await env.client.post(f"/api/v1/projects/{env.project_id}/timeline/exports", json={})
    export_id = assert_dispatch_unavailable(response, "export_id")

    redis = _WorkingPool()
    result = await queue_worker.recover_pending_export_jobs_job({"redis": redis})
    assert result == {"found": 1, "scheduled": 1, "stale_failed": 0}
    assert redis.jobs == [("process_export_job", (export_id,), {"_job_id": export_job_id(export_id)})]
    again = await queue_worker.recover_pending_export_jobs_job({"redis": redis})
    assert again["scheduled"] == 0
    assert [job.state for job in await exports_in_db(env)] == [ExportJobState.QUEUED]

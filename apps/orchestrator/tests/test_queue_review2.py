"""Regression tests for the second deep review of the generation queue (51ec668).

R1-R7 started as reproductions of confirmed findings; they now assert the corrected
behaviour. Additional tests cover the dispatch snapshot, frame-chain re-selection,
provider response validation, recovery bounds and export persistence.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_review2_test_")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", _TEMP_MEDIA_DIR)

from muse_orchestrator.core.database import get_db  # noqa: E402 -- env must be set before import
from muse_orchestrator.main import app  # noqa: E402
from muse_orchestrator.models.domain import (  # noqa: E402
    Base,
    ExportJobRecord,
    ExportJobState,
    GenerationDispatchLeaseRecord,
    GenerationTaskRecord,
    Project,
    QueueTaskState,
    ShotGenerationState,
    ShotRecord,
    ShotTakeRecord,
    TimelineSequenceRecord,
)
from muse_orchestrator.providers.base import (  # noqa: E402
    ErrorCategory,
    ProviderFailure,
    ProviderTask,
    ProviderTaskStatus,
    VideoGenerationRequest,
)
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter  # noqa: E402
from muse_orchestrator.services import generation_queue  # noqa: E402
from muse_orchestrator.services.generation_guards import (  # noqa: E402
    request_snapshot_of,
    resolve_conditioning_frame,
)
from muse_orchestrator.services.generation_queue import GenerationQueueService  # noqa: E402
from muse_orchestrator.services.timeline_service import TimelineValidationService  # noqa: E402
from muse_orchestrator.infrastructure.repositories import StudioRepository  # noqa: E402
from muse_orchestrator.workers import queue_worker  # noqa: E402

STATE: dict[str, object] = {}


class _FakeArqPool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []
        self._ids: set[str] = set()

    async def enqueue_job(self, function: str, *args, **kwargs):
        job_id = kwargs.get("_job_id")
        if job_id and job_id in self._ids:
            return None
        if job_id:
            self._ids.add(job_id)
        self.jobs.append((function, args, kwargs))
        return object()

    async def aclose(self) -> None:
        return None


@pytest.fixture()
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Same session options as core.database.AsyncSessionLocal.
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    STATE["maker"] = maker

    async def override_get_db():
        async with maker() as session:
            yield session

    previous_session_local = queue_worker.AsyncSessionLocal
    queue_worker.AsyncSessionLocal = maker
    previous_pool = getattr(app.state, "arq_pool", None)
    app.state.arq_pool = _FakeArqPool()
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        queue_worker.AsyncSessionLocal = previous_session_local
        if previous_pool is None:
            if hasattr(app.state, "arq_pool"):
                delattr(app.state, "arq_pool")
        else:
            app.state.arq_pool = previous_pool
        await engine.dispose()


SCRIPT = """
SCENE 1: Chase
SHOT 1: Hero leaps
- Action: Leaps across neon gap
SHOT 2: Hero lands
- Action: Rolls onto concrete roof
"""


async def _setup(client: AsyncClient):
    pid = (await client.post("/api/v1/projects", json={"name": "review2"})).json()["id"]
    cid = (await client.post("/api/v1/connections", json={
        "name": "mock-review2", "provider_type": "mock", "base_url": "http://localhost:8080",
        "max_concurrency": 2})).json()["id"]
    await client.post(f"/api/v1/projects/{pid}/scripts/import",
                      json={"raw_content": SCRIPT, "auto_apply_proposal": True})
    shots = (await client.get(f"/api/v1/projects/{pid}/shots")).json()
    return pid, cid, shots[0], shots[1]


async def _shots(client: AsyncClient, pid: str) -> dict[str, dict]:
    return {s["id"]: s for s in (await client.get(f"/api/v1/projects/{pid}/shots")).json()}


async def _ticks(client: AsyncClient, pid: str, n: int = 8) -> None:
    for _ in range(n):
        await client.post(f"/api/v1/projects/{pid}/batch/tick")


async def _tasks_for_shot(shot_id: str) -> list[GenerationTaskRecord]:
    async with STATE["maker"]() as db:
        rows = await db.execute(
            select(GenerationTaskRecord)
            .where(GenerationTaskRecord.shot_id == shot_id)
            .order_by(GenerationTaskRecord.created_at.asc())
        )
        return list(rows.scalars().all())


def _active(tasks):
    return [t for t in tasks if t.state in (QueueTaskState.QUEUED, QueueTaskState.RUNNING, QueueTaskState.PAUSED)]


async def _step(task_id: str) -> GenerationTaskRecord:
    async with STATE["maker"]() as db:
        return await GenerationQueueService.step_task(db, task_id)


async def _drive_until_dispatched(task_id: str) -> GenerationTaskRecord:
    task = await _step(task_id)
    for _ in range(3):
        if task.external_task_id or task.state not in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
            break
        task = await _step(task_id)
    return task


async def _drive_until_done(task_id: str) -> GenerationTaskRecord:
    task = await _step(task_id)
    for _ in range(8):
        if task.state not in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
            break
        task = await _step(task_id)
    return task


async def _chain_and_render(client: AsyncClient):
    pid, cid, s1, s2 = await _setup(client)
    await client.patch(f"/api/v1/projects/{pid}/shots/{s2['id']}", json={"continuity_mode": "frame_chain"})
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid})
    await _ticks(client, pid)
    st = await _shots(client, pid)
    assert st[s1["id"]]["generation_state"] == "succeeded"
    assert st[s2["id"]]["generation_state"] == "succeeded"
    return pid, cid, s1, s2


# ======================= R1-R7: confirmed findings =======================


@pytest.mark.asyncio
async def test_R1_prompt_change_never_creates_a_second_active_task(client: AsyncClient):
    pid, cid, s1, _ = await _setup(client)
    r1 = await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})
    assert r1.status_code == 201, r1.text
    await client.patch(f"/api/v1/projects/{pid}/shots/{s1['id']}", json={"action": "Leaps across a wider neon gap"})
    r2 = await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})
    assert r2.status_code == 201, r2.text
    assert r2.json()["id"] == r1.json()["id"] and r2.json()["created_new"] is False
    await client.patch(f"/api/v1/projects/{pid}/shots/{s1['id']}", json={"action": "Leaps again"})
    launch = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                                json={"connection_id": cid, "shot_ids": [s1["id"]]})).json()
    assert launch["enqueued_task_ids"] == [r1.json()["id"]]
    forced = await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "force_retry": True, "model_id": "other-model"})
    assert forced.json()["id"] == r1.json()["id"]
    assert len(_active(await _tasks_for_shot(s1["id"]))) == 1


@pytest.mark.asyncio
async def test_R1_lost_insert_race_returns_the_winning_active_task(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _ = await _setup(client)
    winner = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "idempotency_key": "winner"})).json()

    original = GenerationQueueService.find_active_task
    calls = {"n": 0}

    async def _blind_first_lookup(db, shot_id, task_type):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # simulate a concurrent request that has not seen the winner yet
        return await original(db, shot_id, task_type)

    monkeypatch.setattr(GenerationQueueService, "find_active_task", staticmethod(_blind_first_lookup))
    loser = await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "idempotency_key": "loser"})
    assert loser.status_code == 201, loser.text
    assert loser.json()["id"] == winner["id"]
    assert loser.json()["created_new"] is False
    assert len(await _tasks_for_shot(s1["id"])) == 1


@pytest.mark.asyncio
async def test_R2_dispatch_rechecks_lint_blockers(client: AsyncClient):
    pid, cid, s1, _ = await _setup(client)
    r = await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})
    assert r.status_code == 201
    patched = (await client.patch(
        f"/api/v1/projects/{pid}/shots/{s1['id']}",
        json={"camera_motion": "static", "action": "Quick dolly toward the hero"},
    )).json()
    assert [i["code"] for i in patched["lint_report"] if i["severity"] == "blocker"]
    t = await _drive_until_dispatched(r.json()["id"])
    assert t.external_task_id is None
    assert t.state == QueueTaskState.FAILED
    assert t.error_category == "lint_blocker"
    assert t.retryable is False
    assert (await _shots(client, pid))[s1["id"]]["generation_state"] == "blocked_reference"


@pytest.mark.asyncio
async def test_R2_enqueue_lint_blocker_does_not_clobber_shot_with_active_task(client: AsyncClient):
    pid, cid, s1, _ = await _setup(client)
    await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, s1["id"])
        shot.lint_report_json = [{"code": "CAMERA_MOTION_CONFLICT", "severity": "blocker", "message": "conflict", "field": "camera_motion"}]
        shot.generation_state = ShotGenerationState.QUEUED
        await db.commit()
    r = await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})
    assert r.status_code == 422
    assert (await _shots(client, pid))[s1["id"]]["generation_state"] == "queued"


@pytest.mark.asyncio
async def test_R3_frame_chain_head_shot_is_cached_by_only_changed(client: AsyncClient):
    pid, cid, s1, _s2 = await _setup(client)
    await client.patch(f"/api/v1/projects/{pid}/shots/{s1['id']}", json={"continuity_mode": "frame_chain"})
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid, "shot_ids": [s1["id"]]})
    await _ticks(client, pid)
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, s1["id"])
        assert shot.generation_state == ShotGenerationState.SUCCEEDED
        # C1: the shot's own extracted opening frame lives on the take, not on the shot.
        assert shot.first_frame_asset_id is None
        take = await db.get(ShotTakeRecord, shot.selected_take_id)
        assert take.first_frame_asset_id is not None
    again = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                               json={"connection_id": cid, "shot_ids": [s1["id"]], "only_changed": True})).json()
    assert again["enqueued_count"] == 0
    assert again["skipped_shot_ids"] == [s1["id"]]


@pytest.mark.asyncio
async def test_R3_legacy_own_frame_is_not_used_as_conditioning(client: AsyncClient):
    pid, cid, s1, _s2 = await _setup(client)
    await client.patch(f"/api/v1/projects/{pid}/shots/{s1['id']}", json={"continuity_mode": "frame_chain"})
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid, "shot_ids": [s1["id"]]})
    await _ticks(client, pid)
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, s1["id"])
        take = await db.get(ShotTakeRecord, shot.selected_take_id)
        shot.first_frame_asset_id = take.first_frame_asset_id  # legacy row written by old code
        await db.commit()
        resolved = await resolve_conditioning_frame(db, shot)
    assert resolved.asset_id is None and resolved.source == "none"
    again = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                               json={"connection_id": cid, "shot_ids": [s1["id"]], "only_changed": True})).json()
    assert again["skipped_shot_ids"] == [s1["id"]]


@pytest.mark.asyncio
async def test_R4_switch_to_frame_chain_conditions_on_upstream_last_frame(client: AsyncClient):
    pid, cid, s1, s2 = await _setup(client)
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid})
    await _ticks(client, pid)
    async with STATE["maker"]() as db:
        upstream_last = (await db.get(ShotRecord, s1["id"])).last_frame_asset_id
        own_take = await db.get(ShotTakeRecord, (await db.get(ShotRecord, s2["id"])).selected_take_id)
    assert upstream_last
    await client.patch(f"/api/v1/projects/{pid}/shots/{s2['id']}", json={"continuity_mode": "frame_chain"})
    r = await client.post("/api/v1/queue/tasks", json={"shot_id": s2["id"], "connection_id": cid, "force_retry": True})
    assert r.status_code == 201, r.text
    t = await _drive_until_dispatched(r.json()["id"])
    cond = generation_queue.conditioning_first_frame_of(t)
    assert cond == upstream_last
    assert cond != own_take.first_frame_asset_id


@pytest.mark.asyncio
async def test_R5_manual_tick_does_not_fork_a_second_job_chain(client: AsyncClient):
    pid, cid, s1, _ = await _setup(client)
    pool = app.state.arq_pool
    r = await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})
    tid = r.json()["id"]
    first_job = [j for j in pool.jobs if j[1] == (tid,)][-1]
    pool.jobs.clear()
    await queue_worker.process_generation_task_step(
        {"redis": pool}, tid, schedule_token=first_job[2]["schedule_token"]
    )
    await client.post(f"/api/v1/projects/{pid}/batch/tick")
    pending = [j for j in pool.jobs if j[1] == (tid,)]
    assert len(pending) == 2  # worker follow-up + manual tick follow-up
    async with STATE["maker"]() as db:
        before = (await db.get(GenerationTaskRecord, tid)).poll_count
    pool.jobs.clear()
    results = [
        await queue_worker.process_generation_task_step({"redis": pool}, tid, schedule_token=j[2]["schedule_token"])
        for j in pending
    ]
    assert [res["state"] for res in results].count("stale_job_skipped") == 1
    async with STATE["maker"]() as db:
        after = await db.get(GenerationTaskRecord, tid)
    assert after.poll_count == before + 1
    assert len([j for j in pool.jobs if j[1] == (tid,)]) <= 1


@pytest.mark.asyncio
async def test_R6_batch_does_not_report_failed_task_as_enqueued(client: AsyncClient):
    pid, cid, s1, _ = await _setup(client)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    async with STATE["maker"]() as db:
        t = await db.get(GenerationTaskRecord, tid)
        t.state = QueueTaskState.FAILED
        s = await db.get(ShotRecord, s1["id"])
        s.generation_state = ShotGenerationState.FAILED
        await db.commit()
    launch = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                                json={"connection_id": cid, "shot_ids": [s1["id"]]})).json()
    assert launch["enqueued_task_ids"] == []
    assert launch["enqueued_count"] == 0
    assert launch["skipped_shot_ids"] == [s1["id"]]
    assert _active(await _tasks_for_shot(s1["id"])) == []


@pytest.mark.asyncio
async def test_R7_cancel_after_send_is_not_redispatched(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _ = await _setup(client)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    calls = []

    class _AcceptThenCancel:
        async def generate_video(self, request):
            calls.append(request.idempotency_key)
            if len(calls) == 1:
                raise asyncio.CancelledError()
            return ProviderTask(external_task_id="ext_2", status="queued")

    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: _AcceptThenCancel())
    async with STATE["maker"]() as db:
        with pytest.raises(asyncio.CancelledError):
            await GenerationQueueService.step_task(db, tid)
    t = await _step(tid)
    assert len(calls) == 1
    assert t.state == QueueTaskState.FAILED
    assert t.error_category == "dispatch_outcome_unknown"
    assert t.retryable is False
    async with STATE["maker"]() as db:
        assert await db.get(GenerationDispatchLeaseRecord, tid) is None


@pytest.mark.asyncio
async def test_R7_outcome_unknown_failure_from_adapter_is_terminal(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _ = await _setup(client)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    calls = []

    class _ReadTimeoutAfterSend:
        async def generate_video(self, request):
            calls.append(1)
            raise ProviderFailure(ErrorCategory.DISPATCH_OUTCOME_UNKNOWN, "read timeout", None, False)

    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: _ReadTimeoutAfterSend())
    t = await _step(tid)
    t = await _step(tid)
    assert len(calls) == 1
    assert t.state == QueueTaskState.FAILED and t.error_category == "dispatch_outcome_unknown"


# ======================= snapshot, frame chain, download =======================


@pytest.mark.asyncio
async def test_dispatch_uses_enqueue_snapshot_not_live_shot(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _ = await _setup(client)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    async with STATE["maker"]() as db:
        task = await db.get(GenerationTaskRecord, tid)
        snapshot = request_snapshot_of(task)
        shot = await db.get(ShotRecord, s1["id"])
        shot.duration_seconds = snapshot["duration_seconds"] + 4.0
        shot.aspect_ratio = "9:16" if snapshot["aspect_ratio"] != "9:16" else "1:1"
        await db.commit()
    seen: list[VideoGenerationRequest] = []

    class _Capture:
        async def generate_video(self, request):
            seen.append(request)
            return ProviderTask(external_task_id="ext_snap", status="queued")

    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: _Capture())
    await _step(tid)
    assert seen[0].duration_seconds == snapshot["duration_seconds"]
    assert seen[0].aspect_ratio == snapshot["aspect_ratio"]


@pytest.mark.asyncio
async def test_upstream_reselection_marks_downstream_stale_without_auto_render(client: AsyncClient):
    pid, cid, s1, s2 = await _chain_and_render(client)
    regen = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "force_retry": True})).json()
    assert (await _drive_until_done(regen["id"])).state == QueueTaskState.SUCCEEDED
    takes = (await client.get(f"/api/v1/queue/shots/{s1['id']}/takes")).json()
    sel = (await client.post(f"/api/v1/queue/shots/{s1['id']}/takes/{takes[1]['id']}/select")).json()
    assert s2["id"] in sel["propagated_downstream_shots"]
    st = (await _shots(client, pid))[s2["id"]]
    assert st["generation_state"] == "not_started"
    assert st["first_frame_asset_id"] == takes[1]["last_frame_asset_id"]
    before = len(await _tasks_for_shot(s2["id"]))
    await _ticks(client, pid, 2)
    assert len(await _tasks_for_shot(s2["id"])) == before  # no automatic paid re-render
    launch = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                                json={"connection_id": cid, "only_changed": True})).json()
    assert len(launch["enqueued_task_ids"]) == 1 and s1["id"] in launch["skipped_shot_ids"]


@pytest.mark.asyncio
async def test_upstream_reselection_skips_active_downstream_and_dispatch_fails_stale(client: AsyncClient):
    pid, cid, s1, s2 = await _chain_and_render(client)
    regen = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "force_retry": True})).json()
    await _drive_until_done(regen["id"])
    down = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s2["id"], "connection_id": cid, "force_retry": True})).json()
    old_first = (await _shots(client, pid))[s2["id"]]["first_frame_asset_id"]
    takes = (await client.get(f"/api/v1/queue/shots/{s1['id']}/takes")).json()
    sel = (await client.post(f"/api/v1/queue/shots/{s1['id']}/takes/{takes[1]['id']}/select")).json()
    assert s2["id"] not in sel["propagated_downstream_shots"]
    assert (await _shots(client, pid))[s2["id"]]["first_frame_asset_id"] == old_first
    t = await _step(down["id"])
    assert t.state == QueueTaskState.FAILED
    assert t.error_category == "stale_conditioning_frame"
    assert t.external_task_id is None


@pytest.mark.asyncio
async def test_selecting_take_without_last_frame_clears_last_frame(client: AsyncClient):
    pid, _cid, s1, s2 = await _chain_and_render(client)
    assert (await _shots(client, pid))[s2["id"]]["generation_state"] == "succeeded"
    takes = (await client.get(f"/api/v1/queue/shots/{s1['id']}/takes")).json()
    async with STATE["maker"]() as db:
        take = await db.get(ShotTakeRecord, takes[0]["id"])
        original_last_frame = take.last_frame_asset_id
        take.last_frame_asset_id = None
        await db.commit()
    sel = (await client.post(f"/api/v1/queue/shots/{s1['id']}/takes/{takes[0]['id']}/select")).json()
    assert sel["last_frame_asset_id"] is None
    st = await _shots(client, pid)
    assert st[s1["id"]]["last_frame_asset_id"] is None
    assert st[s2["id"]]["first_frame_asset_id"] is None
    # A downstream success no longer reflects valid continuity once its input is gone.
    assert st[s2["id"]]["generation_state"] == "blocked_upstream_frame"

    # Restoring the frame its selected take was rendered on unblocks it as succeeded
    # (no paid re-render needed).
    async with STATE["maker"]() as db:
        take = await db.get(ShotTakeRecord, takes[0]["id"])
        take.last_frame_asset_id = original_last_frame
        await db.commit()
    await client.post(f"/api/v1/queue/shots/{s1['id']}/takes/{takes[0]['id']}/select")
    st = await _shots(client, pid)
    assert st[s2["id"]]["first_frame_asset_id"] == original_last_frame
    assert st[s2["id"]]["generation_state"] == "succeeded"


@pytest.mark.asyncio
async def test_only_changed_rerenders_when_model_changes(client: AsyncClient):
    pid, cid, s1, _ = await _setup(client)
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid, "shot_ids": [s1["id"]]})
    await _ticks(client, pid)
    same = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                              json={"connection_id": cid, "shot_ids": [s1["id"]]})).json()
    assert same["skipped_shot_ids"] == [s1["id"]]
    other = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                               json={"connection_id": cid, "shot_ids": [s1["id"]], "model_id": "muse-video-mock-v2"})).json()
    assert other["enqueued_count"] == 1


@pytest.mark.asyncio
async def test_unexpected_download_error_is_terminal_and_releases_lease(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _ = await _setup(client)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]

    class _BrokenDownload:
        async def generate_video(self, request):
            return ProviderTask(external_task_id="ext_dl", status="queued")

        async def get_task(self, task_id):
            return ProviderTaskStatus(external_task_id=task_id, status="succeeded", result_media_url="https://x.test/a.mp4")

        async def download_result(self, ref, dest_dir):
            raise RuntimeError("boom")

    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: _BrokenDownload())
    await _step(tid)
    t = await _step(tid)
    assert t.state == QueueTaskState.FAILED
    assert t.retryable is False
    assert "RuntimeError" in (t.error_message_redacted or "")
    async with STATE["maker"]() as db:
        assert await db.get(GenerationDispatchLeaseRecord, tid) is None


# ======================= worker / recovery / export =======================


@pytest.mark.asyncio
async def test_recovery_cron_is_bounded(client: AsyncClient, monkeypatch):
    _pid, cid, s1, s2 = await _setup(client)
    for sid in (s1["id"], s2["id"]):
        await client.post("/api/v1/queue/tasks", json={"shot_id": sid, "connection_id": cid})
    monkeypatch.setattr(queue_worker, "RECOVERY_MAX_TASKS_PER_RUN", 1)
    result = await queue_worker.recover_pending_generation_tasks_job({"redis": _FakeArqPool()})
    assert result["found"] == 1


@pytest.mark.asyncio
async def test_export_result_persistence_failure_marks_failed_and_discards_file(client: AsyncClient, monkeypatch):
    clip = os.path.join(queue_worker.settings.media_root, "prj_rev2", "clip.mp4")
    async with STATE["maker"]() as db:
        db.add(Project(id="prj_rev2", name="export"))
        # Models declare FKs without relationship(): flush each parent level first.
        await db.flush()
        db.add(TimelineSequenceRecord(id="seq_rev2", project_id="prj_rev2"))
        await db.flush()
        db.add(ExportJobRecord(
            id="exp_rev2", project_id="prj_rev2", sequence_id="seq_rev2", state=ExportJobState.QUEUED,
            manifest_snapshot_json={"resolved_clips": [{"path": clip, "effective_duration": 1.0}],
                                    "unsupported_features": []},
        ))
        await db.commit()
    rendered: list[str] = []

    def _fake_render(*, output_dir, export_id, fps, resolution, clips):
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{export_id}.mp4")
        with open(path, "wb") as stream:
            stream.write(b"x")
        rendered.append(path)
        return path, 1, "a" * 64, ["ffmpeg"], {"valid": True}

    async def _broken_persist(self, asset, job):
        raise RuntimeError("db down")

    monkeypatch.setattr(TimelineValidationService, "render_and_probe_mp4", _fake_render)
    monkeypatch.setattr(StudioRepository, "create_export_result", _broken_persist)
    result = await queue_worker.process_export_job({}, "exp_rev2")
    assert result["state"] == "failed"
    assert rendered and not os.path.exists(rendered[0])
    async with STATE["maker"]() as db:
        job = await db.get(ExportJobRecord, "exp_rev2")
        assert job.state == ExportJobState.FAILED


# ======================= provider response validation =======================


def _adapter(handler) -> Muse2ApiAdapter:
    return Muse2ApiAdapter(base_url="http://provider.test", api_key="sk-secret", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_generate_read_timeout_is_dispatch_outcome_unknown():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ProviderFailure) as info:
        await _adapter(handler).generate_video(VideoGenerationRequest(shot_id="s", prompt="p"))
    assert info.value.category == ErrorCategory.DISPATCH_OUTCOME_UNKNOWN
    assert info.value.retryable is False


@pytest.mark.asyncio
async def test_generate_connect_error_stays_retryable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderFailure) as info:
        await _adapter(handler).generate_video(VideoGenerationRequest(shot_id="s", prompt="p"))
    assert info.value.category == ErrorCategory.TRANSIENT_NETWORK
    assert info.value.retryable is True


@pytest.mark.asyncio
async def test_poll_rejects_non_string_status_and_caps_error():
    def bad_status(request):
        return httpx.Response(200, json={"status": {"nested": True}})

    with pytest.raises(ProviderFailure) as info:
        await _adapter(bad_status).get_task("t1")
    assert info.value.category == ErrorCategory.INVALID_REQUEST

    def long_error(request):
        return httpx.Response(200, json={"status": "failed", "error": "e" * 100_000, "account_id": ["x"]})

    with pytest.raises(ProviderFailure):
        await _adapter(long_error).get_task("t1")

    def capped(request):
        return httpx.Response(200, json={"status": "failed", "error": "e" * 100_000})

    status = await _adapter(capped).get_task("t1")
    assert status.status == "failed" and len(status.error_message) == 4096


@pytest.mark.asyncio
async def test_deeply_nested_json_is_invalid_request():
    def handler(request):
        return httpx.Response(200, content=("[" * 200_000).encode())

    with pytest.raises(ProviderFailure) as info:
        await _adapter(handler).get_task("t1")
    assert info.value.category == ErrorCategory.INVALID_REQUEST
    assert info.value.retryable is False


def test_snapshot_marker_round_trip():
    task = GenerationTaskRecord(history_log=["x", "request_snapshot=" + json.dumps({"aspect_ratio": "16:9"})])
    assert request_snapshot_of(task) == {"aspect_ratio": "16:9"}


@pytest.mark.asyncio
async def test_export_cancellation_signals_render_to_kill_ffmpeg(client: AsyncClient, monkeypatch):
    import threading

    from muse_orchestrator.services.timeline_service import ExportCanceledError

    clip = os.path.join(queue_worker.settings.media_root, "prj_rev2c", "clip.mp4")
    async with STATE["maker"]() as db:
        db.add(Project(id="prj_rev2c", name="export"))
        # Models declare FKs without relationship(): flush each parent level first.
        await db.flush()
        db.add(TimelineSequenceRecord(id="seq_rev2c", project_id="prj_rev2c"))
        await db.flush()
        db.add(ExportJobRecord(
            id="exp_rev2c", project_id="prj_rev2c", sequence_id="seq_rev2c", state=ExportJobState.QUEUED,
            manifest_snapshot_json={"resolved_clips": [{"path": clip, "effective_duration": 1.0}],
                                    "unsupported_features": []},
        ))
        await db.commit()
    started = threading.Event()
    observed: dict[str, bool] = {}

    def _blocking_render(*, output_dir, export_id, fps, resolution, clips, cancel_event=None, timeout_seconds=None):
        started.set()
        observed["cancelled"] = bool(cancel_event is not None and cancel_event.wait(timeout=10))
        raise ExportCanceledError()

    monkeypatch.setattr(TimelineValidationService, "render_and_probe_mp4", _blocking_render)
    job = asyncio.ensure_future(queue_worker.process_export_job({}, "exp_rev2c"))
    while not started.is_set():
        await asyncio.sleep(0.01)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert observed == {"cancelled": True}  # render thread saw the event and stopped FFmpeg
    async with STATE["maker"]() as db:
        assert (await db.get(ExportJobRecord, "exp_rev2c")).state == ExportJobState.FAILED

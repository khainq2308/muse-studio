"""Regression tests for queue/frame-chain/export state hardening (deep-review findings)."""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_qstate_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.core.database import get_db  # noqa: E402 -- env must be set before import
from muse_orchestrator.main import app  # noqa: E402
from muse_orchestrator.models.domain import (  # noqa: E402
    Base,
    ExportJobRecord,
    ExportJobState,
    GenerationDispatchLeaseRecord,
    GenerationTaskRecord,
    QueueTaskState,
    ShotGenerationState,
    ShotRecord,
)
from muse_orchestrator.services import generation_queue  # noqa: E402
from muse_orchestrator.services.arq_dispatch import generation_step_job_id  # noqa: E402
from muse_orchestrator.services.generation_queue import (  # noqa: E402
    GENERATION_LEASE_MAX_SECONDS,
    GENERATION_STEP_JOB_TIMEOUT_SECONDS,
    GenerationQueueService,
)
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
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
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
    pid = (await client.post("/api/v1/projects", json={"name": "qstate"})).json()["id"]
    cid = (await client.post("/api/v1/connections", json={
        "name": "mock-qstate", "provider_type": "mock", "base_url": "http://localhost:8080",
        "max_concurrency": 2})).json()["id"]
    await client.post(f"/api/v1/projects/{pid}/scripts/import",
                      json={"raw_content": SCRIPT, "auto_apply_proposal": True})
    shots = (await client.get(f"/api/v1/projects/{pid}/shots")).json()
    return pid, cid, shots[0], shots[1]


async def _disabled_connection(client: AsyncClient) -> str:
    return (await client.post("/api/v1/connections", json={
        "name": "mock-off", "provider_type": "mock", "base_url": "http://localhost:8080",
        "enabled": False})).json()["id"]


async def _shots(client: AsyncClient, pid: str) -> dict[str, dict]:
    return {s["id"]: s for s in (await client.get(f"/api/v1/projects/{pid}/shots")).json()}


async def _ticks(client: AsyncClient, pid: str, n: int = 8) -> None:
    for _ in range(n):
        await client.post(f"/api/v1/projects/{pid}/batch/tick")


async def _chain_and_render(client: AsyncClient):
    pid, cid, s1, s2 = await _setup(client)
    await client.patch(f"/api/v1/projects/{pid}/shots/{s2['id']}", json={"continuity_mode": "frame_chain"})
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid})
    await _ticks(client, pid)
    st = await _shots(client, pid)
    assert st[s1["id"]]["generation_state"] == "succeeded"
    assert st[s2["id"]]["generation_state"] == "succeeded"
    return pid, cid, s1, s2


async def _tasks_for_shot(shot_id: str) -> list[GenerationTaskRecord]:
    async with STATE["maker"]() as db:
        rows = await db.execute(
            select(GenerationTaskRecord)
            .where(GenerationTaskRecord.shot_id == shot_id)
            .order_by(GenerationTaskRecord.created_at.asc())
        )
        return list(rows.scalars().all())


# --- BUG-001: the ARQ worker path auto-continues the frame chain -------------------------


@pytest.mark.asyncio
async def test_worker_completion_enqueues_ready_frame_chain_downstream(client: AsyncClient):
    pid, cid, s1, s2 = await _setup(client)
    await client.patch(f"/api/v1/projects/{pid}/shots/{s2['id']}", json={"continuity_mode": "frame_chain"})
    launch = (await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid})).json()
    assert launch["blocked_shot_ids"] == [s2["id"]]
    upstream_task_id = launch["enqueued_task_ids"][0]

    redis = _FakeArqPool()
    result: dict = {}
    for _ in range(8):  # no HTTP tick: only the worker drives the queue
        result = await queue_worker.process_generation_task_step({"redis": redis}, upstream_task_id)
        if result["state"] == "succeeded":
            break
    assert result["state"] == "succeeded"

    downstream = await _tasks_for_shot(s2["id"])
    assert len(downstream) == 1
    assert result["auto_enqueued_task_ids"] == [downstream[0].id]
    assert any(job[1] == (downstream[0].id,) for job in redis.jobs)
    assert (await _shots(client, pid))[s2["id"]]["generation_state"] == "queued"


@pytest.mark.asyncio
async def test_worker_never_auto_enqueues_frame_chain_shot_with_lint_blocker(client: AsyncClient):
    pid, cid, s1, s2 = await _setup(client)
    await client.patch(f"/api/v1/projects/{pid}/shots/{s2['id']}", json={"continuity_mode": "frame_chain"})
    launch = (await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid})).json()
    assert launch["blocked_shot_ids"] == [s2["id"]]
    # CAMERA_MOTION_CONFLICT is a linter blocker that is not a BLOCKED_REFERENCE code.
    patched = (await client.patch(
        f"/api/v1/projects/{pid}/shots/{s2['id']}",
        json={"camera_motion": "static", "action": "Quick dolly toward the hero"},
    )).json()
    assert [i["code"] for i in patched["lint_report"] if i["severity"] == "blocker"] == ["CAMERA_MOTION_CONFLICT"]

    redis = _FakeArqPool()
    result: dict = {}
    for _ in range(8):
        result = await queue_worker.process_generation_task_step({"redis": redis}, launch["enqueued_task_ids"][0])
        if result["state"] == "succeeded":
            break
    assert result["state"] == "succeeded"
    assert result["auto_enqueued_task_ids"] == []

    await _ticks(client, pid)
    assert await _tasks_for_shot(s2["id"]) == []
    assert (await _shots(client, pid))[s2["id"]]["generation_state"] == "blocked_reference"

    # Recompiling (another PATCH) must keep the shot blocked while any blocker remains.
    await client.patch(f"/api/v1/projects/{pid}/shots/{s2['id']}", json={"shot_size": "wide"})
    assert (await _shots(client, pid))[s2["id"]]["generation_state"] == "blocked_reference"


@pytest.mark.asyncio
async def test_enqueue_paths_share_the_lint_blocker_guard(client: AsyncClient):
    pid, cid, s1, _ = await _setup(client)
    await client.patch(
        f"/api/v1/projects/{pid}/shots/{s1['id']}",
        json={"camera_motion": "static", "action": "Quick dolly toward the hero"},
    )
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, s1["id"])
        assert await GenerationQueueService.check_enqueue_preconditions(db, shot) is not None
    r = await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})
    assert r.status_code == 422, r.text
    assert await _tasks_for_shot(s1["id"]) == []


# --- BUG-002: forced re-render never strands downstream as BLOCKED_UPSTREAM_FRAME --------


@pytest.mark.asyncio
async def test_force_rerender_does_not_block_downstream_with_valid_upstream_frame(client: AsyncClient):
    pid, cid, s1, s2 = await _chain_and_render(client)
    batch = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                               json={"connection_id": cid, "only_changed": False})).json()
    assert batch["blocked_shot_ids"] == []
    assert batch["enqueued_count"] == 2
    await _ticks(client, pid, 10)
    st = await _shots(client, pid)
    assert st[s2["id"]]["generation_state"] == "succeeded"


@pytest.mark.asyncio
async def test_non_first_upstream_success_unblocks_blocked_downstream(client: AsyncClient):
    pid, cid, s1, s2 = await _chain_and_render(client)
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, s2["id"])
        shot.generation_state = ShotGenerationState.BLOCKED_UPSTREAM_FRAME
        await db.commit()
    task = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "force_retry": True})).json()
    for _ in range(5):
        await client.post(f"/api/v1/queue/tasks/{task['id']}/step")
    assert len((await client.get(f"/api/v1/queue/shots/{s1['id']}/takes")).json()) == 2
    assert (await _shots(client, pid))[s2["id"]]["generation_state"] != "blocked_upstream_frame"


@pytest.mark.asyncio
async def test_existing_succeeded_task_is_not_reported_enqueued_or_left_blocked(client: AsyncClient):
    pid, cid, s1, s2 = await _chain_and_render(client)
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, s2["id"])
        shot.generation_state = ShotGenerationState.BLOCKED_UPSTREAM_FRAME
        await db.commit()
    batch = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                               json={"connection_id": cid, "only_changed": True})).json()
    assert s2["id"] not in batch["blocked_shot_ids"]
    assert batch["enqueued_task_ids"] == []
    assert s2["id"] in batch["skipped_shot_ids"]
    assert (await _shots(client, pid))[s2["id"]]["generation_state"] == "succeeded"


# --- BUG-004: only_changed respects the conditioning first frame ------------------------


@pytest.mark.asyncio
async def test_only_changed_rerenders_downstream_when_first_frame_changed(client: AsyncClient):
    pid, cid, s1, s2 = await _chain_and_render(client)
    takes = (await client.get(f"/api/v1/queue/shots/{s1['id']}/takes")).json()
    await client.post(f"/api/v1/projects/{pid}/shots/{s1['id']}/takes/{takes[0]['id']}/flag-continuity",
                      json={"regenerate_now": True, "connection_id": cid})
    await _ticks(client, pid)
    takes = (await client.get(f"/api/v1/queue/shots/{s1['id']}/takes")).json()
    await client.post(f"/api/v1/queue/shots/{s1['id']}/takes/{takes[1]['id']}/select")
    assert (await _shots(client, pid))[s2["id"]]["first_frame_asset_id"] == takes[1]["last_frame_asset_id"]

    batch = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                               json={"connection_id": cid, "only_changed": True})).json()
    assert s2["id"] not in batch["skipped_shot_ids"]
    assert s1["id"] in batch["skipped_shot_ids"]
    assert len(batch["enqueued_task_ids"]) == 1

    # Once re-rendered on the new frame, the cache applies again.
    await _ticks(client, pid)
    again = (await client.post(f"/api/v1/projects/{pid}/batch/launch",
                               json={"connection_id": cid, "only_changed": True})).json()
    assert again["enqueued_task_ids"] == []


# --- BUG-005: stale task outcomes never downgrade a shot -----------------------------------


@pytest.mark.asyncio
async def test_cancel_or_fail_of_extra_task_keeps_succeeded_shot(client: AsyncClient):
    pid, cid, s1, _s2 = await _setup(client)
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid, "shot_ids": [s1["id"]]})
    await _ticks(client, pid)
    assert (await _shots(client, pid))[s1["id"]]["generation_state"] == "succeeded"

    extra = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "force_retry": True})).json()
    assert (await client.post(f"/api/v1/queue/tasks/{extra['id']}/cancel")).status_code == 200
    assert (await _shots(client, pid))[s1["id"]]["generation_state"] == "succeeded"

    await client.patch(f"/api/v1/projects/{pid}/shots/{s1['id']}", json={"action": "[SIMULATE_401] boom"})
    failing = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "force_retry": True})).json()
    stepped = (await client.post(f"/api/v1/queue/tasks/{failing['id']}/step")).json()
    assert stepped["state"] == "failed"
    assert (await _shots(client, pid))[s1["id"]]["generation_state"] == "succeeded"


@pytest.mark.asyncio
async def test_cancel_stale_task_does_not_override_newer_active_task(client: AsyncClient):
    pid, cid, s1, _s2 = await _setup(client)
    old = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "idempotency_key": "old-key"})).json()
    await client.patch(f"/api/v1/projects/{pid}/shots/{s1['id']}", json={"action": "new direction"})
    new = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "idempotency_key": "new-key"})).json()
    assert new["id"] != old["id"]
    await client.post(f"/api/v1/queue/tasks/{old['id']}/cancel")
    assert (await _shots(client, pid))[s1["id"]]["generation_state"] == "queued"


# --- BUG-006: force_retry reuses an in-flight task -----------------------------------------


@pytest.mark.asyncio
async def test_force_retry_returns_existing_active_task(client: AsyncClient):
    _pid, cid, s1, _s2 = await _setup(client)
    first = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "force_retry": True})).json()
    second = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "force_retry": True})).json()
    assert second["id"] == first["id"]
    assert second["created_new"] is False
    assert len(await _tasks_for_shot(s1["id"])) == 1


# --- BUG-007 / API-003: disabled connections and validate-before-commit ---------------------


@pytest.mark.asyncio
async def test_retry_failed_rejects_disabled_connection(client: AsyncClient):
    pid, cid, s1, _s2 = await _setup(client)
    off = await _disabled_connection(client)
    await client.patch(f"/api/v1/projects/{pid}/shots/{s1['id']}", json={"action": "[SIMULATE_401] boom"})
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid, "shot_ids": [s1["id"]]})
    await _ticks(client, pid)
    before = len(await _tasks_for_shot(s1["id"]))
    res = await client.post(f"/api/v1/projects/{pid}/batch/retry-failed?connection_id={off}")
    assert res.status_code == 400
    assert len(await _tasks_for_shot(s1["id"])) == before


@pytest.mark.asyncio
async def test_flag_continuity_validates_connection_before_committing(client: AsyncClient):
    pid, cid, s1, _s2 = await _setup(client)
    off = await _disabled_connection(client)
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid, "shot_ids": [s1["id"]]})
    await _ticks(client, pid)
    take = (await client.get(f"/api/v1/queue/shots/{s1['id']}/takes")).json()[0]
    url = f"/api/v1/projects/{pid}/shots/{s1['id']}/takes/{take['id']}/flag-continuity"

    disabled = await client.post(url, json={"regenerate_now": True, "connection_id": off})
    assert disabled.status_code == 400
    missing = await client.post(url, json={"regenerate_now": True, "connection_id": "conn_missing"})
    assert missing.status_code == 404
    take_after = (await client.get(f"/api/v1/queue/shots/{s1['id']}/takes")).json()[0]
    assert take_after["continuity_flagged"] is False
    assert len(await _tasks_for_shot(s1["id"])) == 1


@pytest.mark.asyncio
async def test_step_task_never_dispatches_on_disabled_connection(client: AsyncClient):
    _pid, cid, s1, _s2 = await _setup(client)
    task = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()
    await client.patch(f"/api/v1/connections/{cid}", json={"enabled": False})
    async with STATE["maker"]() as db:
        from muse_orchestrator.models.domain import ProviderConnection

        conn = await db.get(ProviderConnection, cid)
        conn.enabled = False
        await db.commit()
    stepped = (await client.post(f"/api/v1/queue/tasks/{task['id']}/step")).json()
    assert stepped["state"] == "failed"
    assert stepped["external_task_id"] is None
    assert stepped["error_category"] == "connection_disabled"
    assert stepped["retryable"] is False


@pytest.mark.asyncio
async def test_retry_failed_maps_enqueue_precondition_to_422_without_partial_commit(client: AsyncClient):
    pid, cid, s1, s2 = await _setup(client)
    await client.patch(f"/api/v1/projects/{pid}/shots/{s2['id']}", json={"continuity_mode": "frame_chain"})
    async with STATE["maker"]() as db:
        for shot_id in (s1["id"], s2["id"]):
            shot = await db.get(ShotRecord, shot_id)
            shot.generation_state = ShotGenerationState.FAILED
        await db.commit()
    res = await client.post(f"/api/v1/projects/{pid}/batch/retry-failed?connection_id={cid}")
    assert res.status_code == 422
    assert await _tasks_for_shot(s1["id"]) == []
    assert await _tasks_for_shot(s2["id"]) == []


# --- API-004: nonexistent parents are 404 ----------------------------------------------------


@pytest.mark.asyncio
async def test_missing_parents_return_404(client: AsyncClient):
    assert (await client.post("/api/v1/projects/prj_missing/batch/tick")).status_code == 404
    assert (await client.post("/api/v1/projects/prj_missing/batch/retry-failed")).status_code == 404
    assert (await client.get("/api/v1/queue/shots/shot_missing/takes")).status_code == 404


# --- BUG-008: ARQ job ids, timeouts and cancellation -----------------------------------------


@pytest.mark.asyncio
async def test_lease_wait_reschedule_gets_a_new_job_id(client: AsyncClient):
    _pid, cid, s1, _s2 = await _setup(client)
    created = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()
    async with STATE["maker"]() as db:
        task = await db.get(GenerationTaskRecord, created["id"])
        task.state = QueueTaskState.RUNNING
        db.add(GenerationDispatchLeaseRecord(
            task_id=task.id, connection_id=cid,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=200)))
        await db.commit()
    async with STATE["maker"]() as db:
        id_a = generation_step_job_id(await GenerationQueueService.step_task(db, created["id"]))
    await asyncio.sleep(0.01)
    async with STATE["maker"]() as db:
        id_b = generation_step_job_id(await GenerationQueueService.step_task(db, created["id"]))
    assert id_a != id_b


def test_generation_step_job_settings_cover_the_lease() -> None:
    step = next(
        f for f in queue_worker.WorkerSettings.functions
        if getattr(f, "name", None) == "process_generation_task_step"
    )
    assert step.keep_result_s == 0
    assert step.timeout_s >= GENERATION_LEASE_MAX_SECONDS
    assert GENERATION_STEP_JOB_TIMEOUT_SECONDS >= GENERATION_LEASE_MAX_SECONDS
    assert queue_worker.WorkerSettings.job_timeout >= GENERATION_LEASE_MAX_SECONDS


@pytest.mark.asyncio
async def test_cancelled_dispatch_releases_lease(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _s2 = await _setup(client)
    created = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()

    class _CancellingProvider:
        async def generate_video(self, request):
            raise asyncio.CancelledError()

        async def generate_image(self, request):
            raise asyncio.CancelledError()

    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: _CancellingProvider())
    async with STATE["maker"]() as db:
        with pytest.raises(asyncio.CancelledError):
            await GenerationQueueService.step_task(db, created["id"])
    async with STATE["maker"]() as db:
        assert await db.get(GenerationDispatchLeaseRecord, created["id"]) is None


# --- BUG-009: export jobs never stay RUNNING forever ------------------------------------------


async def _export_job(export_id: str, clips: list[dict], state: ExportJobState = ExportJobState.QUEUED,
                      manifest_extra: dict | None = None, created_at: datetime | None = None) -> None:
    from muse_orchestrator.models.domain import Project

    async with STATE["maker"]() as db:
        if await db.get(Project, "prj_export") is None:
            db.add(Project(id="prj_export", name="export"))
        manifest = {"resolved_clips": clips, "unsupported_features": []}
        manifest.update(manifest_extra or {})
        job = ExportJobRecord(
            id=export_id, project_id="prj_export", sequence_id="seq_x", state=state,
            manifest_snapshot_json=manifest,
        )
        if created_at is not None:
            job.created_at = created_at
        db.add(job)
        await db.commit()


async def _export_state(export_id: str) -> ExportJobRecord:
    async with STATE["maker"]() as db:
        return await db.get(ExportJobRecord, export_id)


@pytest.mark.asyncio
async def test_export_cancelled_mid_render_is_marked_failed(client: AsyncClient, monkeypatch):
    # settings is a process-wide singleton; another module may have configured MEDIA_ROOT first.
    clip = os.path.join(queue_worker.settings.media_root, "prj_export", "clip.mp4")
    await _export_job("exp_cancel", [{"path": clip, "effective_duration": 1.0}])

    async def _cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(queue_worker.asyncio, "to_thread", _cancelled)
    with pytest.raises(asyncio.CancelledError):
        await queue_worker.process_export_job({}, "exp_cancel")
    job = await _export_state("exp_cancel")
    assert job.state == ExportJobState.FAILED
    assert "interrupted" in (job.error_message_redacted or "")


@pytest.mark.asyncio
async def test_export_rejects_clip_outside_media_root(client: AsyncClient, monkeypatch):
    outside = tempfile.mkdtemp(prefix="muse_outside_")
    await _export_job("exp_escape", [{"path": os.path.join(outside, "x.mp4"), "effective_duration": 1.0}])
    called = False

    async def _render(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("render must not run")

    monkeypatch.setattr(queue_worker.asyncio, "to_thread", _render)
    result = await queue_worker.process_export_job({}, "exp_escape")
    assert result["state"] == "failed"
    assert called is False
    assert "MEDIA_ROOT" in ((await _export_state("exp_escape")).error_message_redacted or "")


@pytest.mark.asyncio
async def test_recovery_cron_fails_stale_running_exports_only(client: AsyncClient):
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    await _export_job("exp_stale", [], state=ExportJobState.RUNNING, manifest_extra={"worker_started_at": old})
    await _export_job("exp_live", [], state=ExportJobState.RUNNING, manifest_extra={"worker_started_at": fresh})
    result = await queue_worker.recover_pending_export_jobs_job({})
    assert result["stale_failed"] == 1
    assert (await _export_state("exp_stale")).state == ExportJobState.FAILED
    assert (await _export_state("exp_live")).state == ExportJobState.RUNNING

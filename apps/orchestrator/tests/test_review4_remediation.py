"""Regression tests for the fourth external review (confirmed findings F-01..F-18)."""
# The `client` fixture is imported from test_queue_review2; test parameters reuse its name.
# ruff: noqa: F811
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from muse_orchestrator.application import queue_service
from muse_orchestrator.application import services as app_services
from muse_orchestrator.core.config import SecretRedactor
from muse_orchestrator.main import app
from muse_orchestrator.models.domain import (
    Asset,
    ExportJobRecord,
    GenerationDispatchLeaseRecord,
    GenerationTaskRecord,
    ProviderConnection,
    QueueTaskState,
    ShotGenerationState,
    ShotRecord,
    TimelineClipRecord,
    TimelineSequenceRecord,
    TimelineTrackRecord,
)
from muse_orchestrator.providers.base import (
    ErrorCategory,
    ProviderFailure,
    ProviderHealth,
    ProviderTask,
    ProviderTaskStatus,
)
from muse_orchestrator.services import generation_queue
from muse_orchestrator.services.backup_service import _MAX_SEQUENCE_SECONDS, BackupService
from tests.test_queue_review2 import (  # noqa: F401 - `client` is a pytest fixture
    STATE,
    _drive_until_dispatched,
    _setup,
    _shots,
    _step,
    _tasks_for_shot,
    client,
)

QUESTION = {"kind": "question", "text": "Which mood?", "options": [], "asked_at": None}


class _Gateway:
    """Accepts dispatches; get_task follows a scripted list of statuses or failures."""

    def __init__(self, script):
        self.script = list(script)
        self.replies: list[dict] = []

    async def generate_video(self, request):
        return ProviderTask(external_task_id="ext_1", status="queued")

    async def get_task(self, task_id):
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return ProviderTaskStatus(external_task_id=task_id, status=item, question=QUESTION if item == "needs_input" else None)

    async def reply(self, task_id, answer):
        self.replies.append(answer)
        return ProviderTaskStatus(external_task_id=task_id, status="running")


async def _awaiting(client: AsyncClient, monkeypatch, script):
    _pid, cid, s1, _ = await _setup(client)
    gateway = _Gateway(["needs_input", *script])
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: gateway)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    await _drive_until_dispatched(tid)
    task = await _step(tid)
    assert task.state == QueueTaskState.AWAITING_INPUT
    return tid, cid, s1, gateway


# --- F-01 / F-02 / F-16: SecretRedactor ------------------------------------------------------


def test_sensitive_keys_are_redacted_even_when_the_value_looks_like_a_url():
    redacted = SecretRedactor.redact_dict({
        "password": "my://secret_pass",
        "api_key": "http://secret-token",
        "secret": "https://hooks.slack.com/services/T00/B00/XXXX",
        "proxy_url": "http://user:pw@proxy.example:8080",
        "secret_pin": 123456,
        "private_key": b"raw-bytes",
        "max_tokens": 5,
        "token": None,
    })
    assert redacted["password"] == redacted["api_key"] == redacted["secret"] == "[REDACTED]"
    assert redacted["secret_pin"] == redacted["private_key"] == "[REDACTED]"
    assert "pw" not in redacted["proxy_url"] and "proxy.example" in redacted["proxy_url"]
    assert redacted["max_tokens"] == 5 and redacted["token"] is None


def test_only_proxy_url_keys_keep_their_host():
    redacted = SecretRedactor.redact_dict({
        "proxy_password": "http://gw.example/SECRET/",
        "proxy_token": "x://y",
        "proxy_pass": "hunter2",
        "pwd": "hunter2",
        "body": b"Authorization: Bearer abcdefghijklmnop",
    })
    assert redacted["proxy_password"] == redacted["proxy_token"] == "[REDACTED]"
    assert redacted["proxy_pass"] == redacted["pwd"] == "[REDACTED]"
    assert "abcdefghijklmnop" not in redacted["body"]


@pytest.mark.parametrize("text", [
    "Authorization: sk-live-1234567890abcdef",
    "Authorization: ApiKey sk-live-1234567890abcdef",
    '{"Authorization": "sk-live-1234567890abcdef"}',
    "Proxy-Authorization: Basic sk-live-1234567890abcdef",
    # The credential is followed by more text: it must not be mistaken for a scheme word.
    "Authorization: sk-live-1234567890abcdef rejected by upstream",
    "Authorization: sk-live-1234567890abcdef\r\nContent-Type: application/json",
    "authorization=sk-live-1234567890abcdef user=bob",
    '{"Authorization": "sk-live-1234567890abcdef def", "x": 1}',
])
def test_authorization_headers_without_a_known_scheme_are_redacted(text):
    assert "sk-live-1234567890abcdef" not in SecretRedactor.redact_text(text)


# --- F-03: failing polls while awaiting input -------------------------------------------------


@pytest.mark.asyncio
async def test_failing_polls_while_awaiting_input_end_in_poll_timeout(client: AsyncClient, monkeypatch):
    outage = ProviderFailure(ErrorCategory.UPSTREAM_5XX, "gateway down", 503, True)
    tid, _cid, s1, _gw = await _awaiting(client, monkeypatch, [outage])
    async with STATE["maker"]() as db:
        task = await db.get(GenerationTaskRecord, tid)
        task.max_polls = task.poll_count + 2
        await db.commit()

    states = [(await _step(tid)).state for _ in range(4)]

    assert states[-1] == QueueTaskState.POLL_TIMEOUT
    final = (await _tasks_for_shot(s1["id"]))[-1]
    assert final.pending_input_json is None and final.retryable is True


# --- F-04: replies hold the task lease ------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_while_a_poll_holds_the_lease_is_a_retryable_conflict(client: AsyncClient, monkeypatch):
    tid, cid, _s1, gateway = await _awaiting(client, monkeypatch, ["needs_input"])
    monkeypatch.setattr(queue_service, "_REPLY_BUSY_ATTEMPTS", 2)
    monkeypatch.setattr(queue_service, "_REPLY_BUSY_RETRY_SECONDS", 0.0)
    async with STATE["maker"]() as db:
        db.add(GenerationDispatchLeaseRecord(
            task_id=tid, connection_id=cid, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
        await db.commit()

    busy = await client.post(f"/api/v1/queue/tasks/{tid}/reply", json={"text": "calm"})

    assert busy.status_code == 409, busy.text
    assert busy.json()["retryable"] is True
    assert gateway.replies == []


@pytest.mark.asyncio
async def test_successful_reply_releases_its_lease(client: AsyncClient, monkeypatch):
    tid, _cid, _s1, gateway = await _awaiting(client, monkeypatch, ["needs_input"])

    replied = await client.post(f"/api/v1/queue/tasks/{tid}/reply", json={"text": "calm"})

    assert replied.status_code == 200, replied.text
    assert replied.json()["state"] == "running"
    assert gateway.replies == [{"text": "calm"}]
    async with STATE["maker"]() as db:
        assert await db.get(GenerationDispatchLeaseRecord, tid) is None


class _FailingReplyGateway(_Gateway):
    def __init__(self, script, error):
        super().__init__(script)
        self.error = error

    async def reply(self, task_id, answer):
        self.replies.append(answer)
        raise self.error


async def _lease_of(tid):
    async with STATE["maker"]() as db:
        return await db.get(GenerationDispatchLeaseRecord, tid)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ProviderFailure(ErrorCategory.UPSTREAM_5XX, "gateway down token=abcdefghijklmnop", 503, True),
    RuntimeError("unexpected"),
])
async def test_reply_failure_releases_the_lease_and_keeps_the_question(client: AsyncClient, monkeypatch, error):
    _pid, cid, s1, _ = await _setup(client)
    gateway = _FailingReplyGateway(["needs_input"], error)
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: gateway)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    await _drive_until_dispatched(tid)
    assert (await _step(tid)).state == QueueTaskState.AWAITING_INPUT

    if isinstance(error, RuntimeError):
        with pytest.raises(RuntimeError):
            await client.post(f"/api/v1/queue/tasks/{tid}/reply", json={"text": "calm"})
    else:
        failed = await client.post(f"/api/v1/queue/tasks/{tid}/reply", json={"text": "calm"})
        assert failed.status_code >= 500
        assert "abcdefghijklmnop" not in failed.text

    assert gateway.replies == [{"text": "calm"}]
    assert await _lease_of(tid) is None
    assert (await _tasks_for_shot(s1["id"]))[-1].state == QueueTaskState.AWAITING_INPUT


@pytest.mark.asyncio
async def test_rejected_reply_refreshes_the_task_after_releasing_the_lease(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _ = await _setup(client)
    gateway = _FailingReplyGateway(
        ["needs_input", "running"],
        ProviderFailure(ErrorCategory.INPUT_NOT_PENDING, "not waiting", 409, False),
    )
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: gateway)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    await _drive_until_dispatched(tid)
    assert (await _step(tid)).state == QueueTaskState.AWAITING_INPUT

    rejected = await client.post(f"/api/v1/queue/tasks/{tid}/reply", json={"text": "calm"})

    assert rejected.status_code == 409, rejected.text
    # The refresh step polled the gateway (the lease was released first) and saw "running".
    assert (await _tasks_for_shot(s1["id"]))[-1].state == QueueTaskState.RUNNING
    assert await _lease_of(tid) is None


@pytest.mark.asyncio
async def test_worker_step_defers_while_a_reply_lease_is_live_and_polls_after_it_expires(client: AsyncClient, monkeypatch):
    tid, cid, _s1, gateway = await _awaiting(client, monkeypatch, ["needs_input"])
    polls_before = len(gateway.script)
    async with STATE["maker"]() as db:
        db.add(GenerationDispatchLeaseRecord(
            task_id=tid, connection_id=cid, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
        await db.commit()

    deferred = await _step(tid)
    assert deferred.state == QueueTaskState.AWAITING_INPUT
    assert await _lease_of(tid) is not None

    async with STATE["maker"]() as db:
        lease = await db.get(GenerationDispatchLeaseRecord, tid)
        lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    resumed = await _step(tid)

    # A task with an external id is polled again; it is never failed as dispatch_outcome_unknown.
    assert resumed.state == QueueTaskState.AWAITING_INPUT
    assert resumed.error_category is None
    assert len(gateway.script) <= polls_before


# --- F-05: pause/resume/account deletion ------------------------------------------------------


@pytest.mark.asyncio
async def test_account_pinned_to_a_paused_task_cannot_be_deleted(client: AsyncClient):
    _pid, cid, s1, _ = await _setup(client)
    account = (await client.post(f"/api/v1/connections/{cid}/accounts", json={"label": "acc", "api_key": "k"})).json()
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    async with STATE["maker"]() as db:
        task = await db.get(GenerationTaskRecord, tid)
        task.state = QueueTaskState.PAUSED
        task.account_id_used = account["id"]
        await db.commit()

    deleted = await client.delete(f"/api/v1/connections/{cid}/accounts/{account['id']}")

    assert deleted.status_code == 409, deleted.text


@pytest.mark.asyncio
async def test_resume_of_an_undispatched_task_on_a_disabled_connection_is_refused(client: AsyncClient):
    _pid, cid, s1, _ = await _setup(client)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    assert (await client.post(f"/api/v1/queue/tasks/{tid}/pause")).status_code == 200
    async with STATE["maker"]() as db:
        conn = await db.get(ProviderConnection, cid)
        conn.enabled = False
        await db.commit()

    resumed = await client.post(f"/api/v1/queue/tasks/{tid}/resume")

    assert resumed.status_code == 409, resumed.text
    assert (await _tasks_for_shot(s1["id"]))[-1].state == QueueTaskState.PAUSED


@pytest.mark.asyncio
async def test_pause_after_the_worker_finished_is_a_conflict(client: AsyncClient):
    _pid, cid, s1, _ = await _setup(client)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    async with STATE["maker"]() as db:
        task = await db.get(GenerationTaskRecord, tid)
        task.state = QueueTaskState.SUCCEEDED
        await db.commit()

    paused = await client.post(f"/api/v1/queue/tasks/{tid}/pause")

    assert paused.status_code == 409
    assert (await _tasks_for_shot(s1["id"]))[-1].state == QueueTaskState.SUCCEEDED


# --- F-06: Redis failure does not leave a retry half done -------------------------------------


class _DownPool:
    async def enqueue_job(self, *args, **kwargs):
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_retry_failed_creates_every_task_before_reporting_a_scheduling_outage(client: AsyncClient):
    pid, cid, s1, s2 = await _setup(client)
    for shot in (s1, s2):
        tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": shot["id"], "connection_id": cid})).json()["id"]
        async with STATE["maker"]() as db:
            task = await db.get(GenerationTaskRecord, tid)
            task.state = QueueTaskState.FAILED
            (await db.get(ShotRecord, shot["id"])).generation_state = ShotGenerationState.FAILED
            await db.commit()
    previous_pool = app.state.arq_pool
    app.state.arq_pool = _DownPool()
    try:
        retried = await client.post(f"/api/v1/projects/{pid}/batch/retry-failed")
    finally:
        app.state.arq_pool = previous_pool

    assert retried.status_code == 503, retried.text
    for shot in (s1, s2):
        assert (await _tasks_for_shot(shot["id"]))[-1].state == QueueTaskState.QUEUED


# --- F-07: assets used by the timeline or an export ------------------------------------------


@pytest.mark.asyncio
async def test_asset_used_by_timeline_and_export_needs_force_and_is_detached(client: AsyncClient):
    pid, _cid, s1, _ = await _setup(client)
    async with STATE["maker"]() as db:
        db.add(Asset(id="ast_tl", project_id=pid, asset_type="video", filename="c.mp4",
                     storage_rel_path=f"{pid}/generated/c.mp4", mime_type="video/mp4", size_bytes=1, sha256="0" * 64))
        db.add(TimelineSequenceRecord(id="seq_1", project_id=pid))
        await db.flush()
        db.add(TimelineTrackRecord(id="trk_1", sequence_id="seq_1",
                                   clips_json=[{"shot_id": s1["id"], "asset_id": "ast_tl"}]))
        await db.flush()
        db.add(TimelineClipRecord(id="clp_1", sequence_id="seq_1", track_id="trk_1", asset_id="ast_tl", order_index=0))
        db.add(ExportJobRecord(id="exp_1", project_id=pid, sequence_id="seq_1", output_asset_id="ast_tl"))
        await db.commit()

    refused = await client.delete("/api/v1/assets/ast_tl")
    assert refused.status_code == 409, refused.text
    assert "timeline clips" in refused.json()["detail"] and "exp_1" in refused.json()["detail"]

    forced = await client.delete("/api/v1/assets/ast_tl", params={"force": "true"})
    assert forced.status_code in (200, 204), forced.text
    async with STATE["maker"]() as db:
        assert (await db.get(TimelineClipRecord, "clp_1")).asset_id is None
        assert (await db.get(TimelineTrackRecord, "trk_1")).clips_json[0]["asset_id"] is None
        assert (await db.get(ExportJobRecord, "exp_1")).output_asset_id is None


# --- F-09 / F-10 / F-11: backup and restore ----------------------------------------------------


def test_restored_shot_states_follow_the_surviving_selected_take():
    rows = {"shots": [
        ({"generation_state": "running"}, SimpleNamespace(generation_state=ShotGenerationState.FAILED, selected_take_id="tke_1")),
        ({"generation_state": "queued"}, SimpleNamespace(generation_state=ShotGenerationState.FAILED, selected_take_id=None)),
        ({"generation_state": "succeeded"}, SimpleNamespace(generation_state=ShotGenerationState.SUCCEEDED, selected_take_id=None)),
        ({"generation_state": "succeeded"}, SimpleNamespace(generation_state=ShotGenerationState.SUCCEEDED, selected_take_id="tke_2")),
    ]}
    BackupService._settle_restored_shot_states(rows)
    assert [obj.generation_state for _row, obj in rows["shots"]] == [
        ShotGenerationState.SUCCEEDED,
        ShotGenerationState.FAILED,
        ShotGenerationState.NOT_STARTED,
        ShotGenerationState.SUCCEEDED,
    ]


def test_backup_sequence_bound_covers_back_to_back_clips():
    # api/timeline.py accepts 500 clips of up to 600 s each laid out sequentially.
    assert _MAX_SEQUENCE_SECONDS >= 500 * 600.0


@pytest.mark.asyncio
async def test_concurrent_placeholder_creation_is_tolerated(client: AsyncClient, monkeypatch):
    from muse_orchestrator.infrastructure.repositories import StudioRepository
    from muse_orchestrator.services.backup_service import RESTORED_PLACEHOLDER_CONNECTION_ID

    async with STATE["maker"]() as db:
        await BackupService(StudioRepository(db))._ensure_placeholder_connection()
        await db.commit()
    # A second restore that checked before the first one committed: its insert collides.
    async with STATE["maker"]() as db:
        real_get = db.get
        calls = {"n": 0}

        async def stale_first_get(model, ident, **kwargs):
            calls["n"] += 1
            if model is ProviderConnection and calls["n"] == 1:
                return None
            return await real_get(model, ident, **kwargs)

        monkeypatch.setattr(db, "get", stale_first_get)
        assert await BackupService(StudioRepository(db))._ensure_placeholder_connection() == RESTORED_PLACEHOLDER_CONNECTION_ID
        count = len((await db.execute(select(ProviderConnection).where(
            ProviderConnection.id == RESTORED_PLACEHOLDER_CONNECTION_ID))).scalars().all())
        assert count == 1


# --- F-12: leaving frame chain unblocks the shot ----------------------------------------------


@pytest.mark.asyncio
async def test_switching_to_standard_continuity_unblocks_the_shot(client: AsyncClient):
    pid, _cid, _s1, s2 = await _setup(client)
    await client.patch(f"/api/v1/projects/{pid}/shots/{s2['id']}", json={"continuity_mode": "frame_chain"})
    async with STATE["maker"]() as db:
        (await db.get(ShotRecord, s2["id"])).generation_state = ShotGenerationState.BLOCKED_UPSTREAM_FRAME
        await db.commit()

    patched = await client.patch(f"/api/v1/projects/{pid}/shots/{s2['id']}", json={"continuity_mode": "standard"})

    assert patched.status_code == 200, patched.text
    assert (await _shots(client, pid))[s2["id"]]["generation_state"] == "not_started"


# --- F-13: a rejected key is not "healthy" ------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_test_with_a_rejected_key_reports_auth_error(client: AsyncClient, monkeypatch):
    class _RejectingGateway:
        def __init__(self, *args, **kwargs):
            pass

        async def health(self):
            return ProviderHealth(healthy=True, ready=True, status_state="healthy", latency_ms=1.0)

        async def list_models(self):
            raise ProviderFailure(ErrorCategory.AUTH_FAILED, "invalid key", 401, False)

        async def first_frame_capability(self):
            return None

    monkeypatch.setattr(app_services, "Muse2ApiAdapter", _RejectingGateway)
    cid = (await client.post("/api/v1/connections", json={
        "name": "gw", "provider_type": "muse2api", "base_url": "http://gw.example", "api_key": "bad"})).json()["id"]

    tested = await client.post(f"/api/v1/connections/{cid}/test")

    assert tested.status_code == 200, tested.text
    assert tested.json()["health_state"] == "auth_error"


# --- F-17: approve status codes ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approving_a_missing_or_finished_node_is_404_or_409(client: AsyncClient):
    pid = (await client.post("/api/v1/projects", json={"name": "wf"})).json()["id"]
    nodes = [
        {"node_id": "p", "node_type": "PromptRefine", "config": {}},
        {"node_id": "gate", "node_type": "ManualApproval", "config": {}},
    ]
    edges = [{"edge_id": "e1", "source_node_id": "p", "source_port": "prompts", "target_node_id": "gate", "target_port": "in"}]
    wf = (await client.post(f"/api/v1/projects/{pid}/workflows",
                            json={"use_default_m3_template": False, "nodes": nodes, "edges": edges})).json()
    runs = f"/api/v1/projects/{pid}/workflows/{wf['id']}/runs"
    run = (await client.post(runs, json={"run_mode": "full"})).json()
    assert (await client.post(f"{runs}/{run['run_id']}/nodes/gate/approve")).status_code == 200

    assert (await client.post(f"{runs}/{run['run_id']}/nodes/nope/approve")).status_code == 404
    assert (await client.post(f"{runs}/{run['run_id']}/nodes/gate/approve")).status_code == 409


# --- F-18: Prometheus exposition ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_prometheus_output_has_one_help_and_type_per_family(client: AsyncClient):
    _pid, cid, s1, s2 = await _setup(client)
    for shot in (s1, s2):
        await client.post("/api/v1/queue/tasks", json={"shot_id": shot["id"], "connection_id": cid})
    async with STATE["maker"]() as db:
        task = (await db.execute(select(GenerationTaskRecord).where(GenerationTaskRecord.shot_id == s2["id"]))).scalar_one()
        task.state = QueueTaskState.RUNNING
        await db.commit()

    response = await client.get("/api/v1/metrics/prometheus")

    assert response.status_code == 200, response.text
    lines = response.text.splitlines()
    for prefix in ("# HELP ", "# TYPE "):
        names = [line.split()[2] for line in lines if line.startswith(prefix)]
        assert len(names) == len(set(names)), prefix
    assert 'musestudio_generation_tasks_by_state{state="running"} 1' in response.text
    assert 'musestudio_generation_tasks_by_state{state="queued"} 1' in response.text


# --- Follow-up review of PR #78 ---------------------------------------------------------------


@pytest.mark.parametrize("text, secret", [
    ("passphrase=my-secret-value", "my-secret-value"),
    ("pwd=my-secret-value", "my-secret-value"),
    ("proxy_pass=my-secret-value", "my-secret-value"),
    ("secret_pin=123456", "123456"),
    ('{"pwd": "my secret value", "max_tokens": 5}', "my secret value"),
    ("{'passphrase': 'abc def ghi'}", "abc def ghi"),
    ('{"secret_pin": 987654}', "987654"),
])
def test_text_redaction_uses_the_same_key_rules_as_mappings(text, secret):
    redacted = SecretRedactor.redact_text(text)
    assert secret not in redacted
    assert SecretRedactor.redact_dict({"payload": text})["payload"] == redacted


def test_text_redaction_keeps_non_secret_pairs():
    text = "idempotency_key=abc max_tokens=5 https://example.com/a?b=c at 12:30"
    assert SecretRedactor.redact_text(text) == text


class _BlockingGateway:
    """generate_video waits until the test releases it; records provider cancels."""

    def __init__(self):
        import asyncio

        self.sent = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled: list[str] = []

    async def generate_video(self, request):
        self.sent.set()
        await self.release.wait()
        return ProviderTask(external_task_id="ext_late", status="queued")

    async def get_task(self, task_id):
        return ProviderTaskStatus(external_task_id=task_id, status="running")

    async def cancel(self, task_id):
        from muse_orchestrator.providers.base import CancelOutcome

        self.cancelled.append(task_id)
        return CancelOutcome.CANCELLED


async def _dispatch_with_operator_action(client, monkeypatch, action: str):
    import asyncio

    _pid, cid, s1, _ = await _setup(client)
    gateway = _BlockingGateway()
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: gateway)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    worker = asyncio.create_task(_step(tid))
    await asyncio.wait_for(gateway.sent.wait(), 5)
    acted = await client.post(f"/api/v1/queue/tasks/{tid}/{action}")
    assert acted.status_code == 200, acted.text
    gateway.release.set()
    task = await asyncio.wait_for(worker, 5)
    return task, gateway


@pytest.mark.asyncio
async def test_cancel_during_dispatch_cancels_the_late_provider_job(client: AsyncClient, monkeypatch):
    task, gateway = await _dispatch_with_operator_action(client, monkeypatch, "cancel")

    assert task.state == QueueTaskState.CANCELED
    assert task.external_task_id == "ext_late"
    assert gateway.cancelled == ["ext_late"]
    assert task.history_log[-1] == "Provider cancel: cancelled"


@pytest.mark.asyncio
async def test_pause_during_dispatch_is_preserved(client: AsyncClient, monkeypatch):
    task, gateway = await _dispatch_with_operator_action(client, monkeypatch, "pause")

    assert task.state == QueueTaskState.PAUSED
    assert task.external_task_id == "ext_late"
    assert gateway.cancelled == []


@pytest.mark.asyncio
async def test_retry_failed_publishes_tasks_created_before_a_refusal(client: AsyncClient, monkeypatch):
    pid, cid, s1, s2 = await _setup(client)
    for shot in (s1, s2):
        tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": shot["id"], "connection_id": cid})).json()["id"]
        async with STATE["maker"]() as db:
            (await db.get(GenerationTaskRecord, tid)).state = QueueTaskState.FAILED
            (await db.get(ShotRecord, shot["id"])).generation_state = ShotGenerationState.FAILED
            await db.commit()
    real_enqueue = generation_queue.GenerationQueueService.enqueue_shot_generation
    calls = {"n": 0}

    async def refuse_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("blocked by a concurrent change")
        return await real_enqueue(*args, **kwargs)

    monkeypatch.setattr(generation_queue.GenerationQueueService, "enqueue_shot_generation", refuse_second)
    jobs_before = len(app.state.arq_pool.jobs)

    retried = await client.post(f"/api/v1/projects/{pid}/batch/retry-failed")

    assert retried.status_code == 422, retried.text
    detail = retried.json()["detail"]
    assert detail["code"] == "retry_partially_applied" and len(detail["new_task_ids"]) == 1
    scheduled = [job for job in app.state.arq_pool.jobs[jobs_before:] if detail["new_task_ids"][0] in str(job)]
    assert scheduled, "the task created before the refusal was not published"


@pytest.mark.asyncio
async def test_client_key_reused_with_another_prompt_is_a_conflict(client: AsyncClient):
    pid, cid, s1, _ = await _setup(client)
    body = {"shot_id": s1["id"], "connection_id": cid, "idempotency_key": "client-key-1"}
    first = await client.post("/api/v1/queue/tasks", json=body)
    assert first.status_code == 201, first.text
    async with STATE["maker"]() as db:
        (await db.get(GenerationTaskRecord, first.json()["id"])).state = QueueTaskState.SUCCEEDED
        await db.commit()
    replay = await client.post("/api/v1/queue/tasks", json=body)
    assert replay.status_code == 201 and replay.json()["id"] == first.json()["id"]

    await client.patch(f"/api/v1/projects/{pid}/shots/{s1['id']}", json={"action": "A completely different action"})
    changed = await client.post("/api/v1/queue/tasks", json=body)

    assert changed.status_code == 409, changed.text


@pytest.mark.parametrize("text, secret", [
    ('"password": "correct horse battery"', "horse battery"),
    ('"client_secret": "a,b&c"', ",b&c"),
    ("password='p}ss'", "}ss"),
    ('{"pwd": "a\\"bc", "x": 1}', "bc"),
    ('{"pwd": "unterminated secret', "unterminated secret"),
])
def test_partially_redacted_and_escaped_values_are_redacted_whole(text, secret):
    assert secret not in SecretRedactor.redact_text(text)


@pytest.mark.asyncio
async def test_question_options_keep_ordinary_key_value_labels(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _ = await _setup(client)
    gateway = _Gateway(["needs_input"])
    question = {"kind": "approval", "text": "Pick", "asked_at": None,
                "options": ["Key: C major", "tokens: 4096", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"]}

    async def ask(task_id):
        return ProviderTaskStatus(external_task_id=task_id, status="needs_input", question=question)

    gateway.get_task = ask
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: gateway)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    await _drive_until_dispatched(tid)
    task = await _step(tid)

    options = task.pending_input_json["options"]
    # Options are sent back verbatim as the operator's choice: only credential-shaped values go.
    assert options[:2] == ["Key: C major", "tokens: 4096"]
    assert "abcdefghijklmnopqrstuvwxyz" not in options[2]
    replied = await client.post(f"/api/v1/queue/tasks/{tid}/reply", json={"choice": "Key: C major"})
    assert replied.status_code == 200, replied.text
    assert gateway.replies == [{"choice": "Key: C major"}]

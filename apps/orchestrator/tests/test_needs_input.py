"""Provider asks the operator (muse2api ``needs_input``): queue state, reply API, timeouts.

A fake provider replaces ``generation_queue.build_provider``; it replays the gateway task
views documented in docs/FRAME_CHAIN_PROVIDER_CONTRACT.md. The real local gateway mock
driver is not available in CI, so no test here talks to a live muse2api.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="muse_needs_input_test_"))

from muse_orchestrator.core.database import get_db  # noqa: E402 -- env must be set before import
from muse_orchestrator.infrastructure.repositories import StudioRepository  # noqa: E402
from muse_orchestrator.main import app  # noqa: E402
from muse_orchestrator.models.domain import (  # noqa: E402
    Base,
    GenerationDispatchLeaseRecord,
    GenerationTaskRecord,
    QueueTaskState,
    ShotGenerationState,
    ShotRecord,
)
from muse_orchestrator.providers.base import (  # noqa: E402
    CancelOutcome,
    ErrorCategory,
    ProviderFailure,
    ProviderTaskStatus,
)
from muse_orchestrator.services import generation_queue  # noqa: E402
from muse_orchestrator.services.generation_queue import (  # noqa: E402
    AWAITING_INPUT_POLL_SECONDS,
    GenerationQueueService,
)

STATE: dict[str, Any] = {}
SECRET_KEY = "sk-needs-input-secret-123456"
QUESTION = {"kind": "question", "text": "Which style do you prefer?",
            "options": ["Cinematic", "Anime"], "asked_at": 1761200000.5}


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


class FakeGateway:
    """Replays gateway task views; records reply payloads."""

    def __init__(self) -> None:
        self.status = ProviderTaskStatus(external_task_id="ext_1", status="running")
        self.replies: list[tuple[str, dict[str, str]]] = []
        self.reply_error: ProviderFailure | None = None
        self.after_reply = ProviderTaskStatus(external_task_id="ext_1", status="running")
        self.cancels: list[str] = []
        # CancelOutcome to return, an exception to raise, or "hang" to never answer.
        self.cancel_result: Any = CancelOutcome.CANCELLED
        self.local_state_at_cancel: QueueTaskState | None = None
        self.cancel_task_id: str | None = None

    def ask(self, question: dict[str, Any] | None = QUESTION) -> None:
        self.status = ProviderTaskStatus(
            external_task_id="ext_1", status="needs_input",
            question=dict(question) if question is not None else None,
        )

    async def get_task(self, task_id: str) -> ProviderTaskStatus:
        return self.status

    async def cancel(self, task_id: str) -> CancelOutcome:
        self.cancels.append(task_id)
        if self.cancel_task_id is not None:
            # The local cancel must already be committed when the provider is contacted.
            async with STATE["maker"]() as db:
                self.local_state_at_cancel = (await db.get(GenerationTaskRecord, self.cancel_task_id)).state
        if self.cancel_result == "hang":
            await asyncio.sleep(3600)
        if isinstance(self.cancel_result, BaseException):
            raise self.cancel_result
        return self.cancel_result

    async def reply(self, task_id: str, answer: dict[str, str]) -> ProviderTaskStatus:
        self.replies.append((task_id, dict(answer)))
        if self.reply_error is not None:
            raise self.reply_error
        self.status = self.after_reply
        return self.after_reply


@pytest.fixture()
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    STATE["maker"] = maker
    gateway = FakeGateway()
    STATE["gateway"] = gateway
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: gateway)

    async def override_get_db():
        async with maker() as session:
            yield session

    previous_pool = getattr(app.state, "arq_pool", None)
    pool = _FakeArqPool()
    STATE["pool"] = pool
    app.state.arq_pool = pool
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
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


async def _setup(client: AsyncClient, *, max_concurrency: int = 2) -> tuple[str, str, list[dict]]:
    pid = (await client.post("/api/v1/projects", json={"name": "needs-input"})).json()["id"]
    cid = (await client.post("/api/v1/connections", json={
        "name": "mock-needs-input", "provider_type": "mock", "base_url": "http://localhost:8080",
        "api_key": SECRET_KEY, "max_concurrency": max_concurrency})).json()["id"]
    await client.post(f"/api/v1/projects/{pid}/scripts/import",
                      json={"raw_content": SCRIPT, "auto_apply_proposal": True})
    shots = (await client.get(f"/api/v1/projects/{pid}/shots")).json()
    return pid, cid, shots


async def _running_task(client: AsyncClient, **setup_kwargs) -> tuple[str, str, str]:
    """Enqueue a task and mark it dispatched (RUNNING with an external id)."""
    pid, cid, shots = await _setup(client, **setup_kwargs)
    response = await client.post("/api/v1/queue/tasks", json={"shot_id": shots[0]["id"], "connection_id": cid})
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    async with STATE["maker"]() as db:
        task = await db.get(GenerationTaskRecord, task_id)
        task.state = QueueTaskState.RUNNING
        task.external_task_id = "ext_1"
        shot = await db.get(ShotRecord, task.shot_id)
        shot.generation_state = ShotGenerationState.RUNNING
        await db.commit()
    return task_id, cid, shots[0]["id"]


async def _step(task_id: str) -> GenerationTaskRecord:
    async with STATE["maker"]() as db:
        return await GenerationQueueService.step_task(db, task_id)


async def _load(task_id: str) -> GenerationTaskRecord:
    async with STATE["maker"]() as db:
        return await db.get(GenerationTaskRecord, task_id)


async def _shot_state(shot_id: str) -> ShotGenerationState:
    async with STATE["maker"]() as db:
        return (await db.get(ShotRecord, shot_id)).generation_state


async def _awaiting_task(client: AsyncClient, **setup_kwargs) -> tuple[str, str, str]:
    task_id, cid, shot_id = await _running_task(client, **setup_kwargs)
    STATE["gateway"].ask()
    task = await _step(task_id)
    assert task.state == QueueTaskState.AWAITING_INPUT
    return task_id, cid, shot_id


# --- poll path -----------------------------------------------------------------------------


async def test_poll_needs_input_stores_question_without_consuming_polls(client: AsyncClient) -> None:
    task_id, _cid, shot_id = await _running_task(client)
    before = await _load(task_id)
    STATE["gateway"].ask()

    task = await _step(task_id)
    assert task.state == QueueTaskState.AWAITING_INPUT
    assert task.pending_input_json == QUESTION
    assert task.poll_count == before.poll_count
    assert task.next_poll_delay_seconds == AWAITING_INPUT_POLL_SECONDS
    assert any("waiting for operator input (question)" in line for line in task.history_log)
    assert await _shot_state(shot_id) == ShotGenerationState.RUNNING
    async with STATE["maker"]() as db:
        assert await db.get(GenerationDispatchLeaseRecord, task_id) is None

    # Further polls while waiting: still not counted and no duplicate history entries.
    history_len = len(task.history_log)
    again = await _step(task_id)
    assert again.state == QueueTaskState.AWAITING_INPUT
    assert again.poll_count == before.poll_count
    assert len(again.history_log) == history_len
    assert again.updated_at != task.updated_at  # new row version -> new ARQ job id

    listed = (await client.get("/api/v1/queue/tasks", params={"state": "awaiting_input"})).json()
    assert listed["total"] == 1
    assert listed["items"][0]["pending_input"] == QUESTION
    assert listed["summary"]["active_tasks"] == 1
    assert listed["summary"]["awaiting_input_tasks"] == 1
    # The summary is project-wide: a filter that hides the task does not hide the count.
    other = (await client.get("/api/v1/queue/tasks", params={"state": "failed"})).json()
    assert other["items"] == []
    assert other["summary"]["awaiting_input_tasks"] == 1


async def test_answered_elsewhere_returns_task_to_running(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _awaiting_task(client)
    STATE["gateway"].status = ProviderTaskStatus(external_task_id="ext_1", status="running")
    task = await _step(task_id)
    assert task.state == QueueTaskState.RUNNING
    assert task.pending_input_json is None


async def test_gateway_input_timeout_fails_task_non_retryably(client: AsyncClient) -> None:
    task_id, _cid, shot_id = await _awaiting_task(client)
    STATE["gateway"].status = ProviderTaskStatus(
        external_task_id="ext_1", status="failed",
        error_message="No operator answer within 30 minutes.", error_code="input_timeout",
    )
    task = await _step(task_id)
    assert task.state == QueueTaskState.FAILED
    assert task.error_category == ErrorCategory.INPUT_TIMEOUT.value == "input_timeout"
    assert task.retryable is False
    assert task.pending_input_json is None
    assert await _shot_state(shot_id) == ShotGenerationState.FAILED


async def test_other_upstream_failures_keep_generic_category(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _running_task(client)
    STATE["gateway"].status = ProviderTaskStatus(
        external_task_id="ext_1", status="failed", error_message="boom", error_code="driver_error",
    )
    task = await _step(task_id)
    assert task.error_category == "upstream_generation_failed"


# --- reply API ------------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [{"text": "Make it cinematic please"}, {"choice": "Anime"}])
async def test_reply_happy_path_sends_answer_and_schedules_next_step(client: AsyncClient, payload) -> None:
    task_id, _cid, shot_id = await _awaiting_task(client)
    pool: _FakeArqPool = STATE["pool"]
    jobs_before = len(pool.jobs)

    response = await client.post(f"/api/v1/queue/tasks/{task_id}/reply", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "running"
    assert body["pending_input"] is None
    assert STATE["gateway"].replies == [("ext_1", payload)]
    (kind, value), = payload.items()
    assert f"Operator answered ({kind}): {value}" in body["history_log"]
    assert len(pool.jobs) == jobs_before + 1
    function, args, _kwargs = pool.jobs[-1]
    assert function == "process_generation_task_step" and args == (task_id,)
    assert await _shot_state(shot_id) == ShotGenerationState.RUNNING


async def test_reply_redacts_secrets_in_history(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _awaiting_task(client)
    response = await client.post(
        f"/api/v1/queue/tasks/{task_id}/reply", json={"text": f"my key is {SECRET_KEY}"}
    )
    assert response.status_code == 200, response.text
    assert SECRET_KEY not in str(response.json()["history_log"])
    # The provider itself still receives the operator's literal answer.
    assert STATE["gateway"].replies[0][1]["text"].endswith(SECRET_KEY)


async def test_follow_up_question_keeps_task_awaiting(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _awaiting_task(client)
    follow_up = {"kind": "approval", "text": "Render 10 seconds?", "options": ["Yes", "No"], "asked_at": 2.0}
    STATE["gateway"].after_reply = ProviderTaskStatus(
        external_task_id="ext_1", status="needs_input", question=follow_up,
    )
    body = (await client.post(f"/api/v1/queue/tasks/{task_id}/reply", json={"choice": "Anime"})).json()
    assert body["state"] == "awaiting_input"
    assert body["pending_input"] == follow_up


@pytest.mark.parametrize("payload", [{}, {"text": "a", "choice": "Anime"}, {"text": ""},
                                     {"text": "x" * 4001}, {"choice": "c" * 201}])
async def test_reply_rejects_malformed_payloads(client: AsyncClient, payload) -> None:
    task_id, _cid, _shot = await _awaiting_task(client)
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/reply", json=payload)
    assert response.status_code == 422, response.text
    assert STATE["gateway"].replies == []


async def test_reply_choice_must_be_a_pending_option(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _awaiting_task(client)
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/reply", json={"choice": "Watercolor"})
    assert response.status_code == 422, response.text
    assert STATE["gateway"].replies == []
    assert (await _load(task_id)).state == QueueTaskState.AWAITING_INPUT


async def test_reply_to_task_not_awaiting_is_409(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _running_task(client)
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/reply", json={"text": "hi"})
    assert response.status_code == 409, response.text
    assert response.json()["category"] == "input_not_pending"
    assert STATE["gateway"].replies == []


async def test_reply_to_unknown_task_is_404(client: AsyncClient) -> None:
    response = await client.post("/api/v1/queue/tasks/tsk_missing/reply", json={"text": "hi"})
    assert response.status_code == 404, response.text


async def test_provider_conflict_maps_to_409_and_refreshes_state(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _awaiting_task(client)
    gateway: FakeGateway = STATE["gateway"]
    gateway.reply_error = ProviderFailure(ErrorCategory.INPUT_NOT_PENDING, "not waiting", 409, False)
    # The gateway already moved on (answered elsewhere): the refresh step picks it up.
    gateway.status = ProviderTaskStatus(external_task_id="ext_1", status="running")
    pool: _FakeArqPool = STATE["pool"]
    jobs_before = len(pool.jobs)

    response = await client.post(f"/api/v1/queue/tasks/{task_id}/reply", json={"text": "hi"})
    assert response.status_code == 409, response.text
    assert response.json()["category"] == "input_not_pending"
    assert response.json()["retryable"] is False
    task = await _load(task_id)
    assert task.state == QueueTaskState.RUNNING
    assert task.pending_input_json is None
    assert len(pool.jobs) == jobs_before + 1  # step chain kept alive


async def test_other_provider_reply_failures_use_error_envelope(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _awaiting_task(client)
    STATE["gateway"].reply_error = ProviderFailure(
        ErrorCategory.UPSTREAM_5XX, f"gateway down {SECRET_KEY}", 503, True
    )
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/reply", json={"text": "hi"})
    assert response.status_code == 502, response.text
    assert response.json()["category"] == "upstream_5xx"
    assert SECRET_KEY not in response.text
    assert (await _load(task_id)).state == QueueTaskState.AWAITING_INPUT


# --- active-slot rules ----------------------------------------------------------------------


async def test_awaiting_task_keeps_the_one_active_task_per_shot_slot(client: AsyncClient) -> None:
    task_id, cid, shot_id = await _awaiting_task(client)
    again = await client.post("/api/v1/queue/tasks", json={"shot_id": shot_id, "connection_id": cid})
    assert again.status_code == 201, again.text
    assert again.json()["id"] == task_id and again.json()["created_new"] is False

    template = await _load(task_id)
    with pytest.raises(IntegrityError):
        async with STATE["maker"]() as db:
            await db.execute(insert(GenerationTaskRecord).values(
                id="tsk_dup", project_id=template.project_id, shot_id=shot_id,
                connection_id=cid, task_type="video", model_id="m", idempotency_key="idem_dup",
                state=QueueTaskState.QUEUED,
            ))
            await db.commit()


async def test_awaiting_task_occupies_a_connection_concurrency_slot(client: AsyncClient) -> None:
    task_id, cid, _shot = await _awaiting_task(client, max_concurrency=1)
    template = await _load(task_id)
    async with STATE["maker"]() as db:
        other_shot = (await db.execute(
            select(ShotRecord).where(ShotRecord.project_id == template.project_id, ShotRecord.id != template.shot_id)
        )).scalars().first()
    response = await client.post("/api/v1/queue/tasks", json={"shot_id": other_shot.id, "connection_id": cid})
    second = await _step(response.json()["id"])
    assert second.state == QueueTaskState.QUEUED
    assert second.external_task_id is None
    assert "Waiting for connection concurrency slot (1/1 busy)." in second.history_log[-1]


async def test_cancel_awaiting_task_is_allowed_and_pause_is_not(client: AsyncClient) -> None:
    task_id, _cid, shot_id = await _awaiting_task(client)
    paused = await client.post(f"/api/v1/queue/tasks/{task_id}/pause")
    assert paused.status_code == 409, paused.text

    canceled = await client.post(f"/api/v1/queue/tasks/{task_id}/cancel")
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["state"] == "canceled"
    assert canceled.json()["pending_input"] is None
    assert await _shot_state(shot_id) == ShotGenerationState.CANCELED

    again = await client.post(f"/api/v1/queue/tasks/{task_id}/cancel")
    assert again.status_code == 409
    missing = await client.post("/api/v1/queue/tasks/tsk_missing/cancel")
    assert missing.status_code == 404


# --- cancel propagation -------------------------------------------------------------------


async def test_cancel_awaiting_task_propagates_to_provider_after_local_commit(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _awaiting_task(client)
    gateway: FakeGateway = STATE["gateway"]
    gateway.cancel_task_id = task_id
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/cancel")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "canceled"
    assert gateway.cancels == ["ext_1"]
    assert gateway.local_state_at_cancel == QueueTaskState.CANCELED
    assert body["history_log"][-2:] == ["Canceled by operator.", "Provider cancel: cancelled"]


@pytest.mark.parametrize(("result", "label"), [
    (CancelOutcome.UNSUPPORTED, "unsupported"),
    (CancelOutcome.ALREADY_FINISHED, "already finished"),
    (CancelOutcome.NOT_FOUND, "not found"),
    (ProviderFailure(ErrorCategory.UPSTREAM_5XX, "boom", 502, True), "failed (upstream_5xx)"),
    (ProviderFailure(ErrorCategory.AUTH_FAILED, "bad key", 401, False), "failed (auth_failed)"),
    (RuntimeError("unexpected"), "failed (RuntimeError)"),
])
async def test_cancel_running_task_records_provider_outcome(client: AsyncClient, result, label) -> None:
    task_id, _cid, shot_id = await _running_task(client)
    STATE["gateway"].cancel_result = result
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "canceled"
    assert response.json()["history_log"][-1] == f"Provider cancel: {label}"
    assert (await _load(task_id)).state == QueueTaskState.CANCELED
    assert await _shot_state(shot_id) == ShotGenerationState.CANCELED


async def test_provider_cancel_failure_is_logged_redacted(client: AsyncClient, caplog) -> None:
    task_id, _cid, _shot = await _running_task(client)
    STATE["gateway"].cancel_result = ProviderFailure(
        ErrorCategory.UPSTREAM_5XX, f"gateway echoed {SECRET_KEY}", 500, True
    )
    with caplog.at_level(logging.WARNING, logger="muse_orchestrator.services.generation_queue"):
        response = await client.post(f"/api/v1/queue/tasks/{task_id}/cancel")
    assert response.status_code == 200
    assert "Provider cancel for task" in caplog.text
    assert SECRET_KEY not in caplog.text
    assert SECRET_KEY not in str(response.json())


async def test_provider_cancel_timeout_does_not_block_the_local_cancel(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(generation_queue, "PROVIDER_CANCEL_TIMEOUT_SECONDS", 0.05)
    task_id, _cid, _shot = await _running_task(client)
    STATE["gateway"].cancel_result = "hang"
    response = await asyncio.wait_for(client.post(f"/api/v1/queue/tasks/{task_id}/cancel"), timeout=5)
    assert response.status_code == 200
    assert response.json()["state"] == "canceled"
    assert response.json()["history_log"][-1] == "Provider cancel: failed (timeout)"


async def test_provider_without_cancel_capability_is_recorded_as_unsupported(
    client: AsyncClient, monkeypatch
) -> None:
    task_id, _cid, _shot = await _running_task(client)

    class NoCancel:
        async def get_task(self, task_id: str) -> ProviderTaskStatus:
            return ProviderTaskStatus(external_task_id=task_id, status="running")

    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: NoCancel())
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/cancel")
    assert response.status_code == 200
    assert response.json()["history_log"][-1] == "Provider cancel: unsupported"


async def test_cancel_without_provider_job_does_not_contact_provider(client: AsyncClient) -> None:
    pid, cid, shots = await _setup(client)
    task_id = (await client.post(
        "/api/v1/queue/tasks", json={"shot_id": shots[0]["id"], "connection_id": cid}
    )).json()["id"]
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/cancel")
    assert response.status_code == 200
    assert STATE["gateway"].cancels == []
    assert not any(line.startswith("Provider cancel:") for line in response.json()["history_log"])


async def test_cancel_paused_task_stays_local(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _running_task(client)
    assert (await client.post(f"/api/v1/queue/tasks/{task_id}/pause")).status_code == 200
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/cancel")
    assert response.status_code == 200
    assert STATE["gateway"].cancels == []


async def test_polled_upstream_cancel_is_terminal_and_not_a_generation_failure(client: AsyncClient) -> None:
    task_id, _cid, shot_id = await _awaiting_task(client)
    STATE["gateway"].status = ProviderTaskStatus(
        external_task_id="ext_1", status="failed",
        error_message="cancelled by client", error_code="cancelled",
    )
    task = await _step(task_id)
    assert task.state == QueueTaskState.FAILED
    assert task.error_category == ErrorCategory.UPSTREAM_CANCELLED.value == "upstream_cancelled"
    assert task.retryable is False
    assert task.pending_input_json is None
    assert await _shot_state(shot_id) == ShotGenerationState.FAILED


async def test_recovery_reschedules_awaiting_tasks(client: AsyncClient) -> None:
    task_id, _cid, _shot = await _awaiting_task(client)
    async with STATE["maker"]() as db:
        tasks = await StudioRepository(db).list_active_generation_tasks_for_recovery(10)
    assert task_id in {task.id for task in tasks}

    from muse_orchestrator.services.arq_dispatch import enqueue_generation_step

    pool = _FakeArqPool()
    assert await enqueue_generation_step(pool, await _load(task_id)) is True
    assert pool.jobs[0][2]["_defer_by"] == AWAITING_INPUT_POLL_SECONDS

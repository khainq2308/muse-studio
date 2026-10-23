"""Regression tests for the third external review (F-01..F-10 confirmed findings)."""
# The `client` fixture is imported from test_queue_review2; test parameters reuse its name.
# ruff: noqa: F811
from __future__ import annotations

import struct
import zlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.models.domain import (
    GenerationDispatchLeaseRecord,
    QueueTaskState,
    ShotRecord,
)
from muse_orchestrator.providers.base import ProviderTask, ProviderTaskStatus
from muse_orchestrator.services import generation_queue
from muse_orchestrator.services.backup_service import BackupService
from muse_orchestrator.services.generation_guards import upstream_selected_last_frame
from tests.test_queue_review2 import (  # noqa: F401 - `client` is a pytest fixture
    STATE,
    _chain_and_render,
    _drive_until_dispatched,
    _drive_until_done,
    _setup,
    _shots,
    _step,
    _tasks_for_shot,
    client,
)


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")


class _RecordingProvider:
    """Accepts dispatches and records the requests; polls stay running."""

    def __init__(self, *, supports_video_references: bool | None = None, question: dict | None = None):
        self.requests: list = []
        self.question = question
        if supports_video_references is not None:
            self.supports_video_references = supports_video_references

    async def generate_video(self, request):
        self.requests.append(request)
        return ProviderTask(external_task_id=f"ext_v{len(self.requests)}", status="queued")

    async def generate_image(self, request):
        self.requests.append(request)
        return ProviderTask(external_task_id=f"ext_i{len(self.requests)}", status="queued")

    async def get_task(self, task_id):
        if self.question is not None:
            return ProviderTaskStatus(external_task_id=task_id, status="needs_input", question=self.question)
        return ProviderTaskStatus(external_task_id=task_id, status="running")


# --- F-01: hard kill mid-dispatch -----------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_dispatch_lease_without_external_id_is_not_redispatched(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _ = await _setup(client)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    provider = _RecordingProvider()
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: provider)
    # State left behind by a worker killed after taking the dispatch lease and (maybe)
    # sending the request, before the external id was stored.
    async with STATE["maker"]() as db:
        task = await db.get(generation_queue.GenerationTaskRecord, tid)
        task.state = QueueTaskState.RUNNING
        task.attempt_count = 1
        db.add(GenerationDispatchLeaseRecord(
            task_id=tid, connection_id=cid, expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        ))
        await db.commit()

    task = await _step(tid)

    assert provider.requests == []
    assert task.state == QueueTaskState.FAILED
    assert task.error_category == "dispatch_outcome_unknown"
    assert task.retryable is False
    assert "Dispatch lease expired" in task.history_log[-1]
    async with STATE["maker"]() as db:
        assert await db.get(GenerationDispatchLeaseRecord, tid) is None


# --- F-02 / F-04: idempotency identity --------------------------------------------------------


@pytest.mark.asyncio
async def test_client_idempotency_key_reused_for_another_shot_is_a_conflict(client: AsyncClient):
    pid, cid, s1, s2 = await _setup(client)
    first = await client.post(
        "/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid, "idempotency_key": "shared-key"}
    )
    assert first.status_code == 201, first.text

    reused = await client.post(
        "/api/v1/queue/tasks", json={"shot_id": s2["id"], "connection_id": cid, "idempotency_key": "shared-key"}
    )

    assert reused.status_code == 409, reused.text
    assert await _tasks_for_shot(s2["id"]) == []
    assert (await _shots(client, pid))[s2["id"]]["generation_state"] == "not_started"


@pytest.mark.asyncio
async def test_same_request_on_another_connection_is_a_new_generation(client: AsyncClient):
    pid, cid, s1, s2 = await _setup(client)
    for shot in (s1, s2):
        tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": shot["id"], "connection_id": cid})).json()["id"]
        assert (await _drive_until_done(tid)).state == QueueTaskState.SUCCEEDED
    other = (await client.post("/api/v1/connections", json={
        "name": "mock-other", "provider_type": "mock", "base_url": "http://localhost:8081"})).json()["id"]

    # Batch "only changed" skips the selected take only on the connection that rendered it.
    same = (await client.post(f"/api/v1/projects/{pid}/batch/launch", json={
        "connection_id": cid, "shot_ids": [s1["id"]], "only_changed": True})).json()
    assert same["skipped_shot_ids"] == [s1["id"]]
    switched = (await client.post(f"/api/v1/projects/{pid}/batch/launch", json={
        "connection_id": other, "shot_ids": [s1["id"]], "only_changed": True})).json()
    assert switched["skipped_shot_ids"] == [] and len(switched["enqueued_task_ids"]) == 1

    # Direct enqueue: the default key includes the connection, so the old task is not reused.
    again = (await client.post("/api/v1/queue/tasks", json={"shot_id": s2["id"], "connection_id": other})).json()
    assert again["created_new"] is True and again["connection_id"] == other


# --- F-03: upstream re-selected while the downstream provider job runs -------------------------


@pytest.mark.asyncio
async def test_downstream_finishing_on_a_superseded_frame_is_not_selected(client: AsyncClient):
    pid, cid, s1, s2 = await _chain_and_render(client)
    old_frame = (await _shots(client, pid))[s2["id"]]["first_frame_asset_id"]
    s2_takes_before = (await client.get(f"/api/v1/queue/shots/{s2['id']}/takes")).json()

    rerender = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s2["id"], "connection_id": cid, "force_retry": True})).json()
    assert rerender["created_new"] is True
    assert (await _drive_until_dispatched(rerender["id"])).external_task_id

    # While shot 2 renders on the old frame, the operator selects a new shot 1 take.
    upstream = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "force_retry": True})).json()
    assert (await _drive_until_done(upstream["id"])).state == QueueTaskState.SUCCEEDED
    new_take = next(t for t in (await client.get(f"/api/v1/queue/shots/{s1['id']}/takes")).json()
                    if t["task_id"] == upstream["id"])
    selected = await client.post(f"/api/v1/queue/shots/{s1['id']}/takes/{new_take['id']}/select")
    assert selected.status_code == 200, selected.text
    new_frame = (await _shots(client, pid))[s1["id"]]["last_frame_asset_id"]
    assert new_frame and new_frame != old_frame

    done = await _drive_until_done(rerender["id"])

    assert done.state == QueueTaskState.SUCCEEDED  # the paid output is kept as a take
    assert any(line.startswith("Stale continuity") for line in done.history_log)
    shot = (await _shots(client, pid))[s2["id"]]
    assert shot["generation_state"] == "not_started"
    assert shot["first_frame_asset_id"] == new_frame
    assert shot["selected_take_id"] == s2_takes_before[0]["id"]
    takes = (await client.get(f"/api/v1/queue/shots/{s2['id']}/takes")).json()
    assert len(takes) == len(s2_takes_before) + 1


# --- F-10: dangling upstream selection --------------------------------------------------------


@pytest.mark.asyncio
async def test_dangling_selected_take_does_not_fall_back_to_an_old_frame(client: AsyncClient):
    pid, _cid, s1, s2 = await _chain_and_render(client)
    async with STATE["maker"]() as db:
        upstream = await db.get(ShotRecord, s1["id"])
        downstream = await db.get(ShotRecord, s2["id"])
        assert upstream.last_frame_asset_id
        assert await upstream_selected_last_frame(db, downstream, upstream) == upstream.last_frame_asset_id
        upstream.selected_take_id = "tke_missing"
        await db.commit()
        assert await upstream_selected_last_frame(db, downstream, upstream) is None


# --- F-05: reference images reach the provider --------------------------------------------------


async def _shot_with_reference(client: AsyncClient):
    pid, cid, s1, _ = await _setup(client)
    upload = await client.post(
        "/api/v1/assets/upload",
        data={"project_id": pid, "asset_type": "reference"},
        files={"file": ("ref.png", _png(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["id"]
    patched = await client.patch(f"/api/v1/projects/{pid}/shots/{s1['id']}", json={"reference_asset_ids": [asset_id]})
    assert patched.status_code == 200, patched.text
    return pid, cid, s1


@pytest.mark.asyncio
async def test_reference_assets_are_sent_as_data_uris(client: AsyncClient, monkeypatch):
    _pid, cid, s1 = await _shot_with_reference(client)
    provider = _RecordingProvider()
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: provider)
    tid = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "task_type": "image"})).json()["id"]

    task = await _drive_until_dispatched(tid)

    assert task.external_task_id == "ext_i1"
    (request,) = provider.requests
    assert len(request.reference_image_urls) == 1
    assert request.reference_image_urls[0].startswith("data:image/png;base64,")
    assert "Reference images sent: 1." in task.history_log


@pytest.mark.asyncio
async def test_video_references_on_a_provider_without_the_field_are_reported(client: AsyncClient, monkeypatch):
    _pid, cid, s1 = await _shot_with_reference(client)
    provider = _RecordingProvider(supports_video_references=False)
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: provider)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]

    task = await _drive_until_dispatched(tid)

    assert provider.requests[0].reference_image_urls == []
    assert any(line.startswith("Reference images not sent") for line in task.history_log)


@pytest.mark.asyncio
async def test_unreadable_reference_fails_closed_before_dispatch(client: AsyncClient, monkeypatch):
    pid, cid, s1 = await _shot_with_reference(client)
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, s1["id"])
        shot.reference_asset_ids = ["ast_missing"]
        await db.commit()
    provider = _RecordingProvider()
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: provider)
    tid = (await client.post("/api/v1/queue/tasks", json={
        "shot_id": s1["id"], "connection_id": cid, "task_type": "image"})).json()["id"]

    task = await _drive_until_dispatched(tid)

    assert provider.requests == []
    assert task.state == QueueTaskState.FAILED
    assert task.retryable is False


def test_muse2api_www222fff_video_payload_drops_references(monkeypatch):
    from muse_orchestrator.core.config import settings
    from muse_orchestrator.providers.base import VideoGenerationRequest
    from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter

    monkeypatch.setattr(settings, "video_payload_size", "16:9")
    request = VideoGenerationRequest(shot_id="s", prompt="p", reference_image_urls=["data:image/png;base64,AA=="])
    adapter = Muse2ApiAdapter("http://muse2api.test", "k")
    monkeypatch.setattr(settings, "video_payload_profile", "www222fff")
    assert adapter.supports_video_references is False
    assert "reference_images" not in adapter._build_video_payload(request)
    monkeypatch.setattr(settings, "video_payload_profile", "czg86389-hub")
    assert adapter._build_video_payload(request)["reference_images"] == request.reference_image_urls


# --- F-07: secrets in question options --------------------------------------------------------


@pytest.mark.asyncio
async def test_question_options_are_redacted(client: AsyncClient, monkeypatch):
    _pid, cid, s1, _ = await _setup(client)
    secret_option = "Use Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    provider = _RecordingProvider(question={
        "kind": "approval", "text": "Allow?", "options": ["Allow", secret_option], "asked_at": None,
    })
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: provider)
    tid = (await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})).json()["id"]
    await _drive_until_dispatched(tid)

    task = await _step(tid)

    assert task.state == QueueTaskState.AWAITING_INPUT
    options = task.pending_input_json["options"]
    assert options[0] == "Allow"
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in options[1]
    listed = (await client.get("/api/v1/queue/tasks", params={"state": "awaiting_input"})).json()
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in str(listed)


# --- F-09: restore continuity graph ----------------------------------------------------------


def test_restore_rejects_a_previous_shot_cycle():
    shots = [
        ({}, SimpleNamespace(id="a", previous_shot_id="c")),
        ({}, SimpleNamespace(id="b", previous_shot_id="a")),
        ({}, SimpleNamespace(id="c", previous_shot_id="b")),
    ]
    with pytest.raises(ApplicationError) as excinfo:
        BackupService._validate_shot_continuity_graph({"shots": shots})
    assert excinfo.value.status_code == 422

    chain = [
        ({}, SimpleNamespace(id="a", previous_shot_id=None)),
        ({}, SimpleNamespace(id="b", previous_shot_id="a")),
        ({}, SimpleNamespace(id="c", previous_shot_id="b")),
    ]
    BackupService._validate_shot_continuity_graph({"shots": chain})

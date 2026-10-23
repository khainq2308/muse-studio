"""Regression tests for the review follow-ups (asset deletion vs active exports, lease
owner tokens, redaction of unquoted values with spaces and Shot Board take API gaps)."""
# The `client` fixture is imported from test_queue_review2; test parameters reuse its name.
# ruff: noqa: F811
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient

from muse_orchestrator.application import queue_service
from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import (
    Asset,
    ExportJobRecord,
    ExportJobState,
    GenerationDispatchLeaseRecord,
    GenerationTaskRecord,
    Project,
    QueueTaskState,
    TimelineClipRecord,
    TimelineSequenceRecord,
    TimelineTrackRecord,
)
from muse_orchestrator.providers.base import (
    ErrorCategory,
    ProviderFailure,
    ProviderTask,
    ProviderTaskStatus,
)
from muse_orchestrator.services import generation_queue
from muse_orchestrator.services.asset_storage import LocalAssetStorage
from tests.test_queue_review2 import (  # noqa: F401 - `client` is a pytest fixture
    STATE,
    _drive_until_dispatched,
    _setup,
    _step,
    client,
)


# --- 1. Asset deletion vs queued/running exports ----------------------------------------------


async def _asset_with_file(pid: str, asset_id: str) -> Path:
    storage = LocalAssetStorage(settings.media_root)
    rel = f"{pid}/followups/{asset_id}.mp4"
    path = storage.root_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    async with STATE["maker"]() as db:
        db.add(Asset(id=asset_id, project_id=pid, asset_type="video", filename=f"{asset_id}.mp4",
                     storage_rel_path=rel, mime_type="video/mp4", size_bytes=12, sha256="0" * 64))
        await db.commit()
    return storage.resolve_safe_path(rel)


async def _export_using(pid: str, export_id: str, seq_id: str, path: Path, state: ExportJobState) -> None:
    async with STATE["maker"]() as db:
        if await db.get(TimelineSequenceRecord, seq_id) is None:
            db.add(TimelineSequenceRecord(id=seq_id, project_id=pid))
            await db.flush()
        db.add(ExportJobRecord(
            id=export_id, project_id=pid, sequence_id=seq_id, state=state,
            # Same shape as api/timeline.py launch_ffmpeg_export: absolute resolved paths.
            manifest_snapshot_json={"resolved_clips": [{"path": str(path), "effective_duration": 1.0}]},
        ))
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [ExportJobState.QUEUED, ExportJobState.RUNNING])
async def test_asset_used_by_an_active_export_cannot_be_deleted_even_with_force(client: AsyncClient, state):
    pid, *_ = await _setup(client)
    path = await _asset_with_file(pid, "ast_in")
    await _export_using(pid, "exp_active", "seq_x", path, state)

    for params in ({}, {"force": "true"}):
        refused = await client.delete("/api/v1/assets/ast_in", params=params)
        assert refused.status_code == 409, refused.text
        body = refused.json()
        assert "exp_active" in body["detail"] and "active export" in body["detail"]
        assert body["retryable"] is True
    assert path.is_file()
    async with STATE["maker"]() as db:
        assert await db.get(Asset, "ast_in") is not None

    # Once the export has finished the regular (force) deletion rules apply again.
    async with STATE["maker"]() as db:
        job = await db.get(ExportJobRecord, "exp_active")
        job.state = ExportJobState.FAILED
        await db.commit()
    assert (await client.delete("/api/v1/assets/ast_in")).status_code == 204
    assert not path.is_file()


@pytest.mark.asyncio
async def test_finished_or_unrelated_exports_do_not_block_deletion(client: AsyncClient):
    pid, *_ = await _setup(client)
    path = await _asset_with_file(pid, "ast_done")
    other = await _asset_with_file(pid, "ast_other")
    await _export_using(pid, "exp_done", "seq_y", path, ExportJobState.SUCCEEDED)
    await _export_using(pid, "exp_other", "seq_y", other, ExportJobState.RUNNING)
    assert (await client.delete("/api/v1/assets/ast_done")).status_code == 204


@pytest.mark.asyncio
async def test_timeline_reference_search_is_scoped_to_the_assets_project(client: AsyncClient):
    pid, *_ = await _setup(client)
    await _asset_with_file(pid, "ast_scope")
    async with STATE["maker"]() as db:
        db.add(Project(id="prj_foreign", name="foreign"))
        await db.flush()
        for seq_id, project_id in (("seq_own", pid), ("seq_foreign", "prj_foreign")):
            db.add(TimelineSequenceRecord(id=seq_id, project_id=project_id))
            await db.flush()
            db.add(TimelineTrackRecord(id=f"trk_{seq_id}", sequence_id=seq_id,
                                       clips_json=[{"asset_id": "ast_scope"}]))
            await db.flush()
            db.add(TimelineClipRecord(id=f"clp_{seq_id}", sequence_id=seq_id, track_id=f"trk_{seq_id}",
                                      asset_id="ast_scope", order_index=0))
        await db.commit()

    refused = await client.delete("/api/v1/assets/ast_scope")
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "clp_seq_own" in detail and "trk_seq_own" in detail
    # A cross-project row cannot be created through the API (assembly validates ownership);
    # clips and legacy track lists now apply the same project scope.
    assert "clp_seq_foreign" not in detail and "trk_seq_foreign" not in detail

    assert (await client.delete("/api/v1/assets/ast_scope", params={"force": "true"})).status_code == 204
    async with STATE["maker"]() as db:
        assert (await db.get(TimelineClipRecord, "clp_seq_own")).asset_id is None
        assert (await db.get(TimelineTrackRecord, "trk_seq_own")).clips_json[0]["asset_id"] is None


# --- 2. Lease owner tokens: a stale holder never deletes a newer holder's lease ----------------

NEWER = "newer-holder-token"
QUESTION = {"kind": "question", "text": "Which mood?", "options": [], "asked_at": None}


async def _take_over_lease(task_id: str) -> None:
    """What a second worker/reply does once the first holder's lease expired mid-call."""
    async with STATE["maker"]() as db:
        task = await db.get(GenerationTaskRecord, task_id)
        lease = await db.get(GenerationDispatchLeaseRecord, task_id)
        assert lease is not None and lease.owner_token not in (None, NEWER)
        await db.delete(lease)
        await db.flush()
        db.add(GenerationDispatchLeaseRecord(
            task_id=task_id, connection_id=task.connection_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), owner_token=NEWER,
        ))
        await db.commit()


async def _lease_token(task_id: str) -> str | None:
    async with STATE["maker"]() as db:
        lease = await db.get(GenerationDispatchLeaseRecord, task_id)
        return None if lease is None else lease.owner_token


class _TakeoverGateway:
    """Provider whose next call outlives the caller's lease: another holder takes it over."""

    def __init__(self, poll_statuses: list[str]) -> None:
        self.poll_statuses = list(poll_statuses)
        self.take_over_on: set[str] = set()
        self.task_id: str | None = None
        self.raise_on: dict[str, BaseException] = {}

    async def _maybe_take_over(self, call: str) -> None:
        if call in self.take_over_on:
            self.take_over_on.discard(call)
            await _take_over_lease(self.task_id)
        if call in self.raise_on:
            raise self.raise_on.pop(call)

    async def generate_video(self, request):
        await self._maybe_take_over("generate")
        return ProviderTask(external_task_id="ext_stale", status="queued")

    async def get_task(self, task_id):
        await self._maybe_take_over("poll")
        status = self.poll_statuses.pop(0) if len(self.poll_statuses) > 1 else self.poll_statuses[0]
        return ProviderTaskStatus(
            external_task_id=task_id, status=status,
            question=QUESTION if status == "needs_input" else None,
            error_message="upstream said no" if status == "failed" else None,
        )

    async def reply(self, task_id, answer):
        await self._maybe_take_over("reply")
        return ProviderTaskStatus(external_task_id=task_id, status="running")


async def _task_with_gateway(client: AsyncClient, monkeypatch, poll_statuses: list[str]):
    _pid, cid, s1, _ = await _setup(client)
    gateway = _TakeoverGateway(poll_statuses)
    monkeypatch.setattr(generation_queue, "build_provider", lambda conn, account=None: gateway)
    created = await client.post("/api/v1/queue/tasks", json={"shot_id": s1["id"], "connection_id": cid})
    gateway.task_id = created.json()["id"]
    return gateway.task_id, gateway


@pytest.mark.asyncio
async def test_stale_dispatch_holder_keeps_the_newer_lease(client: AsyncClient, monkeypatch):
    tid, gateway = await _task_with_gateway(client, monkeypatch, ["running"])
    gateway.take_over_on.add("generate")
    task = await _drive_until_dispatched(tid)
    assert task.external_task_id == "ext_stale"
    assert await _lease_token(tid) == NEWER


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "failed", "needs_input"])
async def test_stale_poll_holder_keeps_the_newer_lease(client: AsyncClient, monkeypatch, status):
    tid, gateway = await _task_with_gateway(client, monkeypatch, [status])
    await _drive_until_dispatched(tid)
    assert await _lease_token(tid) is None  # the dispatch released its own lease
    gateway.take_over_on.add("poll")
    await _step(tid)
    assert await _lease_token(tid) == NEWER


@pytest.mark.asyncio
async def test_stale_poll_failure_holder_keeps_the_newer_lease(client: AsyncClient, monkeypatch):
    tid, gateway = await _task_with_gateway(client, monkeypatch, ["running"])
    await _drive_until_dispatched(tid)
    gateway.take_over_on.add("poll")
    gateway.raise_on["poll"] = ProviderFailure(ErrorCategory.UPSTREAM_5XX, "gateway down", 503, True)
    await _step(tid)
    assert await _lease_token(tid) == NEWER


@pytest.mark.asyncio
async def test_cancelled_step_keeps_the_newer_lease(client: AsyncClient, monkeypatch):
    tid, gateway = await _task_with_gateway(client, monkeypatch, ["running"])
    await _drive_until_dispatched(tid)
    gateway.take_over_on.add("poll")
    gateway.raise_on["poll"] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await _step(tid)
    assert await _lease_token(tid) == NEWER


@pytest.mark.asyncio
async def test_cancelled_step_still_releases_its_own_lease(client: AsyncClient, monkeypatch):
    tid, gateway = await _task_with_gateway(client, monkeypatch, ["running"])
    await _drive_until_dispatched(tid)
    gateway.raise_on["poll"] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await _step(tid)
    assert await _lease_token(tid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "provider_failure", "unexpected"])
async def test_stale_reply_holder_keeps_the_newer_lease(client: AsyncClient, monkeypatch, outcome):
    tid, gateway = await _task_with_gateway(client, monkeypatch, ["needs_input"])
    await _drive_until_dispatched(tid)
    assert (await _step(tid)).state == QueueTaskState.AWAITING_INPUT
    # The API retries a busy reply; keep it to one attempt so the newer lease is observable.
    monkeypatch.setattr(queue_service, "_REPLY_BUSY_ATTEMPTS", 1)
    gateway.take_over_on.add("reply")
    if outcome == "provider_failure":
        gateway.raise_on["reply"] = ProviderFailure(ErrorCategory.UPSTREAM_5XX, "gateway down", 503, True)
    elif outcome == "unexpected":
        gateway.raise_on["reply"] = RuntimeError("boom")

    if outcome == "unexpected":
        with pytest.raises(RuntimeError):
            await client.post(f"/api/v1/queue/tasks/{tid}/reply", json={"text": "calm"})
    else:
        await client.post(f"/api/v1/queue/tasks/{tid}/reply", json={"text": "calm"})
    assert await _lease_token(tid) == NEWER


@pytest.mark.asyncio
async def test_expired_lease_of_another_owner_is_still_taken_over(client: AsyncClient, monkeypatch):
    tid, gateway = await _task_with_gateway(client, monkeypatch, ["running"])
    await _drive_until_dispatched(tid)
    async with STATE["maker"]() as db:
        task = await db.get(GenerationTaskRecord, tid)
        db.add(GenerationDispatchLeaseRecord(
            task_id=tid, connection_id=task.connection_id, owner_token="dead-worker",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        await db.commit()
    polled = await _step(tid)
    assert polled.state == QueueTaskState.RUNNING
    assert any("Poll #" in line for line in polled.history_log)
    assert await _lease_token(tid) is None


@pytest.mark.asyncio
async def test_release_owned_lease_only_matches_the_owner_token(client: AsyncClient, monkeypatch):
    tid, _gateway = await _task_with_gateway(client, monkeypatch, ["running"])
    async with STATE["maker"]() as db:
        task = await db.get(GenerationTaskRecord, tid)
        db.add(GenerationDispatchLeaseRecord(
            task_id=tid, connection_id=task.connection_id, owner_token="mine",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        ))
        await db.commit()
    for token in (None, "", "someone-else"):
        async with STATE["maker"]() as db:
            await generation_queue.release_owned_lease(db, tid, token)
            await db.commit()
        assert await _lease_token(tid) == "mine"
    async with STATE["maker"]() as db:
        await generation_queue.release_owned_lease(db, tid, "mine")
        await db.commit()
    assert await _lease_token(tid) is None

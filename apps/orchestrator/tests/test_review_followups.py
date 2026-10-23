"""Regression tests for the review follow-ups (asset deletion vs active exports, lease
owner tokens, redaction of unquoted values with spaces and Shot Board take API gaps)."""
# The `client` fixture is imported from test_queue_review2; test parameters reuse its name.
# ruff: noqa: F811
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import (
    Asset,
    ExportJobRecord,
    ExportJobState,
    Project,
    TimelineClipRecord,
    TimelineSequenceRecord,
    TimelineTrackRecord,
)
from muse_orchestrator.services.asset_storage import LocalAssetStorage
from tests.test_queue_review2 import (  # noqa: F401 - `client` is a pytest fixture
    STATE,
    _setup,
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

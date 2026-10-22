"""Regression tests for backup/restore hardening (SEC-001/002/008, DATA-001/002/004/005, WF-001, PERF-001)."""
from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_restore_hardening_")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from muse_orchestrator.core.database import get_db  # noqa: E402 -- environment must be configured before app/database import
from muse_orchestrator.main import app  # noqa: E402 -- environment must be configured before app/database import
from muse_orchestrator.models.domain import (  # noqa: E402 -- environment must be configured before app/database import
    Asset,
    Base,
    ExportJobRecord,
    ExportJobState,
    GenerationTaskRecord,
    Project,
    ProviderConnection,
    QueueTaskState,
    SceneRecord,
    ShotGenerationState,
    ShotRecord,
    ShotTakeRecord,
    TimelineSequenceRecord,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture()
async def env(monkeypatch) -> Any:
    from muse_orchestrator.core.config import settings

    monkeypatch.setattr(settings, "media_root", _TEMP_MEDIA_DIR)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def _project(client: AsyncClient, name: str) -> str:
    response = await client.post("/api/v1/projects", json={"name": name, "aspect_ratio": "16:9", "fps": 24})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _upload_png(client: AsyncClient, project_id: str) -> str:
    response = await client.post(
        "/api/v1/assets/upload",
        data={"project_id": project_id, "asset_type": "reference"},
        files={"file": ("frame.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _seed_generation_graph(maker: async_sessionmaker[AsyncSession], project_id: str, asset_id: str, suffix: str) -> None:
    """Scene/shot/connection, an in-flight task, a succeeded task with a selected take, and a queued export."""
    async with maker() as db:
        db.add(ProviderConnection(
            id=f"conn_{suffix}", name=f"conn-{suffix}", provider_type="mock", base_url="http://localhost:9",
        ))
        db.add(SceneRecord(id=f"scn_{suffix}", project_id=project_id, scene_index=0, title="Scene"))
        db.add(ShotRecord(
            id=f"sht_{suffix}", project_id=project_id, scene_id=f"scn_{suffix}", sequence_index=0,
            title="Shot", location_code="loc", generation_state=ShotGenerationState.RUNNING,
            selected_take_id=f"tke_{suffix}",
        ))
        db.add(GenerationTaskRecord(
            id=f"tsk_live_{suffix}", project_id=project_id, scene_id=f"scn_{suffix}", shot_id=f"sht_{suffix}",
            connection_id=f"conn_{suffix}", model_id="m", idempotency_key=f"idem_live_{suffix}",
            state=QueueTaskState.RUNNING, external_task_id="upstream-123", prompt_snapshot="attacker prompt",
        ))
        db.add(GenerationTaskRecord(
            id=f"tsk_done_{suffix}", project_id=project_id, scene_id=f"scn_{suffix}", shot_id=f"sht_{suffix}",
            connection_id=f"conn_{suffix}", model_id="m", idempotency_key=f"idem_done_{suffix}",
            state=QueueTaskState.SUCCEEDED, result_asset_id=asset_id, take_id=f"tke_{suffix}",
        ))
        db.add(ShotTakeRecord(
            id=f"tke_{suffix}", project_id=project_id, shot_id=f"sht_{suffix}", take_number=1,
            task_id=f"tsk_done_{suffix}", asset_id=asset_id, model_id="m", prompt_hash="h", is_selected=True,
        ))
        db.add(TimelineSequenceRecord(id=f"seq_{suffix}", project_id=project_id))
        db.add(ExportJobRecord(
            id=f"exp_{suffix}", project_id=project_id, sequence_id=f"seq_{suffix}", state=ExportJobState.QUEUED,
            ffmpeg_args_json=["-i", "/etc/passwd"],
            manifest_snapshot_json={
                "resolved_clips": [{"path": "/etc/passwd", "duration": 1.0}],
                "video_clips": [{"asset_id": asset_id, "local_path": "/etc/shadow"}],
            },
        ))
        await db.commit()


async def _backup_bytes(client: AsyncClient, project_id: str) -> bytes:
    created = await client.post(f"/api/v1/projects/{project_id}/backup")
    assert created.status_code == 201, created.text
    downloaded = await client.get(f"/api/v1/backups/{created.json()['id']}/download")
    assert downloaded.status_code == 200
    return downloaded.content


def _rewrite_zip(content: bytes, replace: dict[str, bytes | None]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            if info.filename in replace:
                continue
            target.writestr(info.filename, source.read(info.filename))
        for name, data in replace.items():
            if data is not None:
                target.writestr(name, data)
    return out.getvalue()


def _table(content: bytes, key: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return json.loads(archive.read(f"data/{key}.json"))


async def _restore(client: AsyncClient, content: bytes):
    return await client.post("/api/v1/backups/restore", files={"file": ("b.zip", content, "application/zip")})


def _media_files() -> set[Path]:
    return {path for path in Path(_TEMP_MEDIA_DIR).rglob("*") if path.is_file() and "backups" not in path.parts}


async def test_same_instance_restore_neutralizes_in_flight_work_and_keeps_history(env) -> None:
    """SEC-001, DATA-001 (idempotency collision), DATA-005 (scene_id remap)."""
    client, maker = env
    project_id = await _project(client, "Hardening Source")
    asset_id = await _upload_png(client, project_id)
    await _seed_generation_graph(maker, project_id, asset_id, "same")
    content = await _backup_bytes(client, project_id)

    restored = await _restore(client, content)
    assert restored.status_code == 201, restored.text
    body = restored.json()
    report = body["report"]
    assert report["tasks_neutralized"] == 1
    assert report["exports_neutralized"] == 1
    new_project = body["project_id"]

    async with maker() as db:
        tasks = (await db.execute(
            select(GenerationTaskRecord).where(GenerationTaskRecord.project_id == new_project)
        )).scalars().all()
        scene = (await db.execute(select(SceneRecord).where(SceneRecord.project_id == new_project))).scalar_one()
        assert len(tasks) == 2
        for task in tasks:
            assert task.state in (QueueTaskState.FAILED, QueueTaskState.SUCCEEDED)
            assert task.external_task_id is None
            assert task.idempotency_key == f"restored:{task.id}"
            assert task.scene_id == scene.id
        failed = next(task for task in tasks if task.state == QueueTaskState.FAILED)
        assert failed.retryable is False and failed.error_category == "restored_incomplete"
        assert failed.finished_at is not None

        job = (await db.execute(select(ExportJobRecord).where(ExportJobRecord.project_id == new_project))).scalar_one()
        assert job.state == ExportJobState.FAILED
        assert job.ffmpeg_args_json == []
        assert "resolved_clips" not in job.manifest_snapshot_json
        assert "/etc/shadow" not in json.dumps(job.manifest_snapshot_json)

        shot = (await db.execute(select(ShotRecord).where(ShotRecord.project_id == new_project))).scalar_one()
        assert shot.generation_state == ShotGenerationState.FAILED


async def test_restore_keeps_takes_and_selected_take_when_connection_is_missing(env) -> None:
    """DATA-002: cross-instance restore keeps terminal history instead of dropping takes."""
    client, maker = env
    project_id = await _project(client, "Cross Instance Source")
    asset_id = await _upload_png(client, project_id)
    await _seed_generation_graph(maker, project_id, asset_id, "cross")
    content = await _backup_bytes(client, project_id)
    # Simulate another instance: the archived connection id does not exist here.
    tasks = _table(content, "generation_tasks")
    for task in tasks:
        task["connection_id"] = "conn_from_another_instance"
    content = _rewrite_zip(content, {"data/generation_tasks.json": json.dumps(tasks).encode()})

    restored = await _restore(client, content)
    assert restored.status_code == 201, restored.text
    report = restored.json()["report"]
    assert report["tables_restored"]["generation_tasks"] == 2
    assert report["tables_restored"]["shot_takes"] == 1
    assert report["tasks_with_placeholder_connection"] == 2
    new_project = restored.json()["project_id"]
    async with maker() as db:
        take = (await db.execute(select(ShotTakeRecord).where(ShotTakeRecord.project_id == new_project))).scalar_one()
        shot = (await db.execute(select(ShotRecord).where(ShotRecord.project_id == new_project))).scalar_one()
        assert shot.selected_take_id == take.id
        placeholder = await db.get(ProviderConnection, "conn_restored_unavailable")
        assert placeholder is not None
        assert placeholder.enabled is False and placeholder.encrypted_api_key is None


async def test_restore_failure_rolls_back_and_deletes_written_media(env, monkeypatch) -> None:
    """DATA-001: an IntegrityError maps to 409 and media written before commit is removed."""
    client, maker = env
    from sqlalchemy.exc import IntegrityError

    import muse_orchestrator.services.backup_service as backup_module

    project_id = await _project(client, "Rollback Source")
    await _upload_png(client, project_id)
    content = await _backup_bytes(client, project_id)
    before = _media_files()

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(backup_module.BackupService, "_remap_late_references", staticmethod(boom))
    restored = await _restore(client, content)
    assert restored.status_code == 409, restored.text
    assert _media_files() == before
    async with maker() as db:
        projects = (await db.execute(select(Project))).scalars().all()
        assert [project.id for project in projects] == [project_id]


async def test_restore_rejects_oversized_json_entry(env, monkeypatch) -> None:
    """SEC-002: JSON table entries are capped well below the archive limit."""
    client, _maker = env
    import muse_orchestrator.services.backup_service as backup_module

    project_id = await _project(client, "Json Cap Source")
    content = await _backup_bytes(client, project_id)
    content = _rewrite_zip(content, {"data/scenes.json": b"[" + b" " * 4096 + b"]"})
    monkeypatch.setattr(backup_module, "BACKUP_MAX_JSON_ENTRY_BYTES", 1024)
    restored = await _restore(client, content)
    assert restored.status_code == 413, restored.text


async def test_restore_skips_assets_without_verified_media(env) -> None:
    """SEC-008: never keep archive-supplied storage paths or non-allowlisted MIME types."""
    client, maker = env
    project_id = await _project(client, "Media Trust Source")
    await _upload_png(client, project_id)
    await _upload_png(client, project_id)
    content = await _backup_bytes(client, project_id)
    assets = _table(content, "assets")
    assets[0]["storage_rel_path"] = "../../etc/passwd-not-in-archive"
    assets[1]["mime_type"] = "text/html"
    content = _rewrite_zip(content, {"data/assets.json": json.dumps(assets).encode()})

    restored = await _restore(client, content)
    assert restored.status_code == 201, restored.text
    report = restored.json()["report"]
    assert report["tables_restored"]["assets"] == 0
    assert report["rows_skipped"]["assets"] == 2
    assert {row["reason"] for row in report["skipped_rows"]} == {"media missing or invalid"}
    async with maker() as db:
        rows = (await db.execute(
            select(Asset).where(Asset.project_id == restored.json()["project_id"])
        )).scalars().all()
        assert rows == []


async def test_restore_media_limit_is_not_the_upload_limit(env, monkeypatch) -> None:
    """SEC-008: long export MP4s larger than the upload cap must still restore."""
    client, _maker = env
    import muse_orchestrator.services.asset_storage as storage_module

    project_id = await _project(client, "Large Media Source")
    await _upload_png(client, project_id)
    content = await _backup_bytes(client, project_id)
    monkeypatch.setattr(storage_module, "MAX_UPLOAD_SIZE_BYTES", 16)
    restored = await _restore(client, content)
    assert restored.status_code == 201, restored.text
    assert restored.json()["report"]["media_restored"] == 1


async def test_restore_rejects_invalid_workflow_graph(env) -> None:
    """WF-001: restored workflow versions are validated like API-created ones."""
    client, _maker = env
    project_id = await _project(client, "Workflow Source")
    content = await _backup_bytes(client, project_id)
    workflows = [{"id": "wfl_x", "project_id": project_id, "name": "wf"}]
    nodes = [
        {"node_id": "a", "node_type": "Custom", "inputs": {"i": "text"}, "outputs": {"o": "text"}},
        {"node_id": "b", "node_type": "Custom", "inputs": {"i": "text"}, "outputs": {"o": "text"}},
    ]
    edges = [
        {"edge_id": "e1", "source_node_id": "a", "source_port": "o", "target_node_id": "b", "target_port": "i"},
        {"edge_id": "e2", "source_node_id": "b", "source_port": "o", "target_node_id": "a", "target_port": "i"},
    ]
    versions = [{"id": "wfv_x", "workflow_id": "wfl_x", "version_number": 1, "nodes_json": nodes, "edges_json": edges}]
    content = _rewrite_zip(content, {
        "data/workflows.json": json.dumps(workflows).encode(),
        "data/workflow_versions.json": json.dumps(versions).encode(),
    })
    restored = await _restore(client, content)
    assert restored.status_code == 422, restored.text


async def test_orphan_retention_below_one_hour_is_rejected(env) -> None:
    """DATA-004: retention 0 could delete in-flight restore/export files."""
    client, _maker = env
    for path in ("/api/v1/maintenance/orphans/scan", "/api/v1/maintenance/orphans/cleanup"):
        response = await client.post(path, json={"retention_hours": 0, "dry_run": True})
        assert response.status_code == 422, response.text


async def test_backup_and_restore_offload_blocking_io(env, monkeypatch) -> None:
    """PERF-001: zip/hash/media work goes through asyncio.to_thread."""
    client, _maker = env
    import muse_orchestrator.services.backup_service as backup_module

    called: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spy(func: Any, *args: Any, **kwargs: Any) -> Any:
        called.append(getattr(func, "__name__", repr(func)))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(backup_module.asyncio, "to_thread", spy)
    project_id = await _project(client, "Offload Source")
    await _upload_png(client, project_id)
    content = await _backup_bytes(client, project_id)
    restored = await _restore(client, content)
    assert restored.status_code == 201, restored.text
    assert {"_write_backup_archive", "_open_restore_archive", "_load_restore_payload", "_extract_asset_media"} <= set(called)

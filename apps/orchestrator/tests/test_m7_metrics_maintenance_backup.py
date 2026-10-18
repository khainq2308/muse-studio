from __future__ import annotations

import io
import os
import tempfile
import time
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_m7_maint_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.core.database import get_db

from muse_orchestrator.main import app
from muse_orchestrator.models.domain import (
    Asset,
    Base,
    GenerationTaskRecord,
    ProviderConnection,
    QueueTaskState,
    SceneRecord,
    ShotRecord,
)

_SESSION_MAKER: async_sessionmaker[AsyncSession] | None = None

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture()
async def client(monkeypatch):
    global _SESSION_MAKER
    from muse_orchestrator.core.config import settings

    monkeypatch.setattr(settings, "media_root", _TEMP_MEDIA_DIR)
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _SESSION_MAKER = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with _SESSION_MAKER() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _create_project(client: AsyncClient, name: str = "M7 Test Project") -> dict:
    response = await client.post(
        "/api/v1/projects", json={"name": name, "aspect_ratio": "16:9", "fps": 24}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _upload_png(client: AsyncClient, project_id: str, filename: str = "frame.png") -> dict:
    response = await client.post(
        "/api/v1/assets/upload",
        data={"project_id": project_id, "asset_type": "reference"},
        files={"file": (filename, PNG_BYTES, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_metrics_summary_and_prometheus(client: AsyncClient):
    await _create_project(client)

    summary = await client.get("/api/v1/metrics/summary")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["projects"]["total"] == 1
    assert body["queue"]["depth"] == 0
    assert body["assets"]["total"] == 0
    assert body["service"]["uptime_seconds"] >= 0
    assert body["storage"]["media_root"].endswith("muse_m7_maint_test_") or body["storage"]["media_root"] == _TEMP_MEDIA_DIR

    prom = await client.get("/api/v1/metrics/prometheus")
    assert prom.status_code == 200
    text = prom.text
    assert "musestudio_projects_total 1" in text
    assert "musestudio_queue_depth 0" in text
    assert text.startswith("# HELP")


async def test_orphan_scan_and_cleanup(client: AsyncClient):
    project = await _create_project(client, name="Orphan Project")
    asset = await _upload_png(client, project["id"])
    assert _SESSION_MAKER is not None
    async with _SESSION_MAKER() as db:
        stored = await db.get(Asset, asset["id"])
        assert stored is not None
        referenced_rel = stored.storage_rel_path

    media_root = Path(_TEMP_MEDIA_DIR)
    # An old unreferenced file -> orphan.
    orphan_old = media_root / "stray_old.png"
    orphan_old.write_bytes(PNG_BYTES)
    old_mtime = time.time() - 48 * 3600
    os.utime(orphan_old, (old_mtime, old_mtime))
    # A fresh unreferenced file -> protected by the retention window.
    orphan_fresh = media_root / "stray_fresh.png"
    orphan_fresh.write_bytes(PNG_BYTES)

    scan = await client.post("/api/v1/maintenance/orphans/scan", json={"retention_hours": 24})
    assert scan.status_code == 200, scan.text
    found = {item["rel_path"] for item in scan.json()["orphans"]}
    assert "stray_old.png" in found
    assert "stray_fresh.png" not in found
    assert referenced_rel not in found

    # Dry run must not delete anything.
    dry = await client.post(
        "/api/v1/maintenance/orphans/cleanup", json={"retention_hours": 24, "dry_run": True}
    )
    assert dry.json()["deleted"] == 0
    assert orphan_old.exists()

    real = await client.post(
        "/api/v1/maintenance/orphans/cleanup", json={"retention_hours": 24, "dry_run": False}
    )
    body = real.json()
    assert body["deleted"] == 1
    assert body["freed_bytes"] == len(PNG_BYTES)
    assert not orphan_old.exists()
    assert orphan_fresh.exists()
    assert (media_root / referenced_rel).exists()


async def test_backup_restore_roundtrip(client: AsyncClient):
    project = await _create_project(client, name="Backup Source")
    project_id = project["id"]
    asset = await _upload_png(client, project_id)

    # Add a scene + shot directly so the backup has relational depth.
    assert _SESSION_MAKER is not None
    async with _SESSION_MAKER() as db:
        db.add(SceneRecord(id="scn_m7t1", project_id=project_id, scene_index=0, title="S1"))
        db.add(
            ShotRecord(
                id="sht_m7t1",
                project_id=project_id,
                scene_id="scn_m7t1",
                sequence_index=0,
                title="Shot 1",
                location_code="loc_default",
            )
        )
        # Point the asset at the shot for FK coverage.
        stored_asset = await db.get(Asset, asset["id"])
        assert stored_asset is not None
        stored_asset.shot_id = "sht_m7t1"
        await db.commit()

    created = await client.post(f"/api/v1/projects/{project_id}/backup")
    assert created.status_code == 201, created.text
    backup = created.json()
    manifest = backup["manifest_json"]
    assert manifest["format_version"] == "m7-backup-1"
    assert manifest["tables"]["projects"] == 1
    assert manifest["tables"]["scenes"] == 1
    assert manifest["tables"]["shots"] == 1
    assert manifest["tables"]["assets"] == 1
    assert manifest["media_files"] == 1

    listed = await client.get(f"/api/v1/projects/{project_id}/backups")
    assert len(listed.json()) == 1

    downloaded = await client.get(f"/api/v1/backups/{backup['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert "data/projects.json" in names
        assert any(name.startswith("media/") for name in names)

    restored = await client.post(
        "/api/v1/backups/restore",
        files={"file": ("backup.zip", downloaded.content, "application/zip")},
        params={"project_name": "Restored Copy"},
    )
    assert restored.status_code == 201, restored.text
    body = restored.json()
    new_project_id = body["project_id"]
    assert new_project_id != project_id
    assert body["project_name"] == "Restored Copy"
    report = body["report"]
    assert report["tables_restored"]["projects"] == 1
    assert report["tables_restored"]["scenes"] == 1
    assert report["tables_restored"]["shots"] == 1
    assert report["tables_restored"]["assets"] == 1
    assert report["media_restored"] == 1
    assert report["media_missing"] == []

    # Verify the restored rows are wired to the new IDs.
    async with _SESSION_MAKER() as db:
        from sqlalchemy import select

        scenes = (await db.execute(select(SceneRecord).where(SceneRecord.project_id == new_project_id))).scalars().all()
        shots = (await db.execute(select(ShotRecord).where(ShotRecord.project_id == new_project_id))).scalars().all()
        assets = (await db.execute(select(Asset).where(Asset.project_id == new_project_id))).scalars().all()
        assert len(scenes) == 1 and scenes[0].id != "scn_m7t1"
        assert len(shots) == 1 and shots[0].scene_id == scenes[0].id
        assert len(assets) == 1 and assets[0].shot_id == shots[0].id
        assert Path(_TEMP_MEDIA_DIR, assets[0].storage_rel_path).is_file()


async def test_restore_rejects_bad_archive(client: AsyncClient):
    bad = await client.post(
        "/api/v1/backups/restore",
        files={"file": ("x.zip", b"not a zip", "application/zip")},
    )
    assert bad.status_code == 422

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", '{"format_version": "nope"}')
    wrong_version = await client.post(
        "/api/v1/backups/restore",
        files={"file": ("x.zip", buf.getvalue(), "application/zip")},
    )
    assert wrong_version.status_code == 422


async def test_orphan_cleanup_unlinks_symlink_without_deleting_target(client: AsyncClient):
    project = await _create_project(client, name="Symlink Cleanup Project")
    asset = await _upload_png(client, project["id"])
    assert _SESSION_MAKER is not None
    async with _SESSION_MAKER() as db:
        stored = await db.get(Asset, asset["id"])
        assert stored is not None
        referenced_path = Path(_TEMP_MEDIA_DIR, stored.storage_rel_path)
    alias = Path(_TEMP_MEDIA_DIR, "orphan-alias.png")
    try:
        alias.symlink_to(referenced_path)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks are not supported in this test environment")
    response = await client.post(
        "/api/v1/maintenance/orphans/cleanup", json={"retention_hours": 0, "dry_run": False}
    )
    assert response.status_code == 200, response.text
    assert not alias.is_symlink()
    assert referenced_path.is_file(), "cleanup must not unlink the symlink target"


async def test_restore_skips_generation_task_when_source_connection_is_missing(client: AsyncClient):
    project = await _create_project(client, name="Backup With External Connection")
    project_id = project["id"]
    assert _SESSION_MAKER is not None
    async with _SESSION_MAKER() as db:
        db.add(ProviderConnection(
            id="conn_restore_external", name="temporary-restore-connection",
            provider_type="mock", base_url="http://localhost:8080",
        ))
        db.add(SceneRecord(id="scn_restore_external", project_id=project_id, scene_index=0, title="Scene"))
        db.add(ShotRecord(
            id="sht_restore_external", project_id=project_id, scene_id="scn_restore_external",
            sequence_index=0, title="Shot", location_code="loc_default",
        ))
        db.add(GenerationTaskRecord(
            id="tsk_restore_external", project_id=project_id, scene_id="scn_restore_external",
            shot_id="sht_restore_external", connection_id="conn_restore_external", task_type="video",
            model_id="test-model", idempotency_key="idem_restore_external",
            state=QueueTaskState.QUEUED, prompt_snapshot="test prompt",
        ))
        await db.commit()
    backup_response = await client.post(f"/api/v1/projects/{project_id}/backup")
    assert backup_response.status_code == 201, backup_response.text
    downloaded = await client.get(f"/api/v1/backups/{backup_response.json()['id']}/download")
    assert downloaded.status_code == 200
    async with _SESSION_MAKER() as db:
        task = await db.get(GenerationTaskRecord, "tsk_restore_external")
        connection = await db.get(ProviderConnection, "conn_restore_external")
        if task is not None:
            await db.delete(task)
        assert connection is not None
        await db.delete(connection)
        await db.commit()
    restored = await client.post(
        "/api/v1/backups/restore", files={"file": ("backup.zip", downloaded.content, "application/zip")}
    )
    assert restored.status_code == 201, restored.text
    report = restored.json()["report"]
    assert report["rows_skipped"]["generation_tasks"] == 1
    assert report["tables_restored"]["generation_tasks"] == 0


async def test_restore_rejects_archive_with_excessive_uncompressed_size(client: AsyncClient, monkeypatch):
    import muse_orchestrator.services.backup_service as backup_module
    monkeypatch.setattr(backup_module, "BACKUP_MAX_UNCOMPRESSED_BYTES", 128)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", '{"format_version":"m7-backup-1","project_id":"p"}')
        archive.writestr("data/projects.json", "[" + (" " * 256) + "]")
    response = await client.post(
        "/api/v1/backups/restore", files={"file": ("oversized.zip", buf.getvalue(), "application/zip")}
    )
    assert response.status_code == 413, response.text

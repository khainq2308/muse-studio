"""Regression tests for deep-review-2 backup/restore, orphan cleanup and timeline findings.

DATA-002, DATA-008, DATA-009, DATA-010, SEC-001, SEC-002, SEC-006 and the P3 follow-ups.
The session factory mirrors core/database.AsyncSessionLocal (autoflush=False): the
default autoflush=True used by older fixtures hid the duplicate-placeholder bug.
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import os
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from muse_orchestrator.core.database import get_db  # noqa: E402 -- environment must be configured first
from muse_orchestrator.main import app  # noqa: E402 -- environment must be configured first
from muse_orchestrator.models.domain import (  # noqa: E402 -- environment must be configured first
    Asset,
    Base,
    BackupRecord,
    GenerationTaskRecord,
    Project,
    ProviderConnection,
    QueueTaskState,
    SceneRecord,
    ShotRecord,
    TimelineClipRecord,
    TimelineSequenceRecord,
    TimelineTrackRecord,
)
from muse_orchestrator.services import backup_service as backup_module  # noqa: E402
from muse_orchestrator.services import timeline_service  # noqa: E402
from muse_orchestrator.services.timeline_service import (  # noqa: E402
    ExportClip,
    ExportError,
    ExportPlan,
    TimelineValidationService,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture()
async def env(monkeypatch, tmp_path) -> Any:
    from muse_orchestrator.core.config import settings

    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setattr(settings, "media_root", str(media_root))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Same options as core/database.AsyncSessionLocal (production).
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, maker, media_root
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


async def _backup(client: AsyncClient, project_id: str) -> bytes:
    created = await client.post(f"/api/v1/projects/{project_id}/backup")
    assert created.status_code == 201, created.text
    downloaded = await client.get(f"/api/v1/backups/{created.json()['id']}/download")
    assert downloaded.status_code == 200
    return downloaded.content


def _rewrite(content: bytes, replace: dict[str, bytes | None]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            if info.filename not in replace:
                target.writestr(info.filename, source.read(info.filename))
        for name, data in replace.items():
            if data is not None:
                target.writestr(name, data)
    return out.getvalue()


def _table(content: bytes, key: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return json.loads(archive.read(f"data/{key}.json"))


def _with_table(content: bytes, key: str, rows: Any) -> bytes:
    return _rewrite(content, {f"data/{key}.json": json.dumps(rows).encode()})


async def _restore(client: AsyncClient, content: bytes):
    return await client.post("/api/v1/backups/restore", files={"file": ("b.zip", content, "application/zip")})


def _media_files(root: Path) -> set[Path]:
    return {path for path in root.rglob("*") if path.is_file() and "backups" not in path.parts}


async def _project_count(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as db:
        return (await db.execute(select(func.count(Project.id)))).scalar_one()


async def _seed_tasks(maker: async_sessionmaker[AsyncSession], project_id: str, count: int = 2) -> None:
    async with maker() as db:
        db.add(ProviderConnection(id="conn_src", name="src", provider_type="mock", base_url="http://localhost:9"))
        db.add(SceneRecord(id="scn_1", project_id=project_id, scene_index=0, title="S"))
        # Models declare FKs without relationship(): flush each parent level first.
        await db.flush()
        db.add(ShotRecord(
            id="sht_1", project_id=project_id, scene_id="scn_1", sequence_index=0, title="T", location_code="l",
        ))
        await db.flush()
        for index in range(count):
            db.add(GenerationTaskRecord(
                id=f"tsk_{index}", project_id=project_id, shot_id="sht_1", scene_id="scn_1",
                connection_id="conn_src", model_id="m", idempotency_key=f"k{index}",
                state=QueueTaskState.SUCCEEDED,
            ))
        await db.commit()


async def _cross_instance_archive(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> bytes:
    project_id = await _project(client, "src")
    await _seed_tasks(maker, project_id)
    content = await _backup(client, project_id)
    tasks = _table(content, "generation_tasks")
    for task in tasks:
        task["connection_id"] = "conn_other_instance"
    return _with_table(content, "generation_tasks", tasks)


# --- DATA-002: placeholder connection with production session options ----------------------


async def test_restore_with_several_tasks_creates_one_placeholder(env) -> None:
    client, maker, _root = env
    content = await _cross_instance_archive(client, maker)

    restored = await _restore(client, content)
    assert restored.status_code == 201, restored.text
    report = restored.json()["report"]
    assert report["tables_restored"]["generation_tasks"] == 2
    assert report["tasks_with_placeholder_connection"] == 2

    # A second restore reuses the existing placeholder instead of inserting it again.
    again = await _restore(client, content)
    assert again.status_code == 201, again.text
    async with maker() as db:
        placeholders = (await db.execute(
            select(ProviderConnection).where(ProviderConnection.provider_type == "restored_placeholder")
        )).scalars().all()
        assert len(placeholders) == 1
        assert placeholders[0].enabled is False and placeholders[0].encrypted_api_key is None


async def test_restore_placeholder_tolerates_a_user_connection_with_the_same_name(env) -> None:
    client, maker, _root = env
    content = await _cross_instance_archive(client, maker)
    async with maker() as db:
        db.add(ProviderConnection(
            id="conn_user", name=backup_module._RESTORED_PLACEHOLDER_CONNECTION_NAME,
            provider_type="mock", base_url="http://localhost:9",
        ))
        await db.commit()

    restored = await _restore(client, content)
    assert restored.status_code == 201, restored.text
    async with maker() as db:
        tasks = (await db.execute(
            select(GenerationTaskRecord).where(GenerationTaskRecord.project_id == restored.json()["project_id"])
        )).scalars().all()
        assert {task.connection_id for task in tasks} == {backup_module.RESTORED_PLACEHOLDER_CONNECTION_ID}
        user_connection = await db.get(ProviderConnection, "conn_user")
        assert user_connection is not None and user_connection.provider_type == "mock"


# --- SEC-001: restore disk amplification ----------------------------------------------------


async def test_restore_rejects_highly_compressed_media_bomb(env) -> None:
    client, _maker, root = env
    project_id = await _project(client, "amp")
    await _upload_png(client, project_id)
    content = await _backup(client, project_id)
    assets = _table(content, "assets")
    rel = assets[0]["storage_rel_path"]
    rows = [dict(assets[0], id=f"ast_dup_{index}") for index in range(40)]
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (8 * 1024 * 1024)
    evil = _rewrite(content, {"data/assets.json": json.dumps(rows).encode(), f"media/{rel}": big})
    assert len(evil) < 64 * 1024
    before = _media_files(root)

    restored = await _restore(client, evil)

    assert restored.status_code == 413, restored.text
    assert _media_files(root) == before


async def test_restore_extracts_each_media_entry_once(env) -> None:
    client, maker, root = env
    project_id = await _project(client, "dedupe")
    await _upload_png(client, project_id)
    content = await _backup(client, project_id)
    assets = _table(content, "assets")
    rows = [dict(assets[0], id=f"ast_dup_{index}") for index in range(5)]
    before = _media_files(root)

    restored = await _restore(client, _with_table(content, "assets", rows))

    assert restored.status_code == 201, restored.text
    report = restored.json()["report"]
    assert report["tables_restored"]["assets"] == 5
    assert report["media_restored"] == 5
    assert len(_media_files(root) - before) == 1
    async with maker() as db:
        stored = (await db.execute(
            select(Asset.storage_rel_path).where(Asset.project_id == restored.json()["project_id"])
        )).scalars().all()
    assert len(stored) == 5 and len(set(stored)) == 1


async def test_restore_counts_written_bytes_against_the_uncompressed_limit(env, monkeypatch) -> None:
    client, _maker, root = env
    project_id = await _project(client, "running-total")
    await _upload_png(client, project_id)
    await _upload_png(client, project_id)
    content = await _backup(client, project_id)
    before = _media_files(root)
    # Header sizes pass (checked first), but the running total of extracted bytes must still stop.
    real_open = backup_module._open_restore_archive

    def open_then_shrink(*args: Any, **kwargs: Any):
        archive = real_open(*args, **kwargs)
        monkeypatch.setattr(backup_module, "BACKUP_MAX_UNCOMPRESSED_BYTES", len(PNG_BYTES) + 1)
        return archive

    monkeypatch.setattr(backup_module, "_open_restore_archive", open_then_shrink)
    restored = await _restore(client, content)
    assert restored.status_code == 413, restored.text
    assert _media_files(root) == before


# --- DATA-008: manifest project must be the single archived project --------------------------


async def test_restore_rejects_manifest_project_mismatch_before_writing(env) -> None:
    client, maker, root = env
    project_id = await _project(client, "mismatch")
    await _upload_png(client, project_id)
    content = await _backup(client, project_id)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["project_id"] = "proj_not_in_archive"
    evil = _rewrite(content, {"manifest.json": json.dumps(manifest).encode()})
    projects_before = await _project_count(maker)
    files_before = _media_files(root)

    restored = await _restore(client, evil)

    assert restored.status_code == 422, restored.text
    assert await _project_count(maker) == projects_before
    assert _media_files(root) == files_before


async def test_restore_rejects_archives_with_several_projects(env) -> None:
    client, maker, _root = env
    project_id = await _project(client, "multi")
    content = await _backup(client, project_id)
    projects = _table(content, "projects")
    projects.append(dict(projects[0], id="proj_second"))
    projects_before = await _project_count(maker)

    restored = await _restore(client, _with_table(content, "projects", projects))

    assert restored.status_code == 422, restored.text
    assert await _project_count(maker) == projects_before


# --- SEC-002 / P3: JSON shape, types and size --------------------------------------------------


async def _scene_archive(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> tuple[str, bytes]:
    project_id = await _project(client, "typed")
    async with maker() as db:
        db.add(SceneRecord(id="scn_1", project_id=project_id, scene_index=0, title="S"))
        await db.commit()
    return project_id, await _backup(client, project_id)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("project_id", ["x"]),
        ("scene_index", {"a": 1}),
        ("scene_index", True),
        ("scene_index", 2**80),
        ("title", "x" * 10_000),
        ("title", None),
        ("created_at", "not-a-date"),
    ],
    ids=["list-fk", "dict-int", "bool-int", "int-overflow", "overlong-string", "null-required", "bad-datetime"],
)
async def test_restore_rejects_wrong_typed_values_with_422(env, column: str, value: Any) -> None:
    client, maker, _root = env
    _project_id, content = await _scene_archive(client, maker)
    scenes = _table(content, "scenes")
    scenes[0][column] = value
    projects_before = await _project_count(maker)

    restored = await _restore(client, _with_table(content, "scenes", scenes))

    assert restored.status_code == 422, restored.text
    assert await _project_count(maker) == projects_before


async def test_restore_rejects_missing_required_column(env) -> None:
    client, maker, _root = env
    _project_id, content = await _scene_archive(client, maker)
    scenes = _table(content, "scenes")
    del scenes[0]["title"]
    restored = await _restore(client, _with_table(content, "scenes", scenes))
    assert restored.status_code == 422, restored.text


@pytest.mark.parametrize("payload", [b'[{"id": NaN}]', b'[{"id": "x", "scene_index": Infinity}]'])
async def test_restore_rejects_non_finite_json_constants(env, payload: bytes) -> None:
    client, maker, _root = env
    _project_id, content = await _scene_archive(client, maker)
    restored = await _restore(client, _rewrite(content, {"data/scenes.json": payload}))
    assert restored.status_code == 422, restored.text


async def test_restore_rejects_deeply_nested_json_without_500(env) -> None:
    client, maker, _root = env
    _project_id, content = await _scene_archive(client, maker)
    depth = 200_000
    payload = b"[" * depth + b"]" * depth
    restored = await _restore(client, _rewrite(content, {"data/scenes.json": payload}))
    assert restored.status_code == 422, restored.text


async def test_restore_rejects_deep_json_column_values(env) -> None:
    client, maker, _root = env
    _project_id, content = await _scene_archive(client, maker)
    projects = _table(content, "project_revisions") or []
    nested: Any = "leaf"
    for _ in range(500):
        nested = [nested]
    projects.append({"id": "rev_x", "project_id": _table(content, "projects")[0]["id"],
                     "revision_number": 1, "snapshot_json": {"deep": nested}})
    restored = await _restore(client, _with_table(content, "project_revisions", projects))
    assert restored.status_code == 422, restored.text


async def test_restore_caps_rows_per_table(env, monkeypatch) -> None:
    client, maker, _root = env
    _project_id, content = await _scene_archive(client, maker)
    scenes = _table(content, "scenes")
    scenes = [dict(scenes[0], id=f"scn_{index}") for index in range(3)]
    monkeypatch.setattr(backup_module, "BACKUP_MAX_ROWS_PER_TABLE", 2)
    restored = await _restore(client, _with_table(content, "scenes", scenes))
    assert restored.status_code == 413, restored.text


async def test_restore_caps_total_rows(env, monkeypatch) -> None:
    client, maker, _root = env
    _project_id, content = await _scene_archive(client, maker)
    monkeypatch.setattr(backup_module, "BACKUP_MAX_TOTAL_ROWS", 1)
    restored = await _restore(client, content)
    assert restored.status_code == 413, restored.text


async def test_restore_caps_total_json_bytes(env, monkeypatch) -> None:
    client, maker, _root = env
    _project_id, content = await _scene_archive(client, maker)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        json_sizes = [info.file_size for info in archive.infolist() if info.filename.endswith(".json")]
    # Every single entry fits, but their sum does not.
    monkeypatch.setattr(backup_module, "BACKUP_MAX_TOTAL_JSON_BYTES", sum(json_sizes) - 1)
    restored = await _restore(client, content)
    assert restored.status_code == 413, restored.text


async def test_restore_truncates_skipped_rows_report(env, monkeypatch) -> None:
    client, maker, _root = env
    project_id = await _project(client, "skipped")
    await _upload_png(client, project_id)
    content = await _backup(client, project_id)
    assets = _table(content, "assets")
    rows = [dict(assets[0], id=f"ast_missing_{index}", storage_rel_path=f"x/missing_{index}.png") for index in range(6)]
    monkeypatch.setattr(backup_module, "RESTORE_MAX_SKIPPED_ROWS_REPORTED", 2)

    restored = await _restore(client, _with_table(content, "assets", rows))

    assert restored.status_code == 201, restored.text
    report = restored.json()["report"]
    assert report["rows_skipped"]["assets"] == 6
    assert len(report["skipped_rows"]) == 2
    assert len(report["media_missing"]) <= 2


# --- SEC-006: restored timeline values must respect the API bounds ---------------------------


async def _timeline_archive(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> bytes:
    project_id = await _project(client, "gap")
    async with maker() as db:
        db.add(SceneRecord(id="scn_1", project_id=project_id, scene_index=0, title="S"))
        # Models declare FKs without relationship(): flush each parent level first.
        await db.flush()
        db.add(ShotRecord(
            id="sht_1", project_id=project_id, scene_id="scn_1", sequence_index=0, title="T", location_code="l",
        ))
        await db.flush()
        db.add(TimelineSequenceRecord(id="seq_x", project_id=project_id))
        await db.flush()
        db.add(TimelineTrackRecord(id="trk_x", sequence_id="seq_x"))
        await db.flush()
        db.add(TimelineClipRecord(id="clp_x", sequence_id="seq_x", track_id="trk_x", order_index=0))
        await db.commit()
    return await _backup(client, project_id)


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("timeline_clips", "start_time_seconds", 1.0e7),
        ("timeline_clips", "effective_duration_seconds", 1.0e6),
        ("timeline_clips", "trim_start_seconds", -1.0),
        ("timeline_clips", "transition_duration_seconds", 50.0),
        ("timeline_clips", "transition_type", "wipe"),
        ("timeline_sequences", "fps", 100_000),
        ("timeline_sequences", "total_duration_seconds", 1.0e9),
        ("timeline_sequences", "resolution_profile", "99999x99999"),
        ("timeline_tracks", "volume", 1000.0),
        ("shots", "duration_seconds", 1.0e6),
        ("projects", "fps", 0),
    ],
)
async def test_restore_rejects_timeline_values_outside_api_bounds(env, table: str, column: str, value: Any) -> None:
    client, maker, _root = env
    content = await _timeline_archive(client, maker)
    rows = _table(content, table)
    rows[0][column] = value
    projects_before = await _project_count(maker)

    restored = await _restore(client, _with_table(content, table, rows))

    assert restored.status_code == 422, restored.text
    assert await _project_count(maker) == projects_before


async def test_valid_timeline_roundtrip_still_restores(env) -> None:
    client, maker, _root = env
    content = await _timeline_archive(client, maker)
    restored = await _restore(client, content)
    assert restored.status_code == 201, restored.text
    assert restored.json()["report"]["tables_restored"]["timeline_clips"] == 1


def _export_clip(**overrides: Any) -> ExportClip:
    values: dict[str, Any] = {"path": "/nonexistent.mp4", "effective_duration": 3.0}
    values.update(overrides)
    return ExportClip(**values)


def test_export_plan_rejects_unbounded_gap() -> None:
    with pytest.raises(ExportError) as excinfo:
        TimelineValidationService.build_export_plan(
            [_export_clip(start_time=1.0e7)], "/tmp/out.mp4", 24, "1920x1080"
        )
    assert excinfo.value.status_code == 422


@pytest.mark.parametrize("field", ["effective_duration", "trim_start", "transition_duration", "start_time"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_export_plan_rejects_non_finite_values(field: str, value: float) -> None:
    with pytest.raises(ExportError):
        TimelineValidationService.build_export_plan(
            [_export_clip(), _export_clip(**{field: value})], "/tmp/out.mp4", 24, "1920x1080"
        )


def test_export_plan_caps_expected_duration_and_clip_count(monkeypatch) -> None:
    monkeypatch.setattr(timeline_service, "MAX_EXPORT_DURATION_SECONDS", 10.0)
    with pytest.raises(ExportError):
        TimelineValidationService.build_export_plan(
            [_export_clip(effective_duration=6.0), _export_clip(effective_duration=6.0)],
            "/tmp/out.mp4", 24, "1920x1080",
        )
    monkeypatch.setattr(timeline_service, "MAX_EXPORT_DURATION_SECONDS", 4 * 3600.0)
    monkeypatch.setattr(timeline_service, "MAX_EXPORT_CLIPS", 2)
    with pytest.raises(ExportError):
        TimelineValidationService.build_export_plan(
            [_export_clip() for _ in range(3)], "/tmp/out.mp4", 24, "1920x1080"
        )


def test_verify_output_tolerance_is_tight(monkeypatch) -> None:
    plan = ExportPlan(args=[], expected_duration=100.0, width=320, height=240, fps=24)
    probe = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 320, "height": 240},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "103.0"},
    }

    class _Result:
        returncode = 0
        stdout = json.dumps(probe)
        stderr = ""

    monkeypatch.setattr(timeline_service.subprocess, "run", lambda *_a, **_k: _Result())
    monkeypatch.setattr(
        TimelineValidationService, "_probe_video_stream_duration",
        classmethod(lambda cls, *_a, **_k: (True, 103.0)),
    )
    meta = TimelineValidationService.verify_output("/tmp/out.mp4", plan)
    assert meta["valid"] is False  # 3% off a 100s timeline is a wrong render, not jitter


def test_render_can_be_cancelled_and_kills_the_child(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(timeline_service.shutil, "which", lambda *_a, **_k: "/usr/bin/true")
    marker = tmp_path / "child_started"
    sleeper = [sys.executable, "-c", f"import pathlib,time; pathlib.Path({str(marker)!r}).touch(); time.sleep(60)"]

    def fake_plan(cls, clips, output_path, fps, resolution):
        Path(output_path).write_bytes(b"partial")
        return ExportPlan(args=sleeper, expected_duration=1.0, width=320, height=240, fps=24)

    monkeypatch.setattr(TimelineValidationService, "build_export_plan", classmethod(fake_plan))
    cancel = threading.Event()

    def trigger() -> None:
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        cancel.set()

    threading.Thread(target=trigger, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(timeline_service.ExportCanceledError):
        TimelineValidationService.render_and_probe_mp4(
            output_dir=str(tmp_path / "out"), export_id="exp_cancel", fps=24, resolution="320x240",
            clips=[_export_clip()], cancel_event=cancel,
        )
    assert time.monotonic() - started < 15
    assert not (tmp_path / "out" / "exp_cancel.mp4").exists()


# --- DATA-009: create refuses archives that restore would reject ------------------------------


@pytest.mark.parametrize(
    ("limit", "value"),
    [
        ("BACKUP_MAX_JSON_ENTRY_BYTES", 64),
        ("BACKUP_MAX_TOTAL_JSON_BYTES", 256),
        ("BACKUP_MAX_ENTRIES", 2),
        ("BACKUP_MAX_UNCOMPRESSED_BYTES", 256),
        ("RESTORE_MAX_MEDIA_BYTES", 16),
        ("BACKUP_MAX_ROWS_PER_TABLE", 0),
    ],
)
async def test_create_backup_enforces_restore_limits(env, monkeypatch, limit: str, value: int) -> None:
    client, maker, root = env
    project_id = await _project(client, "limits")
    await _upload_png(client, project_id)
    monkeypatch.setattr(backup_module, limit, value)

    created = await client.post(f"/api/v1/projects/{project_id}/backup")

    assert created.status_code == 413, created.text
    backups_dir = root / "backups"
    assert not backups_dir.exists() or not [path for path in backups_dir.rglob("*") if path.is_file()]
    async with maker() as db:
        assert (await db.execute(select(func.count(BackupRecord.id)))).scalar_one() == 0


async def test_created_backup_stores_media_uncompressed(env) -> None:
    client, _maker, _root = env
    project_id = await _project(client, "stored")
    await _upload_png(client, project_id)
    content = await _backup(client, project_id)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        media = [info for info in archive.infolist() if info.filename.startswith("media/")]
    assert media and all(info.compress_type == zipfile.ZIP_STORED for info in media)


# --- DATA-010: non-canonical MEDIA_ROOT --------------------------------------------------------


@pytest.fixture()
def symlinked_root(tmp_path: Path) -> tuple[Path, Path]:
    real = (tmp_path / "real").resolve()
    real.mkdir()
    link_parent = tmp_path / "links" / "nested"
    link_parent.mkdir(parents=True)
    link = link_parent / "media"
    try:
        os.symlink(real, link)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks are not supported in this test environment")
    return real, link


def _legacy_rel(path: Path, link: Path) -> str:
    """What os.path.relpath(resolved_file, settings.media_root) produced before the fix."""
    return os.path.relpath(str(path.resolve()), str(link))


async def test_orphan_scan_normalises_non_canonical_stored_paths(env, symlinked_root, monkeypatch) -> None:
    from muse_orchestrator.core.config import settings
    from muse_orchestrator.services.maintenance_service import cleanup_orphan_media, scan_orphan_media

    _client, maker, _root = env
    real, link = symlinked_root
    monkeypatch.setattr(settings, "media_root", str(link))
    frame = real / "proj_a" / "generated" / "boundary_frames" / "sht" / "take_1" / "first_frame.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(PNG_BYTES)
    stored = _legacy_rel(frame, link)
    assert stored.startswith("..")
    old = time.time() - 48 * 3600
    os.utime(frame, (old, old))
    async with maker() as db:
        db.add(Project(id="proj_a", name="a"))
        # Models declare FKs without relationship(): flush the parent first.
        await db.flush()
        db.add(Asset(
            id="ast_ff", project_id="proj_a", asset_type="reference", filename="f.png",
            storage_rel_path=stored, mime_type="image/png", size_bytes=len(PNG_BYTES), sha256="x",
        ))
        await db.commit()
        orphans = await scan_orphan_media(db, str(link), 24)
        assert orphans == []
        report = await cleanup_orphan_media(db, str(link), 24, dry_run=False)
    assert report["deleted"] == 0
    assert frame.is_file()


async def test_backup_of_non_canonical_paths_restores(env, symlinked_root, monkeypatch) -> None:
    from muse_orchestrator.core.config import settings

    client, maker, _root = env
    real, link = symlinked_root
    monkeypatch.setattr(settings, "media_root", str(link))
    project_id = await _project(client, "legacy-paths")
    frame = real / project_id / "generated" / "first_frame.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(PNG_BYTES)
    async with maker() as db:
        db.add(Asset(
            id="ast_legacy", project_id=project_id, asset_type="reference", filename="first_frame.png",
            storage_rel_path=_legacy_rel(frame, link), mime_type="image/png",
            size_bytes=len(PNG_BYTES), sha256="x",
        ))
        await db.commit()

    content = await _backup(client, project_id)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert f"media/{project_id}/generated/first_frame.png" in archive.namelist()
    assert _table(content, "assets")[0]["storage_rel_path"] == f"{project_id}/generated/first_frame.png"

    restored = await _restore(client, content)
    assert restored.status_code == 201, restored.text
    assert restored.json()["report"]["media_restored"] == 1


# --- P3: orphan cleanup only deletes inside managed subtrees ----------------------------------


async def test_orphan_cleanup_only_touches_managed_subtrees(env) -> None:
    client, _maker, root = env
    project_id = await _project(client, "scope")
    await _upload_png(client, project_id)
    old = time.time() - 48 * 3600
    stray_in_project = root / project_id / "generated" / "stray.mp4"
    stray_in_deleted_project = root / "proj_0123456789" / "exports" / "old.mp4"
    stray_backup = root / "backups" / project_id / ".bkp_x.part"
    stray_temp = root / f".restore-{'a' * 32}.part"
    unmanaged = [root / "README.txt", root / "operator_notes" / "keep.png", root / "lost+found" / "f"]
    managed = [stray_in_project, stray_in_deleted_project, stray_backup, stray_temp]
    for path in [*managed, *unmanaged]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        os.utime(path, (old, old))

    scan = await client.post("/api/v1/maintenance/orphans/scan", json={"retention_hours": 24})
    assert scan.status_code == 200, scan.text
    found = {item["rel_path"] for item in scan.json()["orphans"]}
    assert found == {path.relative_to(root).as_posix() for path in managed}

    cleanup = await client.post(
        "/api/v1/maintenance/orphans/cleanup", json={"retention_hours": 24, "dry_run": False}
    )
    assert cleanup.status_code == 200, cleanup.text
    assert cleanup.json()["deleted"] == len(managed)
    assert all(not path.exists() for path in managed)
    assert all(path.exists() for path in unmanaged)


# --- P3: cancellation during restore leaves no files ------------------------------------------


async def test_cancelled_restore_removes_media_written_by_the_worker_thread(env, monkeypatch) -> None:
    from muse_orchestrator.infrastructure.repositories import StudioRepository
    from muse_orchestrator.services.asset_storage import LocalAssetStorage

    client, maker, root = env
    project_id = await _project(client, "cancel")
    await _upload_png(client, project_id)
    content = await _backup(client, project_id)
    before = _media_files(root)

    extracted = threading.Event()
    release = threading.Event()
    real_extract = backup_module._extract_asset_media

    def slow_extract(*args: Any, **kwargs: Any):
        result = real_extract(*args, **kwargs)
        extracted.set()
        release.wait(10)
        return result

    monkeypatch.setattr(backup_module, "_extract_asset_media", slow_extract)
    async with maker() as db:
        service = backup_module.BackupService(StudioRepository(db), LocalAssetStorage(str(root)))
        task = asyncio.create_task(service.restore_backup(content))
        while not extracted.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.05)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert _media_files(root) == before
    assert await _project_count(maker) == 1


# --- Timeline API P3 ---------------------------------------------------------------------------


async def test_assemble_rejects_audio_asset_from_another_project(env) -> None:
    client, _maker, _root = env
    owner = await _project(client, "owner")
    other = await _project(client, "other")
    foreign_asset = await _upload_png(client, other)
    response = await client.post(
        f"/api/v1/projects/{owner}/timeline/assemble",
        json={"audio_tracks": [{"track_type": "music", "name": "x", "asset_id": foreign_asset}]},
    )
    assert response.status_code == 422, response.text


async def test_assemble_caps_clip_count(env, monkeypatch) -> None:
    from muse_orchestrator.api import timeline as timeline_api

    client, _maker, _root = env
    project_id = await _project(client, "many-clips")
    monkeypatch.setattr(timeline_api, "MAX_TIMELINE_CLIPS", 2)
    clips = [{"order_index": index} for index in range(3)]
    response = await client.post(
        f"/api/v1/projects/{project_id}/timeline/assemble", json={"custom_clips": clips}
    )
    assert response.status_code == 422, response.text

"""Foreign-key ordering regressions (PostgreSQL enforces FKs; SQLite now does too).

The ORM models declare ForeignKeys but no relationship(), so SQLAlchemy's unit of work
orders INSERT/DELETE statements by mapper name, not by FK dependency. Every multi-table
write path must flush parents before children (and delete children before parents).
These tests drive those paths end to end with SQLite ``foreign_keys=ON``.
"""
from __future__ import annotations

import os
import tempfile
import warnings
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SAWarning
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_fk_order_")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from muse_orchestrator.core.database import get_db  # noqa: E402 -- environment must be configured first
from muse_orchestrator.main import app  # noqa: E402 -- environment must be configured first
from muse_orchestrator.models.domain import (  # noqa: E402 -- environment must be configured first
    Base,
    GenerationTaskRecord,
    Project,
    SceneRecord,
    ShotRecord,
    ShotTakeRecord,
    TimelineClipRecord,
    TimelineSequenceRecord,
    TimelineTrackRecord,
    WorkflowDefinition,
    WorkflowVersion,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

SCRIPT = """
SCENE 1: Rooftop
SHOT 1: Hero looks over the city
- Character: Hero
- Action: Hero stares at the skyline
- Duration: 4.0

SHOT 2: Hero turns around
- Character: Hero
- Action: Hero turns toward the door
- Duration: 5.0
"""


class _FakeArqPool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> object:
        self.jobs.append((function, args, kwargs))
        return object()


@pytest.fixture()
async def env(monkeypatch: pytest.MonkeyPatch) -> Any:
    from muse_orchestrator.core.config import settings
    from muse_orchestrator.workers import queue_worker

    monkeypatch.setattr(settings, "media_root", _TEMP_MEDIA_DIR)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Production session options (core/database.AsyncSessionLocal).
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    monkeypatch.setattr(queue_worker, "AsyncSessionLocal", maker)
    previous_pool = getattr(app.state, "arq_pool", None)
    app.state.arq_pool = _FakeArqPool()
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield client, maker
    finally:
        app.dependency_overrides.clear()
        if previous_pool is None:
            if hasattr(app.state, "arq_pool"):
                delattr(app.state, "arq_pool")
        else:
            app.state.arq_pool = previous_pool
        await engine.dispose()


async def _assert_fk_integrity(maker: async_sessionmaker[AsyncSession]) -> None:
    async with maker() as db:
        assert (await db.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
        assert (await db.execute(text("PRAGMA foreign_key_check"))).all() == []


async def _count(db: AsyncSession, model: Any, *where: Any) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def _project_with_rendered_takes(client: AsyncClient) -> str:
    project = await client.post("/api/v1/projects", json={"name": "FK order", "aspect_ratio": "16:9", "fps": 24})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    connection = await client.post(
        "/api/v1/connections",
        json={"name": "fk-order-mock", "provider_type": "mock", "base_url": "http://localhost:8080",
              "max_concurrency": 4},
    )
    assert connection.status_code == 201, connection.text
    imported = await client.post(
        f"/api/v1/projects/{project_id}/scripts/import", json={"raw_content": SCRIPT, "auto_apply_proposal": True}
    )
    assert imported.status_code in (200, 201), imported.text
    launched = await client.post(
        f"/api/v1/projects/{project_id}/batch/launch", json={"connection_id": connection.json()["id"]}
    )
    assert launched.status_code in (200, 201), launched.text
    for _ in range(5):
        await client.post(f"/api/v1/projects/{project_id}/batch/tick")
    shots = (await client.get(f"/api/v1/projects/{project_id}/shots")).json()
    assert len(shots) == 2
    assert all(shot["selected_take_id"] for shot in shots)
    return project_id


async def _assemble(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/projects/{project_id}/timeline/assemble",
        json={
            "name": "FK cut",
            "allow_missing_shots": False,
            "default_transition": "cross_dissolve",
            "transition_duration_seconds": 0.5,
            "audio_tracks": [{"track_type": "music", "name": "OST", "volume": 0.7}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_sqlite_connections_enforce_foreign_keys() -> None:
    """The global connect listener must keep FK enforcement on for every SQLite engine."""
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        engine.dispose()


async def test_unit_of_work_does_not_order_unrelated_mappers(env) -> None:
    """Documents the hazard the explicit flushes guard against (no relationship())."""
    _client, maker = env
    async with maker() as db:
        db.add(Project(id="prj_uow", name="uow"))
        await db.commit()
    async with maker() as db:
        db.add_all([
            TimelineSequenceRecord(id="seq_uow", project_id="prj_uow"),
            TimelineTrackRecord(id="trk_uow", sequence_id="seq_uow"),
            TimelineClipRecord(id="clp_uow", sequence_id="seq_uow", track_id="trk_uow", order_index=0),
        ])
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_timeline_assemble_persists_sequence_tracks_and_clips(env) -> None:
    client, maker = env
    project_id = await _project_with_rendered_takes(client)

    timeline = await _assemble(client, project_id)

    assert timeline["clip_count"] == 2
    assert timeline["audio_track_count"] == 1
    async with maker() as db:
        sequence_id = timeline.get("sequence_id") or timeline.get("id")
        sequence = await db.get(TimelineSequenceRecord, sequence_id)
        assert sequence is not None and sequence.project_id == project_id
        assert await _count(db, TimelineTrackRecord, TimelineTrackRecord.sequence_id == sequence_id) >= 2
        assert await _count(db, TimelineClipRecord, TimelineClipRecord.sequence_id == sequence_id) == 2
    await _assert_fk_integrity(maker)


async def test_backup_restore_roundtrip_with_timeline_tasks_takes_and_workflows(env) -> None:
    client, maker = env
    project_id = await _project_with_rendered_takes(client)
    await _assemble(client, project_id)

    workflow = await client.post(
        f"/api/v1/projects/{project_id}/workflows",
        json={"name": "FK workflow", "use_default_m3_template": True},
    )
    assert workflow.status_code == 201, workflow.text
    workflow_body = workflow.json()
    updated = await client.put(
        f"/api/v1/projects/{project_id}/workflows/{workflow_body['id']}/graph",
        json={"nodes": workflow_body["nodes"], "edges": workflow_body["edges"], "ui_layout": {}},
    )
    assert updated.status_code == 200, updated.text
    for run_mode in ("dry_run", "full"):
        run = await client.post(
            f"/api/v1/projects/{project_id}/workflows/{workflow_body['id']}/runs", json={"run_mode": run_mode}
        )
        assert run.status_code == 201, run.text

    async with maker() as db:
        source_counts = {
            "tasks": await _count(db, GenerationTaskRecord, GenerationTaskRecord.project_id == project_id),
            "takes": await _count(db, ShotTakeRecord, ShotTakeRecord.project_id == project_id),
            "shots": await _count(db, ShotRecord, ShotRecord.project_id == project_id),
            "scenes": await _count(db, SceneRecord, SceneRecord.project_id == project_id),
        }
    assert source_counts["tasks"] >= 2 and source_counts["takes"] >= 2

    backup = await client.post(f"/api/v1/projects/{project_id}/backup")
    assert backup.status_code == 201, backup.text
    archive = await client.get(f"/api/v1/backups/{backup.json()['id']}/download")
    assert archive.status_code == 200

    restored = await client.post(
        "/api/v1/backups/restore", files={"file": ("backup.zip", archive.content, "application/zip")}
    )
    assert restored.status_code == 201, restored.text
    body = restored.json()
    new_project_id = body["project_id"]
    assert new_project_id != project_id
    tables = body["report"]["tables_restored"]
    assert tables["generation_tasks"] == source_counts["tasks"]
    assert tables["shot_takes"] == source_counts["takes"]
    assert tables["workflow_versions"] == 2
    assert tables["timeline_sequences"] == 1
    assert tables["timeline_clips"] == 2

    async with maker() as db:
        assert await _count(db, ShotRecord, ShotRecord.project_id == new_project_id) == source_counts["shots"]
        assert await _count(db, SceneRecord, SceneRecord.project_id == new_project_id) == source_counts["scenes"]
        takes = (await db.execute(
            select(ShotTakeRecord).where(ShotTakeRecord.project_id == new_project_id)
        )).scalars().all()
        assert len(takes) == source_counts["takes"]
        task_ids = set((await db.execute(
            select(GenerationTaskRecord.id).where(GenerationTaskRecord.project_id == new_project_id)
        )).scalars().all())
        assert {take.task_id for take in takes} <= task_ids
        shots = (await db.execute(select(ShotRecord).where(ShotRecord.project_id == new_project_id))).scalars().all()
        assert all(shot.selected_take_id in {take.id for take in takes} for shot in shots)
        workflow_row = (await db.execute(
            select(WorkflowDefinition).where(WorkflowDefinition.project_id == new_project_id)
        )).scalar_one()
        assert await _count(db, WorkflowVersion, WorkflowVersion.workflow_id == workflow_row.id) == 2
        sequence = (await db.execute(
            select(TimelineSequenceRecord).where(TimelineSequenceRecord.project_id == new_project_id)
        )).scalar_one()
        clips = (await db.execute(
            select(TimelineClipRecord).where(TimelineClipRecord.sequence_id == sequence.id)
        )).scalars().all()
        track_ids = set((await db.execute(
            select(TimelineTrackRecord.id).where(TimelineTrackRecord.sequence_id == sequence.id)
        )).scalars().all())
        assert len(clips) == 2 and {clip.track_id for clip in clips} <= track_ids
    await _assert_fk_integrity(maker)


async def test_force_delete_of_take_asset_deletes_children_before_parent(env) -> None:
    """Without the explicit flush the asset DELETE ran first, the FK cascade removed the
    take and the ORM's own take DELETE matched 0 rows (SAWarning)."""
    client, maker = env
    project_id = await _project_with_rendered_takes(client)
    async with maker() as db:
        take = (await db.execute(
            select(ShotTakeRecord).where(ShotTakeRecord.project_id == project_id).limit(1)
        )).scalar_one()
        take_id, asset_id = take.id, take.asset_id

    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        response = await client.delete(f"/api/v1/assets/{asset_id}?force=true")
    assert response.status_code == 204, response.text
    async with maker() as db:
        assert await db.get(ShotTakeRecord, take_id) is None
    await _assert_fk_integrity(maker)

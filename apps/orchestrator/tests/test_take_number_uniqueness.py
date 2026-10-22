from __future__ import annotations

from pathlib import Path

import pytest
from muse_orchestrator.core.database import _run_alembic_upgrade
from muse_orchestrator.models.domain import (
    Asset,
    Base,
    GenerationTaskRecord,
    Project,
    ProviderConnection,
    QueueTaskState,
    SceneRecord,
    ShotRecord,
    ShotTakeRecord,
)
from muse_orchestrator.services.generation_queue import _insert_take_with_number_retry
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _make_take(take_id: str, take_number: int, *, selected: bool = False) -> ShotTakeRecord:
    return ShotTakeRecord(
        id=take_id,
        project_id="project_test",
        shot_id="shot_test",
        take_number=take_number,
        task_id=f"task_{take_id}",
        asset_id=f"asset_{take_id}",
        model_id="test-model",
        prompt_hash="test-prompt-hash",
        is_selected=selected,
    )


async def _seed_take_parents(session: AsyncSession, take_ids: list[str]) -> None:
    """Parent rows for _make_take; FKs are enforced, so each level is flushed first."""
    session.add(Project(id="project_test", name="Take numbers"))
    session.add(ProviderConnection(
        id="conn_test", name="take-number-mock", provider_type="mock", base_url="http://localhost:9",
    ))
    await session.flush()
    session.add(SceneRecord(id="scene_test", project_id="project_test", scene_index=0, title="Scene"))
    await session.flush()
    session.add(ShotRecord(
        id="shot_test", project_id="project_test", scene_id="scene_test",
        sequence_index=0, title="Shot", location_code="loc",
    ))
    await session.flush()
    for take_id in take_ids:
        session.add(GenerationTaskRecord(
            id=f"task_{take_id}", project_id="project_test", scene_id="scene_test", shot_id="shot_test",
            connection_id="conn_test", model_id="test-model", idempotency_key=f"idem_{take_id}",
            state=QueueTaskState.SUCCEEDED,
        ))
        session.add(Asset(
            id=f"asset_{take_id}", project_id="project_test", asset_type="video", filename=f"{take_id}.mp4",
            storage_rel_path=f"project_test/{take_id}.mp4", mime_type="video/mp4", size_bytes=1, sha256="x",
        ))
    await session.commit()


@pytest.mark.asyncio
async def test_take_insert_retries_after_take_number_unique_conflict(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'take-number.db'}", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with sessions() as session:
        await _seed_take_parents(session, ["first", "second"])

    async with sessions() as session:
        session.add(_make_take("first", 1, selected=True))
        await session.commit()

    async with sessions() as session:
        second = _make_take("second", 1, selected=True)
        inserted = await _insert_take_with_number_retry(session, second)
        assert inserted is True
        assert second.take_number == 2
        assert second.is_selected is False
        await session.commit()

        numbers = (
            await session.execute(
                select(ShotTakeRecord.take_number)
                .where(ShotTakeRecord.shot_id == "shot_test")
                .order_by(ShotTakeRecord.take_number)
            )
        ).scalars().all()
        assert numbers == [1, 2]

    await engine.dispose()


def test_legacy_duplicate_take_numbers_are_repaired_by_the_real_migration_path(
    tmp_path: Path, monkeypatch,
) -> None:
    """TEST-005: a pre-Alembic DB is repaired by `alembic upgrade head` (what init_db runs)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "legacy-pre-alembic.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}", future=True)
    try:
        with engine.begin() as connection:
            # Legacy shape: no unique (shot_id, take_number) and no account_id_used column.
            connection.execute(text(
                "CREATE TABLE shot_takes ("
                "id TEXT PRIMARY KEY, shot_id TEXT NOT NULL, "
                "take_number INTEGER NOT NULL, created_at TEXT NOT NULL)"
            ))
            connection.execute(text(
                "CREATE TABLE generation_tasks ("
                "id TEXT PRIMARY KEY, shot_id TEXT NOT NULL, task_type TEXT NOT NULL, "
                "state TEXT NOT NULL, created_at TEXT)"
            ))
            connection.execute(
                text(
                    "INSERT INTO shot_takes (id, shot_id, take_number, created_at) "
                    "VALUES (:id, :shot_id, :take_number, :created_at)"
                ),
                [
                    {"id": "take_a", "shot_id": "shot_a", "take_number": 1, "created_at": "2026-01-01"},
                    {"id": "take_b", "shot_id": "shot_a", "take_number": 1, "created_at": "2026-01-02"},
                    {"id": "take_c", "shot_id": "shot_a", "take_number": 2, "created_at": "2026-01-03"},
                    {"id": "take_d", "shot_id": "shot_b", "take_number": 1, "created_at": "2026-01-04"},
                ],
            )

        _run_alembic_upgrade(f"sqlite+aiosqlite:///{database_path}")

        with engine.begin() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0002_uniqueness"
            )
            task_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(generation_tasks)"))}
            assert "account_id_used" in task_columns

            rows = connection.execute(
                text("SELECT id, shot_id, take_number FROM shot_takes ORDER BY id")
            ).all()
            values = {(row[0], row[1]): row[2] for row in rows}
            assert values[("take_a", "shot_a")] == 1
            assert values[("take_b", "shot_a")] == 3
            assert values[("take_c", "shot_a")] == 2
            assert values[("take_d", "shot_b")] == 1

            with pytest.raises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO shot_takes (id, shot_id, take_number, created_at) "
                    "VALUES ('take_duplicate', 'shot_a', 1, '2026-01-05')"
                ))
    finally:
        engine.dispose()

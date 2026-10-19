from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from muse_orchestrator.core.database import _ensure_schema_compatibility
from muse_orchestrator.models.domain import ShotTakeRecord
from muse_orchestrator.services.generation_queue import _insert_take_with_number_retry


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


@pytest.mark.asyncio
async def test_take_insert_retries_after_take_number_unique_conflict(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'take-number.db'}", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync_conn: ShotTakeRecord.__table__.create(sync_conn))
    sessions = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

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


def test_legacy_duplicate_take_numbers_are_repaired_before_unique_index_creation() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE shot_takes ("
                "id TEXT PRIMARY KEY, shot_id TEXT NOT NULL, "
                "take_number INTEGER NOT NULL, created_at TEXT NOT NULL)"
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

            _ensure_schema_compatibility(connection)

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
                )
    finally:
        engine.dispose()

from __future__ import annotations

from collections.abc import AsyncGenerator
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import Base

engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def _ensure_schema_compatibility(sync_conn) -> None:
    """Apply additive schema upgrades; create_all does not alter existing tables."""
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())

    if "generation_tasks" in tables:
        columns = {column["name"] for column in inspector.get_columns("generation_tasks")}
        if "account_id_used" not in columns:
            sync_conn.execute(text(
                "ALTER TABLE generation_tasks ADD COLUMN account_id_used VARCHAR(64)"
            ))

    if "shot_takes" not in tables:
        return

    # Old versions did not enforce uniqueness and concurrent workers may already
    # have written duplicate numbers. Keep the first row's number and move later
    # duplicates above the existing high-water mark before creating the constraint.
    rows = sync_conn.execute(text(
        "SELECT id, shot_id, take_number FROM shot_takes "
        "ORDER BY shot_id, take_number, id"
    )).fetchall()
    max_by_shot: dict[str, int] = {}
    for _take_id, shot_id, take_number in rows:
        max_by_shot[shot_id] = max(max_by_shot.get(shot_id, take_number), take_number)

    seen: set[tuple[str, int]] = set()
    for take_id, shot_id, take_number in rows:
        key = (shot_id, take_number)
        if key not in seen:
            seen.add(key)
            continue

        new_number = max_by_shot[shot_id] + 1
        sync_conn.execute(
            text("UPDATE shot_takes SET take_number = :take_number WHERE id = :take_id"),
            {"take_number": new_number, "take_id": take_id},
        )
        seen.add((shot_id, new_number))
        max_by_shot[shot_id] = new_number

    # Fresh tables created from the current metadata already have the named
    # UniqueConstraint. Existing tables need a unique index because create_all
    # does not migrate constraints onto them.
    inspector = inspect(sync_conn)
    expected_columns = {"shot_id", "take_number"}
    has_unique_constraint = any(
        set(item.get("column_names") or []) == expected_columns
        for item in inspector.get_unique_constraints("shot_takes")
    )
    has_unique_index = any(
        item.get("unique")
        and set(item.get("column_names") or []) == expected_columns
        for item in inspector.get_indexes("shot_takes")
    )
    if not has_unique_constraint and not has_unique_index:
        sync_conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_shot_takes_shot_take_number "
            "ON shot_takes (shot_id, take_number)"
        ))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_schema_compatibility)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

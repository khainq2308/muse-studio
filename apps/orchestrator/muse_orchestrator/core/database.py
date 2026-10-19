from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from muse_orchestrator.core.config import settings

engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def _ensure_schema_compatibility(sync_conn) -> None:
    """Apply small compatibility changes for existing databases.

    create_all creates new tables but does not add constraints to existing ones.
    Repair legacy duplicate take numbers first, then install a unique index so older
    databases receive the same guarantee as new databases.
    """
    inspector = inspect(sync_conn)
    table_names = set(inspector.get_table_names())

    if "generation_tasks" in table_names:
        columns = {column["name"] for column in inspector.get_columns("generation_tasks")}
        if "account_id_used" not in columns:
            sync_conn.execute(text(
                "ALTER TABLE generation_tasks ADD COLUMN account_id_used VARCHAR(64)"
            ))

    if "shot_takes" not in table_names:
        return

    # Fresh databases already have the UniqueConstraint from Base.metadata. Existing
    # databases receive a unique index once; skip the legacy-data scan on later starts.
    has_unique_constraint = any(
        set(constraint.get("column_names") or ()) == {"shot_id", "take_number"}
        for constraint in inspector.get_unique_constraints("shot_takes")
    )
    has_unique_index = any(
        index.get("unique")
        and set(index.get("column_names") or ()) == {"shot_id", "take_number"}
        for index in inspector.get_indexes("shot_takes")
    )
    if has_unique_constraint or has_unique_index:
        return

    rows = sync_conn.execute(text(
        "SELECT id, shot_id, take_number FROM shot_takes ORDER BY shot_id, created_at, id"
    )).fetchall()

    # Determine the maximum original number up-front so moved duplicates never steal
    # a number from a later row in the legacy data set.
    max_original: dict[str, int] = {}
    for row in rows:
        shot_id = str(row[1])
        number = int(row[2])
        max_original[shot_id] = max(max_original.get(shot_id, 0), number)

    used_numbers: dict[str, set[int]] = {}
    next_number = dict(max_original)
    for row in rows:
        take_id, raw_shot_id, raw_number = row[0], str(row[1]), int(row[2])
        used = used_numbers.setdefault(raw_shot_id, set())
        if raw_number in used:
            assigned = next_number[raw_shot_id] + 1
            next_number[raw_shot_id] = assigned
            sync_conn.execute(
                text("UPDATE shot_takes SET take_number = :take_number WHERE id = :take_id"),
                {"take_number": assigned, "take_id": take_id},
            )
            used.add(assigned)
        else:
            used.add(raw_number)

    # create_all cannot add a UniqueConstraint to an existing table.
    sync_conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_shot_takes_shot_number "
        "ON shot_takes (shot_id, take_number)"
    ))


def _run_alembic_upgrade(database_url: str | None = None) -> None:
    """Apply versioned schema migrations using a synchronous Alembic command runner.

    Alembic's environment creates its own async SQLAlchemy engine. Run the command
    in a worker thread from the application so its event loop remains unblocked.
    The optional URL is primarily used by isolated migration tests.
    """
    from alembic import command
    from alembic.config import Config

    config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    alembic_config = Config(str(config_path))
    target_url = database_url or settings.database_url
    alembic_config.set_main_option("sqlalchemy.url", target_url.replace("%", "%%"))
    command.upgrade(alembic_config, "head")


async def init_db() -> None:
    """Bring both new and pre-Alembic databases to the latest migration revision."""
    await asyncio.to_thread(_run_alembic_upgrade)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from muse_orchestrator.core.database import _run_alembic_upgrade


def test_alembic_bootstrap_is_idempotent_for_a_fresh_database(
    tmp_path: Path, monkeypatch,
) -> None:
    """Fresh installs create schema once and repeated startups stay at the same head."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "alembic-bootstrap.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path}"

    _run_alembic_upgrade(async_url)
    _run_alembic_upgrade(async_url)

    with create_engine(f"sqlite:///{database_path}").connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        assert {"projects", "shots", "generation_tasks", "shot_takes", "alembic_version"} <= tables

        constraints = inspector.get_unique_constraints("shot_takes")
        indexes = inspector.get_indexes("shot_takes")
        assert any(
            set(constraint.get("column_names") or ()) == {"shot_id", "take_number"}
            for constraint in constraints
        ) or any(
            index.get("unique")
            and set(index.get("column_names") or ()) == {"shot_id", "take_number"}
            for index in indexes
        )

        revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        assert revisions == ["0001_bootstrap"]

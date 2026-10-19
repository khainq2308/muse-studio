from __future__ import annotations

from pathlib import Path

from sqlalchemy import Column, String, Table, create_engine, inspect, text

from muse_orchestrator.core.database import _run_alembic_upgrade
from muse_orchestrator.models.domain import Base as CurrentBase


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



def test_bootstrap_uses_frozen_schema_not_future_live_models(
    tmp_path: Path, monkeypatch,
) -> None:
    """A future ORM table must wait for its own Alembic revision, not appear in 0001."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "alembic-frozen-baseline.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    probe = Table(
        "future_model_probe",
        CurrentBase.metadata,
        Column("id", String(64), primary_key=True),
    )

    try:
        _run_alembic_upgrade(async_url)
        with create_engine(f"sqlite:///{database_path}").connect() as connection:
            assert "future_model_probe" not in set(inspect(connection).get_table_names())
    finally:
        CurrentBase.metadata.remove(probe)

from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from muse_orchestrator.core.database import _run_alembic_upgrade
from muse_orchestrator.models.domain import Base as CurrentBase
from sqlalchemy import Column, String, Table, create_engine, inspect, text

HEAD_REVISION = "0002_uniqueness"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


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
        assert revisions == [HEAD_REVISION]


def test_revision_history_is_linear_and_ends_at_expected_head() -> None:
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    assert script.get_heads() == [HEAD_REVISION]
    history = [revision.revision for revision in script.walk_revisions(base="base", head="heads")]
    assert list(reversed(history)) == ["0001_bootstrap", HEAD_REVISION]


def test_migrated_schema_matches_orm_metadata_without_drift(tmp_path: Path, monkeypatch) -> None:
    """TEST-004: alembic upgrade head on an empty DB must equal Base.metadata (create_all)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "alembic-drift.sqlite3"
    _run_alembic_upgrade(f"sqlite+aiosqlite:///{database_path}")

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            # No differences are ignored: SQLite reflection matches the models exactly.
            assert compare_metadata(context, CurrentBase.metadata) == []
    finally:
        engine.dispose()



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

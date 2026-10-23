"""Migration 0002: duplicate repair and uniqueness guarantees (deep review 2)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from muse_orchestrator.models import schema_baseline_v1 as baseline
from muse_orchestrator.models.domain import Base as CurrentBase
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
HEAD_REVISION = "0004_lease_owner_token"


def _config(database_path: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _table(name: str):
    return baseline.Base.metadata.tables[name]


def _task(task_id: str, shot_id: str, state, *, task_type: str = "video", minutes: int = 0) -> dict:
    return {
        "id": task_id,
        "project_id": "proj_a",
        "shot_id": shot_id,
        "connection_id": "conn_a",
        "task_type": task_type,
        "model_id": "model",
        "idempotency_key": f"idem_{task_id}",
        "state": state,
        "created_at": T0 + timedelta(minutes=minutes),
    }


def _seed_parents(connection: Connection, tables, *, projects: bool = True) -> None:
    """Parent rows referenced by _task/_seed_duplicates; SQLite enforces FKs like PostgreSQL.

    Without real parents an FK violation would also raise IntegrityError and could mask
    whether a uniqueness guarantee is actually enforced.
    """
    if projects:
        connection.execute(insert(tables["projects"]), [{"id": "proj_a", "name": "A"}])
    connection.execute(insert(tables["projects"]), [{"id": "proj_b", "name": "B"}])
    connection.execute(insert(tables["provider_connections"]), [
        {"id": "conn_a", "name": "conn-a", "base_url": "http://localhost:9"},
    ])
    connection.execute(insert(tables["scenes"]), [
        {"id": "scn_a", "project_id": "proj_a", "scene_index": 0, "title": "Scene"},
    ])
    connection.execute(insert(tables["shots"]), [
        {"id": shot_id, "project_id": "proj_a", "scene_id": "scn_a", "sequence_index": index,
         "title": "Shot", "location_code": "loc"}
        for index, shot_id in enumerate(("shot_a", "shot_b"))
    ])


def _seed_duplicates(connection: Connection) -> None:
    """Insert pre-0002 duplicates through the frozen baseline tables (ORM-style storage)."""
    connection.execute(insert(_table("projects")), [
        {"id": "proj_a", "name": "A", "current_revision_num": 1},
    ])
    _seed_parents(connection, baseline.Base.metadata.tables, projects=False)
    connection.execute(insert(_table("workflows")), [
        {"id": "wf_a", "project_id": "proj_a", "name": "WF", "current_version": 2},
    ])
    connection.execute(insert(_table("workflow_versions")), [
        {"id": "wfv_1", "workflow_id": "wf_a", "version_number": 1, "created_at": T0},
        {"id": "wfv_2", "workflow_id": "wf_a", "version_number": 2, "created_at": T0 + timedelta(minutes=1)},
        {"id": "wfv_3", "workflow_id": "wf_a", "version_number": 2, "created_at": T0 + timedelta(minutes=2)},
        {"id": "wfv_4", "workflow_id": "wf_a", "version_number": 2, "created_at": T0 + timedelta(minutes=3)},
    ])
    connection.execute(insert(_table("project_revisions")), [
        {"id": "rev_1", "project_id": "proj_a", "revision_number": 1, "created_at": T0},
        {"id": "rev_2", "project_id": "proj_a", "revision_number": 1, "created_at": T0 + timedelta(minutes=1)},
    ])
    connection.execute(insert(_table("script_revisions")), [
        {"id": "scr_1", "project_id": "proj_a", "version_number": 1, "raw_content": "a", "created_at": T0},
        {"id": "scr_2", "project_id": "proj_a", "version_number": 1, "raw_content": "b",
         "created_at": T0 + timedelta(minutes=1)},
    ])
    connection.execute(insert(_table("story_bible_entries")), [
        {"id": "bib_old", "project_id": "proj_a", "entry_type": "character", "code_name": "HERO",
         "display_name": "Old", "updated_at": T0},
        {"id": "bib_new", "project_id": "proj_a", "entry_type": "character", "code_name": "HERO",
         "display_name": "New", "updated_at": T0 + timedelta(days=1)},
        {"id": "bib_mid", "project_id": "proj_a", "entry_type": "character", "code_name": "HERO",
         "display_name": "Mid", "updated_at": T0 + timedelta(hours=1)},
        # Pre-existing name equal to the first generated suffix must not be reused.
        {"id": "bib_taken", "project_id": "proj_a", "entry_type": "character", "code_name": "HERO__dup1",
         "display_name": "Taken", "updated_at": T0},
        # Same code name in another project is not a duplicate.
        {"id": "bib_other", "project_id": "proj_b", "entry_type": "character", "code_name": "HERO",
         "display_name": "Other", "updated_at": T0},
    ])
    state = baseline.QueueTaskState
    connection.execute(insert(_table("generation_tasks")), [
        _task("tsk_old_running", "shot_a", state.RUNNING, minutes=0),
        _task("tsk_mid_paused", "shot_a", state.PAUSED, minutes=1),
        _task("tsk_new_queued", "shot_a", state.QUEUED, minutes=2),
        _task("tsk_done", "shot_a", state.SUCCEEDED, minutes=3),
        _task("tsk_image", "shot_a", state.QUEUED, task_type="image", minutes=0),
        _task("tsk_other_shot", "shot_b", state.RUNNING, minutes=0),
    ])


def _values(connection: Connection, sql: str) -> dict:
    return {row[0]: tuple(row[1:]) if len(row) > 2 else row[1] for row in connection.execute(text(sql))}


def test_enum_storage_uses_names_matching_the_partial_index_predicate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "enum-storage.sqlite3"
    command.upgrade(_config(database_path), "0001_bootstrap")
    with create_engine(f"sqlite:///{database_path}").begin() as connection:
        _seed_parents(connection, baseline.Base.metadata.tables)
        connection.execute(insert(_table("generation_tasks")), [
            _task("tsk_1", "shot_a", baseline.QueueTaskState.QUEUED),
        ])
        assert connection.execute(text("SELECT state FROM generation_tasks")).scalar_one() == "QUEUED"


def test_upgrade_repairs_duplicates_without_deleting_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "review2-repair.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0001_bootstrap")
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            _seed_duplicates(connection)

        command.upgrade(config, "head")

        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                HEAD_REVISION
            )
            # Earliest row keeps its number; later duplicates move above the original max.
            assert _values(connection, "SELECT id, version_number FROM workflow_versions") == {
                "wfv_1": 1, "wfv_2": 2, "wfv_3": 3, "wfv_4": 4,
            }
            assert _values(connection, "SELECT id, current_version FROM workflows") == {"wf_a": 4}
            assert _values(connection, "SELECT id, revision_number FROM project_revisions") == {
                "rev_1": 1, "rev_2": 2,
            }
            assert _values(connection, "SELECT id, current_revision_num FROM projects") == {
                # proj_b (parent of a non-duplicate bible entry) has no duplicate revisions.
                "proj_a": 2, "proj_b": 1,
            }
            assert _values(connection, "SELECT id, version_number FROM script_revisions") == {
                "scr_1": 1, "scr_2": 2,
            }
            # Most recently updated entry keeps the code name; others get a free suffix.
            assert _values(connection, "SELECT id, code_name FROM story_bible_entries") == {
                "bib_new": "HERO",
                "bib_mid": "HERO__dup2",
                "bib_old": "HERO__dup3",
                "bib_taken": "HERO__dup1",
                "bib_other": "HERO",
            }
            tasks = _values(
                connection,
                "SELECT id, state, error_category, error_message_redacted, finished_at FROM generation_tasks",
            )
            assert tasks["tsk_new_queued"][:2] == ("QUEUED", None)
            assert tasks["tsk_done"][:2] == ("SUCCEEDED", None)
            assert tasks["tsk_image"][:2] == ("QUEUED", None)
            assert tasks["tsk_other_shot"][:2] == ("RUNNING", None)
            for canceled in ("tsk_old_running", "tsk_mid_paused"):
                state, category, message, finished_at = tasks[canceled]
                assert (state, category) == ("CANCELED", "duplicate_active_task")
                assert "tsk_new_queued" in message
                assert finished_at is not None

            with pytest.raises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO workflow_versions (id, workflow_id, version_number, schema_version, "
                    "nodes_json, edges_json, ui_layout_json) "
                    "VALUES ('wfv_dup', 'wf_a', 1, '1.0', '[]', '[]', '{}')"
                ))
    finally:
        engine.dispose()


def test_downgrade_drops_constraints_and_reupgrade_succeeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "review2-downgrade.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "head")
    command.downgrade(config, "0001_bootstrap")
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0001_bootstrap"
            )
            # Duplicates are accepted again without the 0002 constraints.
            _seed_duplicates(connection)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                HEAD_REVISION
            )
    finally:
        engine.dispose()


def test_create_all_models_enforce_the_same_guarantees() -> None:
    """Tests use create_all; the ORM metadata must carry the 0002 constraints too."""
    engine = create_engine("sqlite:///:memory:")
    try:
        CurrentBase.metadata.create_all(engine)
        with engine.begin() as connection:
            _seed_parents(connection, CurrentBase.metadata.tables)
            connection.execute(insert(CurrentBase.metadata.tables["generation_tasks"]), [
                _task("tsk_done_1", "shot_a", "SUCCEEDED"),
                _task("tsk_done_2", "shot_a", "CANCELED"),
                _task("tsk_active", "shot_a", "QUEUED"),
                _task("tsk_image", "shot_a", "RUNNING", task_type="image"),
            ])
        for state in ("QUEUED", "RUNNING", "PAUSED", "AWAITING_INPUT"):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(insert(CurrentBase.metadata.tables["generation_tasks"]), [
                    _task(f"tsk_dup_{state}", "shot_a", state),
                ])
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert(CurrentBase.metadata.tables["story_bible_entries"]), [
                {"id": "b1", "project_id": "proj_a", "entry_type": "character", "code_name": "X",
                 "display_name": "X"},
                {"id": "b2", "project_id": "proj_a", "entry_type": "location", "code_name": "X",
                 "display_name": "X"},
            ])
    finally:
        engine.dispose()


def _index_sql(connection: Connection) -> str:
    return connection.execute(text(
        "SELECT sql FROM sqlite_master WHERE type = 'index' "
        "AND name = 'uq_generation_tasks_active_shot_type'"
    )).scalar_one()


def _task_columns(connection: Connection) -> dict[str, str]:
    return {row[1]: row[2] for row in connection.execute(text("PRAGMA table_info(generation_tasks)"))}


def test_0003_awaiting_input_holds_the_active_slot_and_downgrades_to_running(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "review-0003.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0002_uniqueness")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            assert "AWAITING_INPUT" in _index_sql(connection)
            columns = _task_columns(connection)
            assert columns["state"] == "VARCHAR(14)"
            assert "pending_input_json" in columns
            _seed_parents(connection, CurrentBase.metadata.tables)
            awaiting = _task("tsk_wait", "shot_a", "AWAITING_INPUT")
            awaiting["pending_input_json"] = {"kind": "question", "text": "Which?", "options": []}
            connection.execute(insert(CurrentBase.metadata.tables["generation_tasks"]), [awaiting])
        # One active task per shot: AWAITING_INPUT blocks a second active task.
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert(CurrentBase.metadata.tables["generation_tasks"]), [
                _task("tsk_second", "shot_a", "QUEUED"),
            ])

        command.downgrade(config, "0002_uniqueness")
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT state FROM generation_tasks WHERE id = 'tsk_wait'")
            ).scalar_one() == "RUNNING"
            columns = _task_columns(connection)
            assert "pending_input_json" not in columns
            assert columns["state"] == "VARCHAR(12)"
            assert "AWAITING_INPUT" not in _index_sql(connection)
            assert "PAUSED" in _index_sql(connection)

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == HEAD_REVISION
            assert "AWAITING_INPUT" in _index_sql(connection)
    finally:
        engine.dispose()


def _lease_columns(connection: Connection) -> set[str]:
    return {row[1] for row in connection.execute(text("PRAGMA table_info(generation_dispatch_leases)"))}


def test_0004_lease_owner_token_round_trip_keeps_existing_leases(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "review-0004.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0003_awaiting_input")
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            assert "owner_token" not in _lease_columns(connection)
            _seed_parents(connection, CurrentBase.metadata.tables)
            connection.execute(insert(CurrentBase.metadata.tables["generation_tasks"]), [
                _task("tsk_lease", "shot_a", "RUNNING"),
            ])
            # A lease written by pre-0004 code: the table has no owner token column yet.
            connection.execute(text(
                "INSERT INTO generation_dispatch_leases (task_id, connection_id, expires_at, created_at) "
                "VALUES ('tsk_lease', 'conn_a', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ))

        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert "owner_token" in _lease_columns(connection)
            assert connection.execute(text(
                "SELECT owner_token FROM generation_dispatch_leases WHERE task_id = 'tsk_lease'"
            )).scalar_one() is None
            connection.execute(text(
                "UPDATE generation_dispatch_leases SET owner_token = 'tok' WHERE task_id = 'tsk_lease'"
            ))

        command.downgrade(config, "0003_awaiting_input")
        with engine.begin() as connection:
            assert "owner_token" not in _lease_columns(connection)
            assert connection.execute(text(
                "SELECT count(*) FROM generation_dispatch_leases"
            )).scalar_one() == 1

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == HEAD_REVISION
            assert "owner_token" in _lease_columns(connection)
    finally:
        engine.dispose()

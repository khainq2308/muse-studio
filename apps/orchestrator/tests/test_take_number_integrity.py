from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
import pytest

from muse_orchestrator.core.database import _ensure_schema_compatibility
from muse_orchestrator.models.domain import Base


def test_fresh_schema_declares_unique_take_number_per_shot() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            constraints = inspect(connection).get_unique_constraints("shot_takes")

        assert any(
            constraint.get("name") == "uq_shot_takes_shot_take_number"
            and constraint.get("column_names") == ["shot_id", "take_number"]
            for constraint in constraints
        )
    finally:
        engine.dispose()


def test_existing_schema_repairs_duplicate_take_numbers_and_adds_unique_index() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE shot_takes ("
                "id VARCHAR(64) PRIMARY KEY, "
                "shot_id VARCHAR(64) NOT NULL, "
                "take_number INTEGER NOT NULL, "
                "created_at VARCHAR(64)"
                ")"
            ))
            connection.execute(text(
                "INSERT INTO shot_takes (id, shot_id, take_number) VALUES "
                "('take_b', 'shot_a', 1), "
                "('take_a', 'shot_a', 1), "
                "('take_two', 'shot_a', 2), "
                "('take_other', 'shot_b', 1)"
            ))

            _ensure_schema_compatibility(connection)
            _ensure_schema_compatibility(connection)  # Migration is safe to rerun.

            rows = connection.execute(
                text("SELECT id, take_number FROM shot_takes WHERE shot_id = 'shot_a' ORDER BY id")
            ).fetchall()
            numbers = {take_id: number for take_id, number in rows}
            assert numbers == {"take_a": 1, "take_b": 3, "take_two": 2}

            indexes = inspect(connection).get_indexes("shot_takes")
            assert any(
                index.get("name") == "uq_shot_takes_shot_take_number" and index.get("unique")
                for index in indexes
            )

            with pytest.raises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO shot_takes (id, shot_id, take_number) "
                    "VALUES ('take_duplicate', 'shot_a', 2)"
                ))
    finally:
        engine.dispose()

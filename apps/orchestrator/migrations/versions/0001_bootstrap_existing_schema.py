"""Bootstrap existing schemas into Alembic history.

Revision ID: 0001_bootstrap
Revises:
Create Date: 2026-10-19

This one-time bridge creates any missing tables from a frozen schema snapshot and
applies compatibility repairs for pre-Alembic installations. Keep this revision
and its snapshot immutable. New schema changes must use explicit revisions.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

from muse_orchestrator.models.schema_baseline_v1 import Base as BaselineBase

revision = "0001_bootstrap"
down_revision = None
branch_labels = None
depends_on = None


def _ensure_bootstrap_compatibility(sync_conn) -> None:
    """Apply the frozen, one-time compatibility repair for legacy database schemas."""
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

    # Determine original maxima first so repaired duplicates never collide with
    # a larger take number assigned to a later row in the legacy dataset.
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


def upgrade() -> None:
    bind = op.get_bind()
    # This snapshot must not grow when live application models change; otherwise a
    # future column/table could appear in 0001 before its own explicit migration.
    BaselineBase.metadata.create_all(bind=bind, checkfirst=True)
    _ensure_bootstrap_compatibility(bind)


def downgrade() -> None:
    # This bridge adopts an already-running schema; dropping every application table
    # would destroy data in upgraded installations and cannot be a safe generic rollback.
    raise RuntimeError(
        "The schema bootstrap bridge cannot be downgraded safely. "
        "Restore a tested database backup instead of dropping application tables."
    )

"""Queue task state AWAITING_INPUT and the pending provider question.

Revision ID: 0003_awaiting_input
Revises: 0002_uniqueness
Create Date: 2026-10-23

Schema changes
--------------
* ``queuetaskstate`` gains the value ``AWAITING_INPUT`` (the provider asked the
  operator a question). ``generation_tasks.state`` stores enum *names* (upper case):
  - PostgreSQL: ``ALTER TYPE queuetaskstate ADD VALUE IF NOT EXISTS 'AWAITING_INPUT'``,
    run in an autocommit block because a new enum value cannot be used in the same
    transaction that adds it (the index below references it).
  - SQLite: the non-native enum is a VARCHAR sized to the longest name; it is widened
    from VARCHAR(12) to VARCHAR(14) with a batch table rebuild.
* ``uq_generation_tasks_active_shot_type`` is recreated so an AWAITING_INPUT task keeps
  occupying the (shot_id, task_type) active slot:
  ``WHERE state IN ('QUEUED', 'RUNNING', 'PAUSED', 'AWAITING_INPUT')``.
* New nullable JSON column ``generation_tasks.pending_input_json``.

No data repair is needed: no existing row can be AWAITING_INPUT before this revision.

Downgrade
---------
AWAITING_INPUT rows become RUNNING (the next poll re-reads the provider status; the
pending question is dropped with the column), the 0002 index predicate is restored,
``pending_input_json`` is dropped and SQLite's column is narrowed back.
PostgreSQL cannot drop a value from an enum type: ``AWAITING_INPUT`` stays in
``queuetaskstate`` after a downgrade (unused). Re-upgrading is safe because the
value is added with ``IF NOT EXISTS``.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_awaiting_input"
down_revision = "0002_uniqueness"
branch_labels = None
depends_on = None

ACTIVE_TASK_INDEX = "uq_generation_tasks_active_shot_type"
# Frozen predicates (enum names): a revision must not depend on live model code.
OLD_ACTIVE_TASK_PREDICATE = "state IN ('QUEUED', 'RUNNING', 'PAUSED')"
NEW_ACTIVE_TASK_PREDICATE = "state IN ('QUEUED', 'RUNNING', 'PAUSED', 'AWAITING_INPUT')"

_OLD_NAMES = ("QUEUED", "PAUSED", "RUNNING", "SUCCEEDED", "FAILED", "POLL_TIMEOUT", "CANCELED")
_NEW_NAMES = (*_OLD_NAMES, "AWAITING_INPUT")


def _sqlite_state_type(names: tuple[str, ...]) -> sa.Enum:
    # Non-native enum on SQLite renders as VARCHAR(len(longest name)), no CHECK constraint
    # (create_constraint defaults to False), exactly like sqlalchemy.Enum(QueueTaskState).
    return sa.Enum(*names, name="queuetaskstate", native_enum=False, create_constraint=False)


def _create_active_index(predicate: str) -> None:
    op.create_index(
        ACTIVE_TASK_INDEX,
        "generation_tasks",
        ["shot_id", "task_type"],
        unique=True,
        sqlite_where=sa.text(predicate),
        postgresql_where=sa.text(predicate),
    )


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE queuetaskstate ADD VALUE IF NOT EXISTS 'AWAITING_INPUT'")

    # Drop the partial index before any SQLite table rebuild so its WHERE clause is
    # never lost by batch-mode reflection; it is recreated with the new predicate.
    op.drop_index(ACTIVE_TASK_INDEX, table_name="generation_tasks")
    with op.batch_alter_table("generation_tasks") as batch_op:
        if dialect == "sqlite":
            batch_op.alter_column(
                "state",
                existing_type=_sqlite_state_type(_OLD_NAMES),
                type_=_sqlite_state_type(_NEW_NAMES),
                existing_nullable=False,
            )
        batch_op.add_column(sa.Column("pending_input_json", sa.JSON(), nullable=True))
    _create_active_index(NEW_ACTIVE_TASK_PREDICATE)


def downgrade() -> None:
    dialect = op.get_context().dialect.name
    op.execute(
        "UPDATE generation_tasks SET state = 'RUNNING' WHERE state = 'AWAITING_INPUT'"
    )
    op.drop_index(ACTIVE_TASK_INDEX, table_name="generation_tasks")
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_column("pending_input_json")
        if dialect == "sqlite":
            batch_op.alter_column(
                "state",
                existing_type=_sqlite_state_type(_NEW_NAMES),
                type_=_sqlite_state_type(_OLD_NAMES),
                existing_nullable=False,
            )
    _create_active_index(OLD_ACTIVE_TASK_PREDICATE)

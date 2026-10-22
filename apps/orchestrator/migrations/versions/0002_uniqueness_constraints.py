"""Enforce per-parent numbering/code-name uniqueness and one active task per shot.

Revision ID: 0002_uniqueness
Revises: 0001_bootstrap
Create Date: 2026-10-22

Schema changes
--------------
* ``workflow_versions``   UNIQUE (workflow_id, version_number)  -> uq_workflow_versions_workflow_number
* ``script_revisions``    UNIQUE (project_id, version_number)   -> uq_script_revisions_project_version
* ``project_revisions``   UNIQUE (project_id, revision_number)  -> uq_project_revisions_project_number
* ``story_bible_entries`` UNIQUE (project_id, code_name)        -> uq_story_bible_project_code_name
* ``generation_tasks``    partial UNIQUE INDEX (shot_id, task_type)
  WHERE state IN ('QUEUED', 'RUNNING', 'PAUSED')                -> uq_generation_tasks_active_shot_type

``generation_tasks.state`` is ``sqlalchemy.Enum(QueueTaskState)`` without
``values_callable``, so both SQLite (VARCHAR) and PostgreSQL (native enum
``queuetaskstate``) store the enum *names* (upper case), not the lower-case values.
The predicate therefore uses the names.

Pre-constraint data repair (online mode only; never deletes rows)
-----------------------------------------------------------------
* Numbered tables: rows are ordered per parent by ``created_at`` then ``id``. The
  earliest row keeps its number; each later duplicate is renumbered to
  ``max(original numbers for that parent) + 1, + 2, ...``. The parent's "current"
  pointer (``workflows.current_version`` / ``projects.current_revision_num``) is
  raised to the new maximum when it is lower, so the next application-assigned
  number cannot collide with a renumbered row.
* Story Bible: per (project_id, code_name) the most recently updated row
  (``updated_at`` desc, then ``created_at`` desc, then ``id`` desc) keeps the
  code name; every other row is renamed to ``<code_name>__dup<N>`` (truncated to
  fit 128 characters and never colliding with an existing code name).
* Generation tasks: per (shot_id, task_type) among QUEUED/RUNNING/PAUSED rows the
  newest (``created_at`` desc, ``id`` desc) stays active; the others become
  CANCELED with ``error_category='duplicate_active_task'`` and an explanatory
  ``error_message_redacted`` (when those columns exist) and ``finished_at`` set.
  A canceled RUNNING task's provider job is not contacted; its result is ignored.

Offline (``--sql``) rendering cannot inspect data, so it only emits the DDL.
Run the duplicate checks in the docstring's spirit before applying such a script;
the DDL fails safely (no data change) if duplicates remain.

Downgrade drops the constraints and the partial index. The data repair is not
reverted: renumbered rows, renamed code names and canceled duplicate tasks keep
their repaired values (no data is lost, original duplicate values are not restored).
"""
from __future__ import annotations

from alembic import context, op
from sqlalchemy import inspect, text

revision = "0002_uniqueness"
down_revision = "0001_bootstrap"
branch_labels = None
depends_on = None

ACTIVE_TASK_INDEX = "uq_generation_tasks_active_shot_type"
# Enum *names* as stored by sqlalchemy.Enum(QueueTaskState). Frozen here on purpose:
# a revision must not depend on live model code.
ACTIVE_TASK_PREDICATE = "state IN ('QUEUED', 'RUNNING', 'PAUSED')"
CODE_NAME_MAX_LENGTH = 128

NUMBERED_TABLES = (
    # table, constraint name, parent column, number column, (parent table, pointer column)
    (
        "workflow_versions", "uq_workflow_versions_workflow_number",
        "workflow_id", "version_number", ("workflows", "current_version"),
    ),
    (
        "script_revisions", "uq_script_revisions_project_version",
        "project_id", "version_number", None,
    ),
    (
        "project_revisions", "uq_project_revisions_project_number",
        "project_id", "revision_number", ("projects", "current_revision_num"),
    ),
)
STORY_BIBLE_CONSTRAINT = "uq_story_bible_project_code_name"


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _renumber_duplicates(bind, table: str, parent_col: str, number_col: str, pointer) -> None:
    rows = bind.execute(text(
        f"SELECT id, {parent_col}, {number_col} FROM {table} "
        f"ORDER BY {parent_col}, (created_at IS NULL), created_at, id"
    )).fetchall()

    max_original: dict[str, int] = {}
    for _row_id, parent, number in rows:
        max_original[parent] = max(max_original.get(parent, 0), int(number))

    used: dict[str, set[int]] = {}
    next_number = dict(max_original)
    changed_parents: set[str] = set()
    for row_id, parent, number in rows:
        seen = used.setdefault(parent, set())
        number = int(number)
        if number in seen:
            next_number[parent] += 1
            number = next_number[parent]
            bind.execute(
                text(f"UPDATE {table} SET {number_col} = :number WHERE id = :row_id"),
                {"number": number, "row_id": row_id},
            )
            changed_parents.add(parent)
        seen.add(number)

    if pointer is None or not changed_parents:
        return
    parent_table, pointer_col = pointer
    if parent_table not in inspect(bind).get_table_names() or pointer_col not in _columns(bind, parent_table):
        return
    for parent in changed_parents:
        bind.execute(
            text(
                f"UPDATE {parent_table} SET {pointer_col} = :maximum "
                f"WHERE id = :parent AND ({pointer_col} IS NULL OR {pointer_col} < :maximum)"
            ),
            {"maximum": next_number[parent], "parent": parent},
        )


def _rename_duplicate_code_names(bind) -> None:
    rows = bind.execute(text(
        "SELECT id, project_id, code_name FROM story_bible_entries "
        "ORDER BY project_id, code_name, (updated_at IS NULL), updated_at DESC, "
        "(created_at IS NULL), created_at DESC, id DESC"
    )).fetchall()

    taken: dict[str, set[str]] = {}
    for _row_id, project_id, code_name in rows:
        taken.setdefault(project_id, set()).add(code_name)

    keepers: set[tuple[str, str]] = set()
    for row_id, project_id, code_name in rows:
        key = (project_id, code_name)
        if key not in keepers:
            keepers.add(key)  # first row of the group is the most recently updated one
            continue
        names = taken[project_id]
        suffix_number = 1
        while True:
            suffix = f"__dup{suffix_number}"
            candidate = f"{code_name[: CODE_NAME_MAX_LENGTH - len(suffix)]}{suffix}"
            if candidate not in names:
                break
            suffix_number += 1
        names.add(candidate)
        bind.execute(
            text("UPDATE story_bible_entries SET code_name = :code_name WHERE id = :row_id"),
            {"code_name": candidate, "row_id": row_id},
        )


def _cancel_duplicate_active_tasks(bind) -> None:
    rows = bind.execute(text(
        "SELECT id, shot_id, task_type FROM generation_tasks "
        f"WHERE {ACTIVE_TASK_PREDICATE} "
        "ORDER BY shot_id, task_type, (created_at IS NULL), created_at DESC, id DESC"
    )).fetchall()

    kept_by_key: dict[tuple[str, str], str] = {}
    duplicates: list[tuple[str, str]] = []
    for task_id, shot_id, task_type in rows:
        key = (shot_id, task_type)
        if key in kept_by_key:
            duplicates.append((task_id, kept_by_key[key]))
        else:
            kept_by_key[key] = task_id  # first row of the group is the newest one
    if not duplicates:
        return

    columns = _columns(bind, "generation_tasks")
    assignments = ["state = 'CANCELED'"]
    if "error_category" in columns:
        assignments.append("error_category = 'duplicate_active_task'")
    if "error_message_redacted" in columns:
        assignments.append("error_message_redacted = :message")
    if "retryable" in columns:
        assignments.append("retryable = :retryable")
    if "finished_at" in columns:
        assignments.append("finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP)")
    if "updated_at" in columns:
        assignments.append("updated_at = CURRENT_TIMESTAMP")
    statement = text(
        f"UPDATE generation_tasks SET {', '.join(assignments)} WHERE id = :task_id"
    )
    for task_id, kept_task_id in duplicates:
        params = {"task_id": task_id}
        if "error_message_redacted" in columns:
            params["message"] = (
                "Canceled by migration 0002_uniqueness: another active task "
                f"({kept_task_id}) exists for the same shot and task type."
            )
        if "retryable" in columns:
            params["retryable"] = False
        bind.execute(statement, params)


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            "-- 0002_uniqueness: duplicate repair requires online mode; "
            "verify there are no duplicates before applying the DDL below"
        )
    else:
        bind = op.get_bind()
        for table, _name, parent_col, number_col, pointer in NUMBERED_TABLES:
            _renumber_duplicates(bind, table, parent_col, number_col, pointer)
        _rename_duplicate_code_names(bind)
        _cancel_duplicate_active_tasks(bind)

    for table, name, parent_col, number_col, _pointer in NUMBERED_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.create_unique_constraint(name, [parent_col, number_col])
    with op.batch_alter_table("story_bible_entries") as batch_op:
        batch_op.create_unique_constraint(STORY_BIBLE_CONSTRAINT, ["project_id", "code_name"])

    op.create_index(
        ACTIVE_TASK_INDEX,
        "generation_tasks",
        ["shot_id", "task_type"],
        unique=True,
        sqlite_where=text(ACTIVE_TASK_PREDICATE),
        postgresql_where=text(ACTIVE_TASK_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(ACTIVE_TASK_INDEX, table_name="generation_tasks")
    with op.batch_alter_table("story_bible_entries") as batch_op:
        batch_op.drop_constraint(STORY_BIBLE_CONSTRAINT, type_="unique")
    for table, name, _parent_col, _number_col, _pointer in reversed(NUMBERED_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(name, type_="unique")

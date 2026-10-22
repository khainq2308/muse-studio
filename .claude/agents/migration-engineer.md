---
name: migration-engineer
description: Owns database schema evolution with Alembic (async SQLAlchemy, PostgreSQL target, SQLite tests). Writes safe, reversible revisions on top of the existing 0001/0002 history and never adds ad-hoc ALTER TABLE logic. Use for any change to models/domain.py or database schema.
tools: Read, Grep, Glob, Edit, Write, Bash
model: inherit
---

You manage schema changes for MuseStudio. Reply in Vietnamese. Read `AGENTS.md`, `docs/AGENT_WORKFLOW.md` and `.agents/skills/alembic-migrations/SKILL.md`, then follow the skill exactly.

## Current state (verify, do not assume)
- Alembic is bootstrapped in `apps/orchestrator/migrations/` (config: `apps/orchestrator/alembic.ini`). `core/database.py:init_db` runs `alembic upgrade head` in a worker thread at startup; there is no startup `create_all` and no ad-hoc `ALTER TABLE` helper.
- Revisions: `0001_bootstrap` (one-time bridge: creates missing tables from the frozen `models/schema_baseline_v1.py`, adds `generation_tasks.account_id_used`, repairs duplicate take numbers; intentionally not downgradable) and `0002_uniqueness` (repairs duplicates without deleting rows, then adds UNIQUE constraints on workflow/script/project revision numbers and Story Bible code names, plus the partial unique index `uq_generation_tasks_active_shot_type`).
- `models/schema_baseline_v1.py` is frozen for 0001; never edit it. Live models are in `models/domain.py`; every model change needs a new revision.
- `sqlalchemy.Enum(...)` columns store enum *names* (for example `QUEUED`), so raw SQL and index predicates must use names.
- Production target is PostgreSQL. Most tests use SQLite with `create_all`; migration tests (`tests/test_database_migrations.py`, `tests/test_schema_review2.py`, `tests/test_take_number_uniqueness.py`) run the real Alembic upgrade on temporary SQLite files and assert zero autogenerate drift against `Base.metadata`.

## Rules
1. Never edit an existing revision; add a new one. The shared guard (`scripts/agent_guard/files.py`) blocks edits to revisions committed in `HEAD` and to `models/schema_baseline_v1.py`; a new, uncommitted revision stays editable.
2. Revisions must be reversible (`downgrade`) unless data loss is inherent — then say so in the docstring.
3. Be safe on populated tables: add columns as nullable or with server defaults, backfill in a separate step, add NOT NULL/constraints afterwards; create indexes explicitly. Repair duplicates before adding unique constraints without deleting user data, and document the strategy.
4. SQLite compatibility: use `op.batch_alter_table` where ALTER is needed; mirror every constraint and index in `models/domain.py` so `create_all` matches the migrated schema.
5. Never run migrations against a real database from the agent; the shell guard refuses `alembic upgrade/downgrade/stamp` unless the command uses `--sql` or an explicit SQLite URL. Validate upgrade/downgrade/upgrade on a throwaway SQLite file and render PostgreSQL DDL offline as described in the `alembic-migrations` skill, using a range such as `<previous>:head --sql` (online-only data-repair steps are skipped offline).
6. Do not reintroduce startup compatibility helpers. Existing installs reach head through `alembic upgrade head` (0001 adopts pre-Alembic schemas); use `alembic stamp` only for a database verified to already match a revision.
7. When adding a revision, update the expected head in `tests/test_database_migrations.py`.

## Done when
`alembic upgrade head`, `alembic downgrade -1` and a second `upgrade head` succeed on a fresh SQLite file, the drift test shows no differences against the models, tests are green, and docs explain any new operational step.

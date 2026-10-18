---
name: alembic-migrations
description: Bootstrap Alembic for the async SQLAlchemy orchestrator and write safe schema revisions (PostgreSQL target, SQLite for tests). Use whenever a model/table/column/index changes, when someone mentions migrations, schema drift, ALTER TABLE, create_all, or database upgrades, or when replacing _ensure_schema_compatibility.
---

# Alembic migrations for MuseStudio

## Detect the situation first
```bash
ls apps/orchestrator/migrations apps/orchestrator/alembic.ini 2>&1
grep -n "_ensure_schema_compatibility\|create_all" apps/orchestrator/muse_orchestrator/core/database.py
```
- No `migrations/` dir → **bootstrap** (below). Never add another hand-written `ALTER TABLE` to `core/database.py`.
- Exists → **new revision** (below).

## Bootstrap (one reviewable change)
1. From `apps/orchestrator`: `alembic init -t async migrations`.
2. `alembic.ini`: leave `sqlalchemy.url` empty; the URL comes from settings.
3. `migrations/env.py`: import `settings` and `Base` (`muse_orchestrator.models.domain`), set `target_metadata = Base.metadata`, set the URL from `settings.database_url`, enable `compare_type=True`, and `render_as_batch=True` when the dialect is SQLite. See `references/env-snippet.md`.
4. Baseline revision: autogenerate against an **empty** SQLite file → `alembic revision --autogenerate -m "baseline"`. Review it by hand: every table, index, FK, enum; include the `generation_tasks.account_id_used` column that `_ensure_schema_compatibility` adds today.
5. Production wiring: run `alembic upgrade head` at deploy/startup (a separate step or entrypoint), **not** inside every worker. Keep `create_all` only for tests.
6. Existing installs already have tables: document `alembic stamp head` (databases created by the old code) versus `alembic upgrade head` (empty databases) in the README.
7. Remove `_ensure_schema_compatibility` only after the baseline contains its change.

## New revision
1. Change `models/domain.py`.
2. `alembic revision --autogenerate -m "<imperative summary>"` (run against a throwaway SQLite DB that was upgraded to the previous head).
3. **Read and edit the generated file.** Autogenerate misses: server defaults, enum changes, renames (it emits drop+add — rewrite as `op.alter_column`/`rename_table`), data backfills.
4. Safe-change rules on populated tables:
   - New column: nullable (or `server_default`), backfill in a separate step, then tighten.
   - New NOT NULL/unique/FK: backfill and verify first; create indexes explicitly with `op.create_index`.
   - Dropping/renaming is a two-release change (stop using → drop).
   - Use `op.batch_alter_table` for SQLite-compatible ALTERs.
5. Write a real `downgrade()`. If irreversible, raise `NotImplementedError` with an explanation in the docstring.
6. Prove it (never against real data):
   ```bash
   cd apps/orchestrator && export DATABASE_URL=sqlite+aiosqlite:///./mig_check.db
   alembic upgrade head && alembic downgrade -1 && alembic upgrade head
   alembic check          # no pending autogenerate diff
   alembic upgrade head --sql | less   # review PostgreSQL DDL offline
   rm -f mig_check.db
   ```
7. Run `scripts/test.sh orchestrator`.

## Rules
- Revisions are immutable once merged (a hook blocks edits). Fix mistakes with a new revision.
- One logical change per revision; message in imperative mood.
- Never run migrations against a real/production database from the agent.

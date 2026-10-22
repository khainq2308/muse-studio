---
name: alembic-migrations
description: Safe, reversible Alembic migrations for MuseStudio's async SQLAlchemy backend.
---

# Alembic migrations for MuseStudio

Verify the migration directory/configuration and current database initialization before acting. If Alembic is absent, bootstrap it in a separate reviewable change. Never add startup ALTER TABLE code or edit an applied revision.

## New revision
1. Change the model and generate a revision against a disposable DB already upgraded to the prior head.
2. Review autogenerate output manually: renames, backfills, server defaults and enum changes need particular attention.
3. For populated tables, add nullable/server-default columns first, backfill separately, then add constraints and indexes.
4. Write a meaningful downgrade and document irreversible operations.
5. Test upgrade/downgrade/upgrade on a throwaway database, check model drift, and run the orchestrator tests. Name the throwaway database explicitly, for example `DATABASE_URL='sqlite+aiosqlite:////tmp/muse_mig.db' alembic upgrade head`; the shared shell guard refuses `alembic upgrade/downgrade/stamp` without `--sql` or an explicit SQLite URL.

## PostgreSQL offline DDL
Alembic output dialect comes from the configured URL. Do not use the default SQLite URL for PostgreSQL DDL. Where settings read DATABASE_URL, use a placeholder PostgreSQL URL for offline rendering only:

    cd apps/orchestrator
    DATABASE_URL='postgresql+asyncpg://user:password@localhost/muse' alembic upgrade head --sql

Confirm offline mode does not connect to a live database and use the actual setting name expected by the project. Review generated SQL; offline rendering is not a substitute for disposable-database migration tests.

Never migrate a production database from an agent session. Keep existing-install stamp instructions separate from fresh-install upgrade instructions.

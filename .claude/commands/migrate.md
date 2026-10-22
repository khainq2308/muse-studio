---
description: Create an Alembic migration for a schema change
argument-hint: "<what changes, e.g. 'add shot_takes.quality_score float nullable'>"
---
Schema task: `$ARGUMENTS`

Delegate to the **migration-engineer** sub-agent and follow `.agents/skills/alembic-migrations/SKILL.md`.
- Verify the current head in apps/orchestrator/migrations/versions/ and tests/test_database_migrations.py first; add a new revision on top of it.
- Show me the generated revision, the upgrade → downgrade → upgrade proof on a throwaway SQLite file, the drift-test result, and the offline PostgreSQL SQL (`<previous>:head --sql`) for review. Never run against a real database.

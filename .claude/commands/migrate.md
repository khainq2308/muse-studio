---
description: Create or bootstrap an Alembic migration for a schema change
argument-hint: "<what changes, e.g. 'add shot_takes.quality_score float nullable' or 'bootstrap alembic'>"
---
Schema task: `$ARGUMENTS`

Delegate to the **migration-engineer** sub-agent and follow the `alembic-migrations` skill.
- If Alembic is not set up yet and the task is not "bootstrap", do the bootstrap first as a separate, reviewable step.
- Show me the generated revision, the upgrade/downgrade proof on a fresh SQLite file, and the offline PostgreSQL SQL (`alembic upgrade head --sql`) for review. Never run against a real database.

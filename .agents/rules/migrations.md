---
trigger: glob
globs: apps/orchestrator/migrations/**
description: Alembic revision safety for apps/orchestrator/migrations.
---

Follow `.agents/skills/alembic-migrations/SKILL.md`. Never edit a committed revision or `models/schema_baseline_v1.py` (the shared hook blocks both); add a new reversible revision, mirror constraints in `models/domain.py`, update the expected head in `tests/test_database_migrations.py`, and prove upgrade/downgrade/upgrade only on a throwaway SQLite file or with `--sql`. Never migrate a real database.

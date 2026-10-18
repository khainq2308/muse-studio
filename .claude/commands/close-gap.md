---
description: Close one known gap from the review (arq-worker, alembic, boundary-frames, web-foundation, contracts, ci-hardening, docker-prod)
argument-hint: "<gap-name>"
---
Work on gap: `$ARGUMENTS`. First run `python3 scripts/check_docs_sync.py` to confirm the gap still exists. Create a branch `feat/<gap-name>`.

| Gap | Goal | Agent | Acceptance |
|---|---|---|---|
| `arq-worker` | A worker service runs `process_generation_task_step` and the daily cleanup cron. Add `worker` service to docker-compose (same image, `command: arq muse_orchestrator.workers.queue_worker.WorkerSettings`, depends_on redis+postgres), make the API enqueue steps when tasks are created, document it in README. | backend-dev | Test proves enqueue + re-enqueue with `_defer_by`; compose config validates (`docker compose config`); README quick start mentions the worker. |
| `alembic` | Versioned schema with a baseline revision replacing `_ensure_schema_compatibility`. | migration-engineer | up/down on fresh SQLite works; no drift; upgrade path for existing installs documented. |
| `boundary-frames` | Real boundary frames via `apps/media-engine`; no fake PNG; mock bypass confined to a test seam. | media-engineer | Integration test with a lavfi-generated clip checks PNG validity + SHA-256. |
| `web-foundation` | Typed API client, `modules/` structure, routing, use `packages/contracts`, replace inline-style sprawl with a small shared style approach. | frontend-dev | `scripts/test.sh web` green; App.tsx is layout/routing only. |
| `contracts` | Make `packages/contracts` the single source of API types (and add the missing DTOs). | frontend-dev | web imports from contracts; docs-sync rule turns ok. |
| `ci-hardening` | CI runs `ruff check`, `ruff format --check`, `check_layering.py`, `check_docs_sync.py` (non-blocking at first), `npm ci` with a committed lockfile, pip caching per app. | backend-dev | CI file valid YAML; every new step runs locally via scripts/. |
| `docker-prod` | Separate dev/prod images: no `[dev]` extras, non-root user, no `--reload`, healthchecks, CORS origins from env. | backend-dev | `docker compose -f docker-compose.yml config` ok; settings tests cover CORS env. |

Use the right sub-agent from the table, keep the change focused on this gap only, run the relevant tests, and finish by re-running `check_docs_sync.py` to show the gap closed. Update docs via the **docs-sync** agent. Do not claim a gap closed unless its acceptance check actually passed here.

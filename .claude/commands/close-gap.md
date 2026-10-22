---
description: Close one known implementation gap using evidence-based acceptance criteria
argument-hint: "<gap-name or milestone, e.g. 'M6 timeline editor UI'>"
---
Work only on the requested gap: `$ARGUMENTS`.

1. Run python3 scripts/audit_roadmap.py and read the matching milestone in docs/IMPLEMENTATION_PLAN.md. Then inspect the actual implementation and tests to confirm the gap still exists. The script is heuristic; its output or exit code alone does not prove a gap is open or closed. If the gap is already closed, report the evidence and stop.
2. Take acceptance criteria from the plan/architecture docs (docs/FRONTEND_ARCHITECTURE.md for UI work). Where they are missing, define observable criteria and state them before editing. Use a focused feature branch.
3. Use the relevant specialist and shared skill (frontend-dev + frontend-feature for UI, backend-dev + add-api-endpoint for API, media-engineer + safe-ffmpeg for media, migration-engineer + alembic-migrations for schema). Keep the change focused and add regression tests.
4. Run the acceptance gates from docs/AGENT_WORKFLOW.md (Phase D), then re-run python3 scripts/audit_roadmap.py and python3 scripts/check_docs_sync.py. Update docs only from evidence.

Never claim closure without acceptance tests or runtime evidence, and do not start adjacent gaps automatically.

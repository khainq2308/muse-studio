---
description: Close one known implementation gap using evidence-based acceptance criteria
argument-hint: "<gap-name>"
---
Work only on the requested gap. First run python3 scripts/audit_roadmap.py and inspect the actual implementation/tests to verify this specific gap still exists. The script uses heuristics; its exit code alone does not prove a particular gap.

Define observable acceptance criteria and use a focused feature branch when possible.

Gap goals:
- arq-worker: worker processes generation and cleanup tasks; API enqueues and re-enqueues jobs.
- alembic: versioned schema and documented existing-install upgrade path.
- boundary-frames: real PNGs via media-engine; no fake PNG outside an explicit test seam.
- web-foundation: typed API client, modular UI, routing and shared contracts.
- contracts: real web imports from packages/contracts.
- ci-hardening: CI runs lint, layering, docs and agent-kit validation.
- docker-prod: hardened production image and environment-driven CORS.

Use the relevant specialist, keep the change focused, add regression tests, run acceptance gates, then re-run audit_roadmap.py and check_docs_sync.py. Update docs only from evidence. Never claim closure without acceptance tests/runtime evidence, and do not start adjacent gaps automatically.

# Implementation Plan & Milestones

* **M0 (Completed):** Repo audit, Capability Matrix, DAG Engine, Prompt Compiler/Linter, Secret Redactor, Contract Tests.
* **M1 (Completed):** Project CRUD + Revisions, Encrypted Connection Profiles + Capability Probe, Safe Asset Storage, Studio Web M1 Dashboard.
* **M2 (Partial):** Script parser/backend primitives exist. Studio Web Shot Board part A (shot list grouped by scene, shot edit via `PATCH` with inline 422 errors and the recompiled prompt) is implemented and tested with unit tests and Playwright against a mocked API only; Script Studio, Story Bible and Shot Board take review (part B) are incomplete, and the Shot Board has no browser evidence against a real backend yet.
* **M3 (Partial):** DAG validation/planning/cache primitives exist; the React Flow workflow canvas and production node executors are incomplete.
* **M4 (Partial):** Queue/polling, the Queue Monitor UI (including operator questions) and the ARQ worker wiring are implemented; the `arq-e2e` CI job verifies enqueue → worker dispatch/poll → take, schedule-token dedupe, cancel, needs-input reply and recovery-cron re-scheduling with real Redis and separate API/worker processes, and a manual run against a real muse.ai account succeeded (docs/evidence/m4/2026-10-23-real-arq-worker.md). Remaining before completion: a real worker on PostgreSQL and worker crash/restart mid-task.
* **M5 (Partial):** Batch/take concepts exist; the Shot Board can enqueue a single shot and launch a confirmed batch, showing `frame_chain_continuity_not_guaranteed` warnings and blocked shots (mocked-API unit/Playwright tests only). Take review/A-B compare/select take/continuity flags in the UI are not built, and frame chaining must use real, validated boundary-frame PNG assets and pass an integration test.
* **M6 (Partial):** Backend FFmpeg export supports a subset of transitions; Timeline Editor UI, clip audio, title cards, subtitles and extra audio tracks are incomplete.
* **M7 (Partial):** Some operations/backup/observability slices exist; full production readiness requires acceptance evidence for accounts/proxies, workers, cleanup and end-to-end operations.

## Verification policy

scripts/audit_roadmap.py reports heuristic navigation signals, not behavior. scripts/check_docs_sync.py validates that each milestone has one explicit, allowed status and that capability-matrix status labels use the approved vocabulary; it deliberately does not infer completion from file names or dependencies. A milestone is complete only after implementation, acceptance tests and relevant runtime evidence are reviewed.

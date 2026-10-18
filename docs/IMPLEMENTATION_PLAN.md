# Implementation Plan & Milestones

* **M0 (Completed):** Repo audit, Capability Matrix, DAG Engine, Prompt Compiler/Linter, Secret Redactor, Contract Tests.
* **M1 (Completed):** Project CRUD + Revisions, Encrypted Connection Profiles + Capability Probe, Safe Asset Storage, Studio Web M1 Dashboard.
* **M2 (Partial):** Script parser/backend primitives exist; production Studio Web Script Studio, Story Bible and Shot Board UI are incomplete.
* **M3 (Partial):** DAG validation/planning/cache primitives exist; the React Flow workflow canvas and production node executors are incomplete.
* **M4 (Partial):** Queue/polling logic exists; production ARQ worker wiring and reliable API enqueue/re-enqueue require end-to-end verification.
* **M5 (Partial):** Batch/take concepts exist; frame chaining must use real, validated boundary-frame PNG assets and pass an integration test.
* **M6 (Partial):** Backend FFmpeg export supports a subset of transitions; Timeline Editor UI, clip audio, title cards, subtitles and extra audio tracks are incomplete.
* **M7 (Partial):** Some operations/backup/observability slices exist; full production readiness requires acceptance evidence for accounts/proxies, workers, cleanup and end-to-end operations.

## Verification policy

scripts/audit_roadmap.py reports heuristic navigation signals, not behavior. scripts/check_docs_sync.py validates that each milestone has one explicit, allowed status and that capability-matrix status labels use the approved vocabulary; it deliberately does not infer completion from file names or dependencies. A milestone is complete only after implementation, acceptance tests and relevant runtime evidence are reviewed.

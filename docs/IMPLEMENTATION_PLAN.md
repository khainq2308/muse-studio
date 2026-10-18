# Implementation Plan & Milestones

* **M0 (Completed):** Repo audit, Capability Matrix, DAG Engine, Prompt Compiler/Linter, Secret Redactor, Contract Tests.
* **M1 (Completed):** Project CRUD + Revisions, Encrypted Connection Profiles + Capability Probe, Safe Asset Storage, Studio Web M1 Dashboard.
* **M2 (Completed):** Script Studio, Story Bible (Character/Location/Style/Props), Shot Board CRUD & MuseFlow Script Parser.
* **M3 (Completed):** Workflow Canvas (React Flow + Typed DAG Execution).
* **M4 (Completed):** Live `muse2api` Async Queue & Non-blocking Polling Worker.
* **M5 (Completed):** Batch Render, Takes A/B Review & Frame Chaining.
* **M6 (Completed):** Timeline Editor & FFmpeg MP4 Export.
* **M7 (Completed):** Multi-Connection Accounts/Proxies (per-account API keys + proxy, least-recently-used selection in the generation queue), Observability Metrics (JSON summary + Prometheus exposition), Orphan Media Cleanup (scan/cleanup API + daily ARQ cron), Backup/Restore (project ZIP snapshots with ID remapping), System Operations (structured logs with secret redaction, redacted diagnostics bundle, audit trail on account/backup/cleanup actions — concepts ported from PR #9).

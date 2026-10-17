# Implementation Plan & Milestones

## Delivered baselines

- **M0 (Completed):** Repo audit, capability matrix, DAG engine, prompt compiler/linter, secret redactor, contract tests.
- **M1 (Completed):** Project CRUD/revisions, encrypted connection profiles and capability probe, safe asset storage, Studio Web foundation.
- **M2 (Baseline implemented):** Script Studio API, Story Bible, Shot Board, and MuseFlow script import/analysis.
- **M3 (Baseline implemented):** Workflow Canvas API, typed DAG validation/planning, persisted runs, cache path, and manual approval gates. Production execution for every node type is still a separate gap; simulated/placeholder outputs must remain clearly labeled.
- **M4 (Baseline implemented; production worker hardening remains):** Generation queue, upstream submit/poll logic, retry/backoff and task state. A separately deployed ARQ dispatcher/poller with restart recovery is not considered delivered until implemented and tested.
- **M5 (Baseline implemented):** Batch render coordination, Takes A/B review, continuity metadata, and frame chaining. Provider-specific capabilities must be reported only when verified.
- **M6 (Baseline implemented; production export validation remains):** Timeline assembly and export API. An ISO-BMFF fallback artifact used when FFmpeg binaries are unavailable in CI is not a substitute for a real playable MP4; production completion requires real FFmpeg/FFprobe validation.
  
## M7 — Operational reliability and scale (In progress)

**Scope:** Multi-connection accounts/proxies, observability metrics, orphan media cleanup, backup/restore.

- **M7.0 — Observability foundation (current slice):** Authenticated Prometheus-format HTTP counter/histogram endpoint, bounded route-template labels, tests, and operational documentation.
- **M7.1 — Accounts and real proxy routing:** Explicit account/proxy domain and secret handling, actual outbound HTTP proxy transport, safe account selection/concurrency/cooldown semantics, schema upgrade path, and adapter tests.
- **M7.2 — Queue/provider metrics:** Bounded-cardinality task/provider metrics with retry/error/latency signals.
- **M7.3 — Orphan media cleanup:** Dry-run reconciliation, age/grace period, path/symlink protections, explicit apply mode, and audit trail.
- **M7.4 — Backup/restore:** Coordinated database + media artifacts, manifest/checksums, staged restore, key-management and recovery runbook.

See [M7_IMPLEMENTATION_PLAN.md](M7_IMPLEMENTATION_PLAN.md) for constraints and acceptance criteria for each slice.

## Completion policy

A baseline milestone means the primary code path exists, not necessarily that every production operational concern is closed. Real upstream capability, persistent worker operation, playable media output, backup recovery, and destructive cleanup must be verified through implementation-specific tests and runbooks before being described as production-ready.

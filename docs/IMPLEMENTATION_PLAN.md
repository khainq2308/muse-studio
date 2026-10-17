# Implementation Plan & Milestones

Status snapshot: 2026-10-17, after merge commit ef5fc2f9c59791b82a9ab0cb70ec8b7f2f177ed9.

## M0 — Capability and architecture foundation
**Status: implemented baseline.** Repository audit, capability matrix, contract tests, typed DAG validation/planning, prompt compiler/linter, secret redaction, and the initial architecture docs exist. Provider capabilities remain mock-only or unverified unless a live probe has proven otherwise.

## M1 — Project and infrastructure foundation
**Status: implemented baseline.** Project CRUD/revisions, encrypted connection profiles and probes, protected product API routes, validated asset storage, FFprobe metadata inspection, boundary-frame extraction utilities, application services/repository for projects/connections/assets, and CI are present. This does not mean production video jobs or timeline export are implemented.

## M2 — Script-to-shot workspace
**Status: API foundation implemented; Web workspace in progress.**
- API/domain: source-script revisions and import/parser; Story Bible entries; scenes and shots; prompt compilation; reference blockers; approval state; continuity lint; Shot Board JSON/CSV export.
- Web vertical slice: Projects, Script Studio, Story Bible, and Shot Board. Source revision must be preserved. Applying a parsed proposal is destructive to the existing Story Bible/scenes/shots and must require clear user intent and confirmation when data already exists.
- Acceptance: project can be created and selected; a script can be saved as a revision without applying it; apply is an explicit action; Bible entries can be created/updated; shot prompt/lint is visible; shot properties and approval can be edited; unauthorized API errors are recoverable through session-only token entry; CI frontend build and backend tests pass.

## M3 — Workflow authoring and planning
**Status: typed DAG API and simulation baseline implemented; production UI/executors incomplete.**
- Present: typed graph parsing/validation, cycle checking, deterministic execution plans, cache keys, persisted run/node records, dry-run planning, and simulation outputs explicitly marked as simulated/placeholder.
- Remaining: full graph editor UI, real node executor registry, production execution persistence contracts, run progress/recovery, and auditable manual approval behavior. A simulation must never be presented as generated media.

## M4 — Verified provider queue
**Status: not production-ready.**
- Verify real provider endpoints, auth and response shapes via a controlled capability probe first.
- Add queue dispatch/poll worker, bounded concurrency, retry/backoff/jitter, timeout/cancel semantics, idempotency, secure redacted logs, and restart recovery. No in-process background task may stand in for production queueing.
- Use mocks/contract fixtures for CI; live provider tests remain opt-in and require configured credentials.

## M5 — Batch render and frame chaining
**Status: utilities exist; product execution path incomplete.**
- Add batch/take planning, per-shot input/output assets, human A/B review, previous-shot last-frame handoff, and explicit state/blockers.
- Persist boundary frames and media provenance through the asset model. Do not mark a shot generated until a verified provider result has been downloaded and media-validated.

## M6 — Timeline editor and export
**Status: not implemented.**
- Add timeline model/editor, ordering and transition semantics, FFmpeg export jobs, export validation, progress/status, and downloadable final MP4.
- Export must use validated local assets and must not block API requests.

## Execution protocol
Work on one bounded PR at a time. Never commit directly to main or auto-merge. Every milestone must state what is real, simulated, mock-only, or still unverified. Update this plan only when the source tree supports the claimed status.

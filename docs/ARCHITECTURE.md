# MuseStudio Technical Architecture

## Runtime shape

MuseStudio is a monorepo with separate deployment areas:

- apps/studio-web: React + TypeScript + Vite dashboard.
- apps/orchestrator: FastAPI API, application use cases, persistence, provider adapters and workflow planning.
- apps/media-engine: isolated FFprobe/FFmpeg utilities for validating video and extracting boundary frames.
- apps/studio-web/src/lib/api/schema.d.ts: generated frontend contracts from the orchestrator OpenAPI snapshot; no parallel handwritten API contract package.
- ops: operator probes and deployment helpers.

The orchestrator remains a modular monolith. The package boundaries are designed to keep the API and application rules independent from the database and external provider details; separate API, ARQ and media worker processes can be deployed when their execution paths are implemented and tested.

## Dependency direction

API routes -> application services -> infrastructure repositories.

The application services coordinate domain models and provider/storage adapters. Repository classes own SQLAlchemy queries and transaction boundaries. Routes own HTTP schemas, status codes and DTO serialization only. Domain validation should not instantiate HTTP clients or access environment state.

Current application services cover project create/list/duplicate/revision listing, connection create/list/probe, and asset upload/delete. The workflow API and the M2 pipeline still contain persistence/orchestration logic that should be moved behind use cases in a subsequent task; do not claim the complete backend has been fully layered yet.

## Persistence and transactions

- PostgreSQL is the Docker Compose deployment target; SQLite remains useful for isolated async tests.
- StudioRepository centralizes project, revision, connection and asset persistence used by application services.
- Project create writes the project, initial revision and audit event in one database transaction.
- Asset upload validates content before recording metadata; when the metadata transaction fails, the newly-written file is removed when safe to do so.
- Asset deletion rejects locked references unless the caller explicitly requests force, commits metadata deletion, then removes the resolved file path within the media root.
- The existing workflow/run/cache persistence is not yet moved into the repository layer.
- Production configuration refuses the development encryption key and requires STUDIO_ADMIN_TOKEN.

## HTTP access control

All product API routers are mounted with the shared verify_single_admin dependency. /healthz is intentionally public for liveness checks. In development with no admin token set, API access remains open to support local onboarding; production configuration must set both STUDIO_ADMIN_TOKEN and a non-default SECRET_ENCRYPTION_KEY.

Before public deployment, also configure TLS, restrictive CORS origins, rate limits and operational monitoring. An admin token is a single-admin gate, not multi-user authentication.

## Media engine and frame chaining

The reusable `muse_media` package lives in `apps/media-engine` and is installed as an editable local dependency of the orchestrator in local test setup and Docker. Production video-task completion now invokes `extract_boundary_frames` through `asyncio.to_thread`: FFprobe validates metadata and exact decoded frame count, FFmpeg extracts frame index zero and the last decoded frame, and the queue verifies nonempty PNG outputs before recording SHA-256-backed Asset rows for the first/last frames. Extraction errors fail the media acceptance path rather than persisting fake PNG artifacts.

The mock provider remains a demo/test seam and writes tiny valid PNG placeholders so queue/continuity tests can run without interpreting synthetic mock MP4 bytes as real video. Those mock frames are not evidence of real visual continuity. Passing an actual extracted frame to a provider still depends on that provider's accepted reference-image contract; the queue's local asset ID is not itself a publicly fetchable image URL.

## Timeline export

POST /api/v1/projects/{id}/timeline/exports renders the sequence's real video clips with FFmpeg: each clip is trimmed, scaled and padded to the output frame, then joined with a hard cut (concat) or cross dissolve (xfade); timeline gaps are filled with black. The exporter runs off the event loop and then verifies the file with FFprobe (H.264 video, AAC audio, expected size, duration within tolerance). Clips must be video assets that belong to the same project.

An export that cannot produce a verified MP4 is never reported as successful: a missing FFmpeg/FFprobe (503), an FFmpeg failure or a failed verification (422) is stored as a FAILED export job, returned as an error, and creates no asset. The finished file is served by GET /api/v1/assets/{asset_id}/download.

Not rendered yet, and listed as unsupported_features in every response of an export that reaches rendering (success, or a stored FAILED job): clip audio (the output carries a silent AAC track), title card, subtitles and extra audio tracks. Requests rejected before rendering (unknown sequence, clips without a project video) carry no such list.

Timeline placement is rendered faithfully or refused: a gap is filled with black, and a clip that overlaps the previous one is accepted only as a cross dissolve whose length equals the overlap. Any other overlap (for example a hard cut over the previous clip) fails the export with 422 rather than producing a video longer than the timeline.

## Provider media downloads

Muse2ApiAdapter.download_result sends the provider API key only to the provider's own origin. Result URLs on any other host must use https and never receive the key; redirects are followed manually (at most 3) and re-checked on every hop. Non-http(s) schemes, scheme-relative URLs and path-traversal media ids are refused before any request is made.

## Queue and workflow execution status

The M3 workflow API validates typed DAGs, builds deterministic execution plans, stores run/node records, and supports manual approval/cache paths. Until a production executor is registered for a node type, run responses explicitly label its output as simulated/placeholder; legacy cache rows without execution provenance are ignored. It still does not execute arbitrary node implementations as production work. Redis and ARQ dependencies exist in the deployment configuration, but a separate production ARQ dispatch/poll worker and its restart-recovery contract are not considered delivered until implemented with tests.

## Continuous integration

.github/workflows/ci.yml runs agent-kit metadata/link checks and guard tests, orchestrator pytest, media-engine unit tests and the frontend TypeScript build on pull requests. The orchestrator test job installs FFmpeg/FFprobe and the local media-engine package; it includes a generated-video integration test for boundary-frame extraction. Media-engine unit tests mock subprocess calls. CI does not contact a real muse2api provider, and passing mocks do not verify external provider capabilities.

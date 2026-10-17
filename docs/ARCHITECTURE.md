# MuseStudio Technical Architecture

## Runtime shape

MuseStudio is a monorepo with separate deployment areas:

- apps/studio-web: React + TypeScript + Vite dashboard.
- apps/orchestrator: FastAPI API, application use cases, persistence, provider adapters and workflow planning.
- apps/media-engine: isolated FFprobe/FFmpeg utilities for validating video and extracting boundary frames.
- packages/contracts: shared UI/domain status contracts.
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

## Media engine boundary

muse_media.probe.probe_video invokes FFprobe using a fixed argv list, requires a valid video stream and positive dimensions/duration, and records an exact decoded-frame count. muse_media.boundary_frames.extract_boundary_frames uses FFmpeg to extract frame index zero and the last decoded frame, validates PNG output, and records SHA-256 hashes. These utilities do not yet run as queued jobs or write MediaAsset records; wiring them into the orchestrator is a later milestone.

## Queue and workflow execution status

The M3 workflow API currently validates typed DAGs, builds deterministic execution plans, stores run/node records, and supports manual approval/cache paths. It does not yet execute arbitrary node implementations as production work. Redis and ARQ dependencies exist in the deployment configuration, but a separate production ARQ dispatch/poll worker and its restart-recovery contract are not considered delivered until implemented with tests.

## Continuous integration

.github/workflows/ci.yml runs orchestrator pytest, media-engine unit tests and the frontend TypeScript build on pull requests. Media-engine unit tests mock subprocess calls, so CI does not require a running FFmpeg executable or contact a real provider.

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

muse_media.probe.probe_video invokes FFprobe using a fixed argv list, requires a valid video stream and positive dimensions/duration, and records an exact decoded-frame count. muse_media.boundary_frames.extract_boundary_frames uses FFmpeg to extract frame index zero and the last decoded frame, validates PNG output, and records SHA-256 hashes. The orchestrator uses the media engine to extract the boundary frames of every generated video take (services/boundary_frames.py): the first and last decoded frames become real PNG reference assets that drive frame chaining. muse-media is a separate installable package (apps/media-engine) and FFmpeg/FFprobe must be installed on the server; the orchestrator Docker image and CI install both. The utilities do not yet run as queued jobs of their own; extraction runs inside the generation task step, off the event loop.

If extraction fails (undecodable video, missing FFmpeg or muse-media), the take is still saved, the task records error_category=frame_extraction_failed with the reason, and the take has no boundary frames, so the next shot of a frame chain stays blocked. A real provider never gets placeholder frames. Only the mock provider, whose "videos" are not decodable, gets frames, and those are tiny PNGs with a MOCK_PLACEHOLDER_FRAME payload and a mock_ file name prefix.

A shot in frame_chain mode sends its first frame to the provider as a data: URI of the PNG, because the provider cannot resolve an internal asset id. This relies on muse2api accepting a data URI in first_frame_image, which is unverified (see the capability matrix). A chained shot whose first-frame asset or file is missing fails with a clear reason instead of rendering without its first frame.

## Timeline export

POST /api/v1/projects/{id}/timeline/exports renders the sequence's real video clips with FFmpeg: each clip is trimmed, scaled and padded to the output frame, then joined with a hard cut (concat) or cross dissolve (xfade); timeline gaps are filled with black. The exporter runs off the event loop and then verifies the file with FFprobe (H.264 video, AAC audio, expected size, duration within tolerance). Clips must be video assets that belong to the same project.

An export that cannot produce a verified MP4 is never reported as successful: a missing FFmpeg/FFprobe (503), an FFmpeg failure or a failed verification (422) is stored as a FAILED export job, returned as an error, and creates no asset. The finished file is served by GET /api/v1/assets/{asset_id}/download.

Not rendered yet, and listed as unsupported_features in every response of an export that reaches rendering (success, or a stored FAILED job): clip audio (the output carries a silent AAC track), title card, subtitles and extra audio tracks. Requests rejected before rendering (unknown sequence, clips without a project video) carry no such list.

Timeline placement is rendered faithfully or refused: a gap is filled with black, and a clip that overlaps the previous one is accepted only as a cross dissolve whose length equals the overlap. Any other overlap (for example a hard cut over the previous clip) fails the export with 422 rather than producing a video longer than the timeline.

## Provider media downloads

Muse2ApiAdapter.download_result sends the provider API key only to the provider's own origin. Result URLs on any other host must use https and never receive the key; redirects are followed manually (at most 3) and re-checked on every hop. Non-http(s) schemes, scheme-relative URLs and path-traversal media ids are refused before any request is made.

The body is streamed to a temporary file and never held in memory. A download over the size limit (150 MB, the same as uploads) is rejected, up front from Content-Length or while streaming when no length is declared, and the partial file is removed. The media type is taken from the file signature (PNG, JPEG, WebP, MP4), never from the Content-Type header, so an HTML or JSON error page served with a 200 is rejected as invalid_media, which is not retryable. Connection failures, including a drop in the middle of a download, are retryable network errors and leave no partial file. Error response bodies are read with a 64 KB cap.

When a task finishes, the detected type must match the task: an image task accepts only images and a video task only video. A rejected result fails the task as invalid_media and its file is deleted, unless an existing asset already points at that very file (downloads are stored under their content hash). A retryable download failure keeps the task polling and clears the error once a later poll succeeds.

## Queue and workflow execution status

The M3 workflow API validates typed DAGs, builds deterministic execution plans, stores run/node records, and supports manual approval/cache paths. Until a production executor is registered for a node type, run responses explicitly label its output as simulated/placeholder; legacy cache rows without execution provenance are ignored. It still does not execute arbitrary node implementations as production work. Redis and ARQ dependencies exist in the deployment configuration, but a separate production ARQ dispatch/poll worker and its restart-recovery contract are not considered delivered until implemented with tests.

## Continuous integration

.github/workflows/ci.yml runs orchestrator pytest, media-engine unit tests and the frontend TypeScript build on pull requests. Media-engine unit tests mock subprocess calls, so CI does not require a running FFmpeg executable or contact a real provider.

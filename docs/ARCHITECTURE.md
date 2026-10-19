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

muse_media.probe.probe_video invokes FFprobe using a fixed argv list, requires a valid video stream and positive dimensions/duration, and records an exact decoded-frame count. muse_media.boundary_frames.extract_boundary_frames uses FFmpeg to extract frame index zero and the last decoded frame, validates PNG output, and records SHA-256 hashes. The orchestrator uses extract_boundary_frames for every finished video take (see "Boundary frames and frame chaining"); it is installed from apps/media-engine, and the orchestrator image and CI install it together with FFmpeg. Running these utilities as separate queued media-worker jobs remains a later milestone. A missing FFmpeg/FFprobe raises MediaToolError, which is kept apart from MediaProcessingError so a broken toolchain is never mistaken for a corrupt video.

## Timeline export

POST /api/v1/projects/{id}/timeline/exports renders the sequence's real video clips with FFmpeg: each clip is trimmed, scaled and padded to the output frame, then joined with a hard cut (concat) or cross dissolve (xfade); timeline gaps are filled with black. The exporter runs off the event loop and then verifies the file with FFprobe (H.264 video, AAC audio, expected size, duration within tolerance). Clips must be video assets that belong to the same project.

An export that cannot produce a verified MP4 is never reported as successful: a missing FFmpeg/FFprobe (503), an FFmpeg failure or a failed verification (422) is stored as a FAILED export job, returned as an error, and creates no asset. The finished file is served by GET /api/v1/assets/{asset_id}/download.

Not rendered yet, and listed as unsupported_features in every response of an export that reaches rendering (success, or a stored FAILED job): clip audio (the output carries a silent AAC track), title card, subtitles and extra audio tracks. Requests rejected before rendering (unknown sequence, clips without a project video) carry no such list.

Timeline placement is rendered faithfully or refused: a gap is filled with black, and a clip that overlaps the previous one is accepted only as a cross dissolve whose length equals the overlap. Any other overlap (for example a hard cut over the previous clip) fails the export with 422 rather than producing a video longer than the timeline.

## Provider media downloads

Muse2ApiAdapter.download_result sends the provider API key only to the provider's own origin. Result URLs on any other host must use https and never receive the key; redirects are followed manually (at most 3) and re-checked on every hop. Non-http(s) schemes, scheme-relative URLs and path-traversal media ids are refused before any request is made.

What a download is allowed to be is decided by its bytes, never by the upstream Content-Type. The body is streamed to a temporary file (Accept-Encoding: identity, so a compressed body cannot expand past the cap) and stops at the first bytes if they are not a known PNG, JPEG, WebP or MP4 signature, which avoids pulling an HTML error page in full. It is capped at 150 MB, enforced both from Content-Length and while streaming. The result is renamed into place only after every check passes, so a rejected or interrupted download leaves no file. The returned mime_type is what the bytes are. The queue then requires it to match the task (video task, video file; image task, image file).

A rejected result (invalid_upstream_media) or any non-retryable download failure marks the task and shot FAILED and deletes the file, unless an existing asset already points at the same content-addressed file. A transient failure keeps the task RUNNING and downloads again on the next poll, bounded by max_polls.

## Boundary frames and frame chaining

For a finished video take the first and last decoded frames are extracted with FFmpeg (muse-media) off the event loop, stored as real PNG assets with their own size and SHA-256, and linked to the take. The last frame becomes the shot's last_frame_asset_id and unlocks downstream frame_chain shots. shot.first_frame_asset_id is an input (the previous shot's last frame, or one set by the operator); a finished take never overwrites it.

- A video FFmpeg cannot decode is rejected like any other invalid media: the task is FAILED and nothing is stored.
- If FFmpeg/FFprobe or muse-media is unavailable the video is kept, no frames are invented, task.error_category is frame_extraction_unavailable and the history log says frame chaining from the take is blocked. Downstream frame_chain shots stay BLOCKED_UPSTREAM_FRAME.
- Only the mock provider gets placeholder frames (valid 1x1 PNGs), because its "videos" are not decodable.

The first frame sent to a provider is the image itself as a data: URI (PNG, JPEG or WebP, at most 16 MB), because a remote provider cannot resolve asset ids or reach local files. An asset that is missing, belongs to another project, is too large or is not an image fails the task before the provider is called. Not yet verified against a real muse2api: whether it accepts a data: URI in first_frame_image. If it only accepts URLs, the adapter needs an upload or signed-URL step, and docs/MUSE2API_CAPABILITY_MATRIX.md should record the result.

## Queue and workflow execution status

The M3 workflow API validates typed DAGs, builds deterministic execution plans, stores run/node records, and supports manual approval/cache paths. Until a production executor is registered for a node type, run responses explicitly label its output as simulated/placeholder; legacy cache rows without execution provenance are ignored. It still does not execute arbitrary node implementations as production work. Redis and ARQ dependencies exist in the deployment configuration, but a separate production ARQ dispatch/poll worker and its restart-recovery contract are not considered delivered until implemented with tests.

## Continuous integration

.github/workflows/ci.yml runs orchestrator pytest, media-engine unit tests and the frontend TypeScript build on pull requests. Media-engine unit tests mock subprocess calls, so CI does not require a running FFmpeg executable or contact a real provider.

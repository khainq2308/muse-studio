# MuseStudio Technical Architecture

## Runtime shape

MuseStudio is a monorepo with separate deployment areas:

- apps/studio-web: React + TypeScript + Vite dashboard.
- apps/orchestrator: FastAPI API, application use cases, persistence, provider adapters, workflow planning and the ARQ worker (`muse_orchestrator/workers/queue_worker.py`).
- apps/media-engine: isolated FFprobe/FFmpeg utilities for validating video and extracting boundary frames.
- apps/studio-web/src/lib/api/schema.d.ts: generated frontend contracts from the orchestrator OpenAPI snapshot; no parallel handwritten API contract package.
- ops: operator probes and deployment helpers.

The orchestrator is a modular monolith deployed as two processes from the same image: the API (`uvicorn`) and the ARQ worker (`arq muse_orchestrator.workers.queue_worker.WorkerSettings`, Compose service `orchestrator-worker` in both `docker-compose.yml` and `docker-compose.prod.yml`). Both share PostgreSQL and Redis. Operational procedures for backup/restore and media cleanup are in [OPERATIONS.md](OPERATIONS.md).

## Dependency direction

API routes -> application services -> infrastructure repositories.

The application services coordinate domain models and provider/storage adapters. Repository classes own SQLAlchemy queries and transaction boundaries. Routes should own HTTP schemas, status codes and DTO serialization only. Domain validation should not instantiate HTTP clients or access environment state.

Current layering status (read from code, not inferred from the checker):

- Application services exist for projects/connections/assets (`application/services.py`), the M2 pipeline — script import, Story Bible, shot updates (`application/pipeline_service.py`), workflows (`application/workflow_service.py`), connection accounts (`application/account_service.py`) and system/audit/readiness (`application/system_service.py`).
- Some routes still orchestrate use cases themselves: `api/queue.py` (enqueue, step) and `api/batch.py` pass `repository.session` into `GenerationQueueService` (queue cancel and the operator reply go through `application/queue_service.py`); `api/timeline.py` assembles sequences and export plans from repository reads inside the route; `api/maintenance.py` and `api/metrics.py` inject a DB session and pass it to service functions. Moving that orchestration into application services is outstanding work; do not claim the backend is fully layered.
- `scripts/check_layering.py` ratchets two per-file metrics in `api/*.py` against `scripts/layering_baseline.json`: `direct_db` (DB method calls such as `execute`/`add`/`commit`/`get` on a session, including session aliases and routes that inject `get_db`/`AsyncSession`) and `session_passthrough` (a route passing `repository.session` or an injected session into a service). Both baselines are currently at their recorded debt — `direct_db` 4 (session injection in `api/maintenance.py` and `api/metrics.py`) and `session_passthrough` 13 (`api/batch.py`, `api/queue.py`, `api/maintenance.py`, `api/metrics.py`); `--report` lists every site. The checker cannot see repository calls that encode business rules or transaction orchestration hidden in service helpers, so a passing ratchet is not proof of correct layering.

## Persistence and transactions

- PostgreSQL is the Docker Compose deployment target; SQLite is used for isolated async tests and local development. A `connect` listener in `core/database.py` runs `PRAGMA foreign_keys=ON` for every SQLite connection so foreign keys and `ON DELETE` behavior match PostgreSQL. SQLite still ignores `SELECT ... FOR UPDATE`.
- Schema is managed by Alembic (`0001_bootstrap`, `0002_uniqueness`, `0003_awaiting_input`, `0004_lease_owner_token`). `0002_uniqueness` adds per-parent uniqueness for workflow versions, script revisions, project revisions and Story Bible code names, and the partial unique index `uq_generation_tasks_active_shot_type` (one QUEUED/RUNNING/PAUSED task per shot and task type). It repairs existing duplicates before adding the constraints, without deleting rows. `0003_awaiting_input` adds the queue state `AWAITING_INPUT` (PostgreSQL `ALTER TYPE ... ADD VALUE` in an autocommit block; SQLite widens the enum VARCHAR), includes it in that partial index and adds `generation_tasks.pending_input_json`. Its downgrade turns AWAITING_INPUT rows into RUNNING; PostgreSQL keeps the unused enum value because enum values cannot be dropped. `0004_lease_owner_token` adds the nullable `generation_dispatch_leases.owner_token` (existing leases keep NULL and simply expire); its downgrade drops the column.
- StudioRepository centralizes project, revision, connection, asset, queue-recovery and export persistence used by application services and the worker.
- Project create writes the project, initial revision and audit event in one database transaction.
- Asset upload validates content before recording metadata; when the metadata transaction fails, the newly-written file is removed when safe to do so.
- Asset deletion (`DELETE /api/v1/assets/{asset_id}`) returns 409 when the asset is a locked reference or is still referenced by a take, a shot (selected take output, reference list, first/last frame) or a Story Bible entry; the 409 detail lists the references. `?force=true` detaches every reference, deletes takes whose output is the asset (a `SUCCEEDED` shot whose selected take is deleted returns to `NOT_STARTED`), recompiles lint in the same transaction, commits, and only then removes the file — unless another asset row shares the same storage path.
- Timeline references (clip rows and legacy track `clips_json` of the asset's project, both scoped to that project because assembly validates clip/audio ownership) and export outputs also need `force=true`, which detaches them. An asset whose file is an input of a `QUEUED`/`RUNNING` export — listed in the job's `manifest_snapshot_json.resolved_clips`, the only plan the export worker renders from — is never deleted, not even with `force=true`: a retryable 409 (`category: conflict`) names the active export jobs. Residual race: an export launched between this check and the delete commit fails its render (recorded FAILED), it never succeeds on a missing input.
- Storage paths are canonical: `media_rel_path()` resolves both the file and `MEDIA_ROOT` before computing the POSIX relative path, so a symlinked media root never produces `../..` paths. Legacy non-canonical values are normalised when read by orphan scans and backups.
- A uniqueness violation that reaches the API is returned as 409 `conflict`; other integrity errors are treated as server errors. Database value errors (for example a too-long VARCHAR on PostgreSQL) are returned as 422.

## Script import

The script import endpoint (POST /api/v1/projects/{project_id}/scripts/import) always stores a new script revision with the raw text preserved. Limits: `raw_content` up to 1,000,000 characters; lines longer than 10,000 characters are truncated for analysis; a SCENE number above 10,000 is rejected with 422, and duplicate SCENE numbers are renumbered; shot durations above 600 s are clamped and non-positive durations replaced by the default. Every such adjustment is returned in the response `warnings` list and stored in the revision proposal.

With `auto_apply_proposal=true` the existing Story Bible, scenes and shots are replaced:

- 409 when the project has any QUEUED/RUNNING/PAUSED/AWAITING_INPUT generation task;
- 409 when shots already have generated takes and `replace_existing` is not `true`; with `replace_existing=true`, takes, generation tasks and their dispatch leases are deleted explicitly before the pipeline records are rebuilt;
- 409 (`retryable: true`) when a concurrent import wins the revision number race.

## Prompt compilation

`services/prompt_compiler.py` builds each shot's video prompt from fixed sections (style, subjects, environment, camera/composition, action, continuity, technical output) and lints it; `PipelineService` recompiles every shot of a project after each pipeline mutation and before batch launch, storing `prompt_final`, `negative_prompt`, `prompt_hash` (SHA-256 over the sections and the negative prompt) and the lint report.

- **Action boundaries:** the ACTION section appends the shot's `action_start` / `action_end` only when they are informative, e.g. `Action: Hero watches the street. Starts: Hero crouches at the ledge. Ends: Hero stands and turns away.` A boundary that is empty or generic is left out of both the prompt and the hash; "generic" uses the same normalised placeholder set as the continuity lint (`is_generic_handoff` in `services/prompt_compiler.py`), which covers the script parser defaults `in position` / `completes motion` / `neutral pose`. A shot with no informative boundary therefore produces the same prompt text and hash as before this change; one generic and one real boundary shows only the real one.
- **Dialogue is never sent:** `dialogue` stays on the shot for review but is not part of the prompt or its hash: video models do not speak and tend to render dialogue as on-screen text.
- **Compatibility with older takes:** the compiler also computes the legacy hash (the hash the shot had before boundaries entered the prompt); it is never persisted. Every task enqueued since this change records `"prompt_version": 2` in its request snapshot (`request_snapshot_entry` in `services/generation_guards.py`). Batch launch with `only_changed=true` treats a selected take as current when its `prompt_hash` equals the shot's current hash, or equals the legacy hash **and** the take's task has no `prompt_version` marker (enqueued before the upgrade); the existing model, connection and conditioning-frame checks still apply (`ShotPromptHashes.take_is_current` in `application/pipeline_service.py`). So takes rendered before the upgrade are not all invalidated. Consequence: a pre-upgrade take stays current until an edit changes the legacy hash (action, camera, composition, continuity notes, subjects, location, style, duration…); an edit that changes **only** `action_start`/`action_end` on such a shot is not detected — use `only_changed=false` or regenerate that shot. Takes rendered after the upgrade carry the marker, so they match only the current hash: a later boundary edit is detected even when the take was rendered while the boundaries were empty or generic (its hash then equals the legacy hash). Editing a boundary to or from a generic placeholder changes the hash only when the informative text changes. Formatting: an empty action is omitted (`Action: Starts: X.`), and `.`, `!`, `?`, `…` at the end of a boundary are replaced by a single period.
- **Hash change and the default idempotency key:** the default idempotency key of an enqueue includes the current prompt hash. A shot with informative boundaries therefore has a new key after the upgrade: re-enqueueing it — for example a FAILED shot under batch `only_changed`, or the single-shot enqueue `POST /api/v1/queue/tasks`, which does not apply the legacy rule — creates a new task instead of replaying the old one. This is expected and may use provider quota.

## HTTP access control

All product API routers are mounted with the shared `verify_single_admin` dependency (`X-Studio-Admin-Token`, constant-time comparison). `/healthz` (liveness, no dependency checks) and `/readyz` (readiness: database and Redis reachable, 503 with per-check status otherwise) are intentionally public and return no secrets.

Configuration rules enforced by `core/config.py` outside the development environments (`development`, `dev`, `local`, `test`, `testing`):

- `STUDIO_ADMIN_TOKEN` is required and must be at least 32 characters;
- `SECRET_ENCRYPTION_KEY` must not be the development key and must be at least 32 characters;
- `MUSE2API_VIDEO_PAYLOAD_PROFILE` must not be `compat` (see [FRAME_CHAIN_PROVIDER_CONTRACT.md](FRAME_CHAIN_PROVIDER_CONTRACT.md));

In addition, connection creation (`validate_provider_base_url` in `application/services.py`) refuses `http://` provider base URLs outside development unless `ALLOW_INSECURE_PROVIDER_HTTP=true`.

When a development environment runs without an admin token, the API is unauthenticated and a startup warning is logged. Two guards then apply in `main.py`:

- **Host guard (DNS rebinding):** requests whose `Host` is not in `DEV_ALLOWED_HOSTS` (default `localhost,127.0.0.1,::1,testserver,orchestrator-api`) get 400.
- **Origin/CSRF guard:** state-changing methods (anything except GET/HEAD/OPTIONS) whose `Origin` is not in `DEV_ALLOWED_ORIGINS` (default `http://localhost:5173,http://127.0.0.1:5173`), or that a browser marks `Sec-Fetch-Site: cross-site`, get 403. Clients that send neither header (curl, tests) are unaffected.

CORS is configured in code, not by environment: the only allowed origin is `http://localhost:5173`, with an explicit method and header list (`Accept`, `Authorization`, `Content-Type`, `X-Studio-Admin-Token`, `X-Request-ID`). Production serves the UI and API from the same origin through Nginx, so it does not rely on CORS. An admin token is a single-admin gate, not multi-user authentication; TLS, rate limiting and monitoring remain deployment responsibilities.

## Request correlation, errors and logging

- Every response carries `X-Request-ID`: a safe client-supplied value (`[A-Za-z0-9._:-]{1,128}`) is echoed, otherwise a new id is generated. The request-id middleware is the outermost middleware, so guard rejections carry it too.
- Errors use one envelope `{detail, category, retryable}` (`ErrorEnvelope` in OpenAPI). Validation errors never echo the submitted input; unhandled exceptions return a generic 500 and log a redacted traceback.
- The application logger writes one redacting stdout handler. `LOG_LEVEL` (default `INFO`) and `LOG_FORMAT` (`keyvalue` default, or `json`) configure it; each line includes the request id and the request log records the path only (no query string). Free-text redaction (`SecretRedactor.redact_text`, input bounded to 16 384 characters, linear patterns) hides an unquoted value under a sensitive key up to the next field delimiter (`,` `;` `&` `}` `"` or line break), so `password=my secret value, user=bob` becomes `password=[REDACTED], user=bob`; quoted values are redacted inside their quotes, including escaped quotes of JSON embedded in a JSON string (`{\"api_key\": \"x\"}`). Keys are judged by the same rule as mapping keys (`is_sensitive_key`), and the value of a non-sensitive pair is scanned too, so `Auth failed: passphrase=x` is caught. Deliberate trade-offs: an unquoted value is cut at those delimiters (`password=ab;cd` leaks `cd`), and text after a sensitive key is over-redacted rather than kept for diagnostics. Operator question options use `redact_credentials` instead (value shapes only: known secrets, URL userinfo, Authorization/bearer, credential query parameters, `Cookie: name=value`), because the chosen option is sent back to the provider verbatim.

## Media engine and frame chaining

The reusable `muse_media` package lives in `apps/media-engine` and is installed as an editable local dependency of the orchestrator (uv path source) in local test setup and Docker. Production video-task completion invokes `extract_boundary_frames` through `asyncio.to_thread`: FFprobe validates metadata and exact decoded frame count, FFmpeg extracts frame index zero and the last decoded frame, and the queue verifies nonempty PNG outputs before recording SHA-256-backed Asset rows. Both frames are stored on the take; only the last frame is copied to the shot. Extraction errors fail the media acceptance path rather than persisting fake PNG artifacts.

The conditioning-frame contract, the shared enqueue/dispatch guard, request snapshots and the one-active-task rule are specified in [FRAME_CHAIN_PROVIDER_CONTRACT.md](FRAME_CHAIN_PROVIDER_CONTRACT.md).

The mock provider remains a demo/test seam and writes tiny valid PNG placeholders so queue/continuity tests can run without interpreting synthetic mock MP4 bytes as real video. Those mock frames are not evidence of real visual continuity. Passing an actual extracted frame to a provider still depends on that provider's accepted reference-image contract; live frame conditioning is unverified.

## Generation queue and ARQ worker

Generation tasks are persisted rows; Redis/ARQ only schedules work.

- **Scheduling:** API routes schedule the first step after commit (`api/arq_dispatch.py`); a failure to reach Redis is logged and left to recovery. Each ARQ job id encodes the task id, state, poll count and a **schedule token** derived from the row's `updated_at` (`services/arq_dispatch.py`). The step job carries that token; when the row has changed since scheduling (another job, manual tick/step, resume), the job raises `StaleGenerationStepJob` and exits without stepping or scheduling. This keeps exactly one live step chain per task even when HTTP tick/step and recovery also schedule jobs.
- **Leases:** a dispatch/poll lease (180–330 s, derived from the connection timeout) protects each in-flight provider call; the ARQ step timeout (360 s) is longer than any lease. Every lease carries a random owner token (one per worker step or operator reply); its holder deletes it only while the token still matches (`release_owned_lease` in `services/generation_queue.py`), so a holder that outlived its lease cannot delete the lease of a newer holder. An *expired* lease is still taken over by the next step or reply whoever owned it. The token protects the lease row only: a stale holder's own task-state writes are not fenced by it.
- **Cancellation:** if a step is cancelled after a paid generate request may already have been sent and no external task id was stored, the task becomes FAILED `dispatch_outcome_unknown` (non-retryable) instead of being re-sent. Cancellation in any other phase only releases the lease.
- **Recovery cron** (`recover_pending_generation_tasks_job`, every minute at second 0): reschedules at most 500 QUEUED/RUNNING/AWAITING_INPUT tasks per run, least-recently-updated first, so orphaned tasks are not starved by tasks with a live chain. Stable job ids deduplicate.
- **Frame-chain auto-continue:** when a task succeeds, ready downstream frame-chain shots are enqueued under the same guard as manual enqueue.
- A disabled connection never receives a new dispatch (task FAILED `connection_disabled`); already-dispatched tasks keep polling so a paid result is still collected.
- **Operator questions (needs-input):** when the gateway reports `needs_input`, the task becomes `AWAITING_INPUT` with the question in `pending_input_json` and is polled every 15 s without consuming `max_polls`; it keeps its shot slot and a connection concurrency slot, and the shot stays RUNNING. `POST /api/v1/queue/tasks/{id}/reply` forwards the answer and schedules the next step; the gateway's own 30-minute input timeout fails the task with `error_category = input_timeout`. Details in [FRAME_CHAIN_PROVIDER_CONTRACT.md](FRAME_CHAIN_PROVIDER_CONTRACT.md#operator-questions-needs-input).

These behaviors are covered by SQLite-backed tests with fake Redis pools, and the row-lock, partial-unique-index and lease races are additionally exercised against real PostgreSQL in the `postgres-integration` CI job. The `arq-e2e` CI job runs the API and the ARQ worker as separate processes against a real Redis (`tests/test_arq_e2e.py`: enqueue → dispatch → poll → take, schedule-token dedupe, cancel, needs-input reply, recovery cron for a lost job); a real provider is not exercised in CI (see [TEST_STRATEGY.md](TEST_STRATEGY.md) and docs/evidence/m4/2026-10-23-real-arq-worker.md for a manual run against muse.ai).

## Timeline export

POST /api/v1/projects/{id}/timeline/exports validates the sequence's video clips, persists a QUEUED export job (202 Accepted) and hands the FFmpeg render to the ARQ worker (`process_export_job`). Each clip is trimmed, scaled and padded to the output frame, then joined with a hard cut (concat) or cross dissolve (xfade); timeline gaps are filled with black. The worker runs FFmpeg off the event loop and verifies the file with FFprobe (H.264 video, AAC audio, expected size, container and video stream duration within tolerance). Clips must be video assets that belong to the same project and resolve inside `MEDIA_ROOT`.

Media/export limits are settings in `core/config.py` (documented in `.env.example`) and are validated at startup:

| Variable | Default | Meaning |
|---|---|---|
| `MAX_UPLOAD_SIZE_BYTES` | 157286400 (150 MiB) | Asset upload cap. Raising it also requires raising `client_max_body_size` of the `/api/v1/assets/upload` location in `apps/studio-web/nginx.prod.conf` (currently 160m). |
| `EXPORT_FFMPEG_TIMEOUT_SECONDS` | 1800 | One FFmpeg render subprocess. |
| `EXPORT_FFPROBE_TIMEOUT_SECONDS` | 60 | One FFprobe call (per source clip while planning, twice for output verification). |
| `EXPORT_MAX_DURATION_SECONDS` | 14400 | Maximum timeline duration accepted for export. |
| `EXPORT_MAX_CLIPS` | 500 | Maximum clips in one export. |
| `EXPORT_JOB_TIMEOUT_SECONDS` | 2100 | ARQ timeout for one export job; startup fails unless it exceeds `EXPORT_FFMPEG_TIMEOUT_SECONDS + 2 x EXPORT_FFPROBE_TIMEOUT_SECONDS`. |

Backup/restore size limits are code constants, not settings (see [OPERATIONS.md](OPERATIONS.md)); the Nginx restore location allows 520m for the 500 MiB archive cap.

An export that cannot produce a verified MP4 is never reported as successful: a missing FFmpeg/FFprobe, an FFmpeg failure or a failed verification is recorded by the worker as a FAILED export job (polled via GET /api/v1/projects/{id}/timeline/exports/{export_id}) and creates no asset. If persisting a verified result fails, the rendered file is discarded and the job is marked FAILED. The finished file is served by GET /api/v1/assets/{asset_id}/download.

Worker lifecycle for exports:

- **Cancellation:** when ARQ cancels the export job (`EXPORT_JOB_TIMEOUT_SECONDS`, default 2100 s, or worker shutdown), a cancel event makes the render thread kill its FFmpeg child and remove partial output; the worker waits up to 30 s for that, then marks the job FAILED in a fresh session. There is no HTTP endpoint to cancel an export.
- **Recovery cron** (`recover_pending_export_jobs_job`, every minute at second 20): re-enqueues QUEUED exports and runs the stale sweep, which marks RUNNING exports FAILED once they are older than the job timeout plus 300 s.

Not rendered yet, and listed as unsupported_features in every response of an export that reaches rendering (success, or a stored FAILED job): clip audio (the output carries a silent AAC track), title card, subtitles and extra audio tracks. Requests rejected before rendering (unknown sequence, clips without a project video) carry no such list.

Timeline placement is rendered faithfully or refused: a gap is filled with black, and a clip that overlaps the previous one is accepted only as a cross dissolve whose length equals the overlap. Any other overlap (for example a hard cut over the previous clip) fails the export with 422 rather than producing a video longer than the timeline.

## Provider media downloads

Muse2ApiAdapter.download_result sends the provider API key only to the provider's own origin. Result URLs on any other host must use https and never receive the key; redirects are followed manually (at most 3) and re-checked on every hop. Non-http(s) schemes, scheme-relative URLs and path-traversal media ids are refused before any request is made.

## Workflow execution status

The M3 workflow API validates typed DAGs, builds deterministic execution plans, stores run/node records, and supports manual approval/cache paths. Until a production executor is registered for a node type, run responses explicitly label its output as simulated/placeholder; legacy cache rows without execution provenance are ignored. It does not execute arbitrary node implementations as production work.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main`, `feat/**`, `fix/**`, `refactor/**`, `chore/**`, `docs/**`, `test/**` and on pull requests to `main`:

- **agent-kit-check:** `check_agent_assets.py`, `check_docs_sync.py`, Ruff (pinned to the version in `uv.lock`) over the whole `apps/orchestrator` + `apps/media-engine` tree plus changed Python files elsewhere via `scripts/lint.sh --python-only`, the layering ratchet, agent-kit guard unit tests, production Compose `config` validation, production image build, and `scripts/test_nginx_prod.sh` (production Nginx image against a mock API container).
- **python-tests:** installs FFmpeg/FFprobe, `uv sync --locked`, runs the orchestrator suite with `REQUIRE_FFMPEG=1`, then `pip-audit` over the locked production dependencies (blocking).
- **postgres-integration:** a health-checked `postgres:16` service container, `uv sync --locked`, then `tests/test_postgres_integration.py` with `TEST_POSTGRES_URL` and `REQUIRE_POSTGRES=1` (a missing URL fails collection, and the step fails if JUnit reports any skip). Each test builds a fresh database with the real Alembic `upgrade head` and covers schema drift, migration `0002` duplicate repair, concurrent enqueue/step/`max_concurrency` races on separate connections (row locks, partial unique index, dispatch lease), concurrent workflow-graph and Story Bible writes, timeline assemble and backup→restore through the API, and asyncpg SQLSTATE → HTTP mapping.
- **openapi-contract:** OpenAPI snapshot check, `audit_openapi_responses.py --strict`, `audit_api_response_models.py` requiring all 13 routers to be typed, generated TypeScript schema check and a clean-tree check.
- **media-engine-tests:** media-engine unit tests (subprocess calls are mocked).
- **frontend-build:** `npm ci`, `npm audit --omit=dev --audit-level=high`, lint, format check, Vitest, production build, Playwright against the production preview with a mocked API.

CI runs PostgreSQL only in the `postgres-integration` job and Redis/ARQ only in the `arq-e2e` job (elsewhere the queue is driven by direct `step_task` calls and a fake pool); it does not contact a real muse2api provider; passing mocks do not verify external provider capabilities.

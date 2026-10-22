# Frame-chain provider handoff contract

## Purpose

Frame chaining has two different contracts to satisfy:

1. MuseStudio must turn its local Asset ID into the actual image bytes. An Asset ID is a database identifier and is not a provider-fetchable URL.
2. The configured muse2api implementation must recognize the input field and the image reference format it receives.

This change addresses the first contract and provides source-backed compatibility aliases for two known request-schema variants. It does not claim live-provider verification.

## Conditioning-frame contract (C1)

Source: `apps/orchestrator/muse_orchestrator/services/generation_guards.py`, `services/generation_queue.py`, `application/pipeline_service.py`.

- `shot.first_frame_asset_id` is **only the conditioning input** of the next video generation of that shot. It is either set by the operator (`PATCH` shot, validated to belong to the same project) or written by the queue at enqueue time with the frame resolved from the upstream shot.
- Shots no longer store their own extracted opening frame. A completed take keeps its own boundary frames on `take.first_frame_asset_id` / `take.last_frame_asset_id`; the queue copies only the last frame to `shot.last_frame_asset_id`. A legacy shot whose `first_frame_asset_id` points at a frame extracted from one of its own takes is treated as having no conditioning input.
- Only shots with `continuity_mode = frame_chain` are conditioned. With `previous_shot_id`, the frame is the last frame of the upstream shot's **selected take** (legacy rows without a selected take fall back to `upstream.last_frame_asset_id`); it must exist and belong to the same project, otherwise the shot is blocked (`blocked_upstream_frame`). Without `previous_shot_id`, an operator-set frame that no longer exists or belongs to another project fails closed (`conditioning_frame_invalid`) instead of silently becoming text-to-video.
- Changing `continuity_mode` or `previous_shot_id` clears `first_frame_asset_id` unless the same request sets a new one.
- Deleting an asset with `force=true` detaches it from `shot.first_frame_asset_id` / `last_frame_asset_id` and take frame columns, then recompiles lint in the same transaction.

### Enqueue and dispatch guard

Every enqueue path (`POST /queue/tasks`, batch launch, retry-failed, flag-continuity, frame-chain auto-enqueue) and the dispatch step call the same guard (`evaluate_generation_guard`). It refuses work, in this order, for: an explicit `BLOCKED_REFERENCE` shot state (enqueue only), any Prompt Linter blocker (`lint_blocker`), a disabled connection (`connection_disabled`), a missing upstream frame (`blocked_upstream_frame`) or an invalid operator frame (`conditioning_frame_invalid`).

At enqueue the queue stores a **request snapshot** in the task history: `duration_seconds`, `aspect_ratio`, the resolved conditioning frame and its source (`upstream` / `operator` / `none`). Immediately before every paid dispatch (including resume and recovery) the guard runs again — lint blockers are therefore re-checked at dispatch — and the freshly resolved conditioning frame is compared with the snapshot. A mismatch (upstream take re-selected, continuity edited) fails the task with `stale_conditioning_frame` and returns the shot to `NOT_STARTED`; the operator re-enqueues to render on the current frame. Dispatch uses the snapshot's duration and aspect ratio, not the live (possibly edited) shot. Tasks enqueued before snapshots existed fall back to the live resolution.

### Task failure categories introduced by this contract

| `error_category` | Meaning | Retry |
|---|---|---|
| `lint_blocker` | Prompt Linter still reports a blocker at enqueue or dispatch. | Fix the shot, then enqueue again. |
| `blocked_upstream_frame` | No validated last frame on the upstream shot's selected take. | Generate/select the upstream take first. |
| `conditioning_frame_invalid` | Operator-set first frame is missing or belongs to another project. | Set a valid frame or clear it. |
| `stale_conditioning_frame` | Conditioning frame changed between enqueue and dispatch. | Re-enqueue explicitly. |
| `dispatch_outcome_unknown` | The worker step was cancelled (job timeout/shutdown) after the provider request may have been sent and before an external task id was stored. The task is FAILED and non-retryable; it is not re-sent automatically to avoid a duplicate paid generation. | Check the provider dashboard, then retry explicitly. |

### One active task per shot and task type

At most one `QUEUED`/`RUNNING`/`PAUSED` task may exist per `(shot_id, task_type)`. The queue returns the existing active task instead of creating a second one, and Alembic revision `0002_uniqueness` enforces it with the partial unique index `uq_generation_tasks_active_shot_type` (predicate on the stored enum names `QUEUED`, `RUNNING`, `PAUSED`). A lost concurrent insert returns the winning task rather than a 500. On upgrade, pre-existing duplicates are repaired by keeping the newest active task and cancelling the others with `error_category = duplicate_active_task`.

## Local frame handoff

Before dispatching a frame-chained video task, the queue takes the conditioning frame validated against the request snapshot (see above) and:

- verifies the Asset belongs to the same project as the shot;
- only permits PNG, JPEG, or WebP and rejects empty or greater-than-20-MiB files;
- resolves the storage path inside `MEDIA_ROOT` using `LocalAssetStorage.resolve_safe_path`;
- reads the bounded file, checks its byte count, MIME signature, and SHA-256 against stored metadata;
- encodes the image as `data:<mime>;base64,...`.

A missing/corrupt frame on a shot that has an upstream `frame_chain` dependency fails closed. It must not silently turn into text-to-video.

## Known request-schema variants

These are source-level observations, not a live probe of the user's deployment:

- [www222fff/muse2api video route](https://github.com/www222fff/muse2api/blob/main/src/muse2api/api/routes/videos.py) reads `body.image` or `body.input_reference`. Its [request schema](https://github.com/www222fff/muse2api/blob/main/src/muse2api/api/schemas.py) defines `image`, `input_reference`, and `size`; the shared image loader accepts data URLs or HTTP(S) URLs.
- [czg86389-hub/muse2api README](https://github.com/czg86389-hub/muse2api) documents `first_frame_image` as a first-frame input and says it accepts data URLs or HTTP(S) URLs.

The adapter supports an explicit `MUSE2API_VIDEO_PAYLOAD_PROFILE` setting:

- `compat` (code default, **development/test only**) preserves the historical aliases: both `image` and `first_frame_image`, plus both `size` and `aspect_ratio`. It duplicates a base64 data URI and may enlarge a request substantially. `core/config.py` refuses to start with `compat` whenever `ENVIRONMENT` is not one of `development`, `dev`, `local`, `test`, `testing`; the development Compose file defaults to `compat`, the production Compose/CI configuration must set a provider-specific profile.
- `czg86389-hub` sends `first_frame_image` and `aspect_ratio` only.
- `www222fff` sends `image` and `size` only. The accepted representation of `size` is deliberately not inferred from the name or set to an aspect ratio. Set `MUSE2API_VIDEO_SIZE` to a value confirmed for the actual deployed gateway; the application refuses this profile when that value is missing.

The profile removes the duplicate frame field for the two provider-specific configurations. It does not itself prove that the deployment accepts the chosen field or that generated output is visually conditioned on the frame. `compat` may be used only on a local development instance while the deployed fork is unknown; any non-development deployment must choose `czg86389-hub` or `www222fff` (with `MUSE2API_VIDEO_SIZE`) based on the identified gateway, and that profile must be tested against the exact deployed version before frame chaining is treated as verified. The queue continues to enforce the 20-MiB raw-frame limit.

## Verification status and operational test

Status remains **unverified** until a dated evidence artifact is committed under `docs/evidence/` for the actual deployed instance/version. A mock provider or request-body unit test is not enough.

A real-instance acceptance check should use a test connection and a small, non-sensitive image:
1. Generate one upstream clip.
2. Confirm the resulting first/last frame Assets are actual PNGs with matching stored SHA-256 values.
3. Enable `frame_chain` on the next shot and confirm its dispatched JSON contains a data URI under the field understood by the deployed gateway, not an internal Asset ID.
4. Confirm the output video was conditioned on that first frame (for example by checking the first output frame visually against the input). A successful HTTP 2xx alone is not evidence of visual conditioning.
5. Record the UTC date, deployment repository/version, tested fields, outcome, and limitations without including API keys, cookies, proxy credentials, or private media.

This test may consume generation quota. Do not mark the capability verified before the evidence file and dated result exist.

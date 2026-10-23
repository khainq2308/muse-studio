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
| `input_timeout` | The provider asked the operator a question (task `AWAITING_INPUT`) and the gateway gave up after its 30-minute input timeout. Non-retryable; the shot is settled like any other failure. | Enqueue again (consider answering faster). |
| `dispatch_outcome_unknown` | The worker step was cancelled (job timeout/shutdown) after the provider request may have been sent and before an external task id was stored. The task is FAILED and non-retryable; it is not re-sent automatically to avoid a duplicate paid generation. | Check the provider dashboard, then retry explicitly. |

### One active task per shot and task type

At most one `QUEUED`/`RUNNING`/`PAUSED`/`AWAITING_INPUT` task may exist per `(shot_id, task_type)`. The queue returns the existing active task instead of creating a second one, and the partial unique index `uq_generation_tasks_active_shot_type` enforces it (created by Alembic revision `0002_uniqueness` with the stored enum names `QUEUED`, `RUNNING`, `PAUSED`; `0003_awaiting_input` adds `AWAITING_INPUT`). A lost concurrent insert returns the winning task rather than a 500. On upgrade, pre-existing duplicates are repaired by keeping the newest active task and cancelling the others with `error_category = duplicate_active_task`.

## Operator questions (needs-input)

Source: `providers/muse2api_adapter.py`, `services/generation_queue.py`, `application/queue_service.py`, `api/queue.py`. Status: **unverified** against a real muse.ai account (see the capability matrix); the gateway contract below is exercised only with offline `httpx.MockTransport` tests and a fake provider.

Gateway contract (muse2api fork with the needs-input driver):

- `GET /v1/videos/{id}` and `GET /v1/images/generations/{id}` may return `"status": "needs_input"` with `"question": {"kind": "question" | "approval" | "stalled", "text": str (<= 4000), "options": [str] (<= 10, each <= 200), "asked_at": float}`; `question` is `null` otherwise. `interactions` lists earlier questions and answers (not stored by MuseStudio). `stalled` questions offer `["Keep waiting", "Give up"]`; payment approvals never reach the operator.
- `POST /v1/videos/{id}/reply` or `POST /v1/images/generations/{id}/reply` with exactly one of `{"text": str <= 4000}` / `{"choice": str <= 200}` (one of the options) returns the task view; `409 {"error": {"code": "conflict"}}` when the task is not waiting, `400 invalid_request` for a bad payload or choice, `404` for an unknown task. After an answer the gateway task is `running` again; the personal gateway (khainq2308/muse2api `1bd4451`+) already returns `status: running` with `question: null` in the reply response, older builds returned the pre-answer `needs_input` view. Both are accepted.
- An unanswered question fails the gateway task after 30 minutes with `error.code = "input_timeout"`.

Adapter: `needs_input` maps to provider status `needs_input` with a validated question; a present but out-of-contract question (unknown kind, oversize text/options, wrong types) is a non-retryable `invalid_request`, a `null` question keeps the status with no question. `reply()` routes `image:` external ids to the image route; HTTP 409 becomes the non-retryable category `input_not_pending`, 400/404 stay `invalid_request`. The mock provider never asks questions and answers `reply()` with `input_not_pending`.

Queue:

- Poll result `needs_input` → task `AWAITING_INPUT`, question stored (text secret-redacted) in `generation_tasks.pending_input_json`, one history entry per new question. The discovering poll and all polls while waiting do not count against `max_polls`; the task is re-polled every 15 s (the gateway enforces its own input timeout). The task keeps its dispatch account, its `(shot, task_type)` slot and a connection concurrency slot (it still holds a muse.ai tab); the shot stays `RUNNING`.
- The gateway reports `running` again (answered elsewhere) → task `RUNNING`, question cleared. `failed` with `input_timeout` → `FAILED`, `error_category = input_timeout`, non-retryable.
- Cancel is allowed while awaiting input; pause is refused with 409. See "Provider cancel" below. Recovery re-schedules awaiting tasks; batch tick polls them with the running tasks.

### Provider cancel

The personal gateway (khainq2308/muse2api `1bd4451`+) exposes `POST /v1/videos/{id}/cancel` and `POST /v1/images/generations/{id}/cancel` (no body): 200 with the task view in `status: cancelled` (`error.code = "cancelled"`), 404 with the gateway error envelope (`error.code = "not_found"`) for an unknown task, 409 `conflict` when the task already finished. It stops the browser wait and frees the muse.ai tab and account. Public upstream `949f326` has no such route and answers FastAPI's default `404 {"detail": "Not Found"}` (observed 2026-10-23 on an older local build).

- Adapter: `cancel()` (optional `CancellableProvider` protocol, not part of `VideoProvider`) returns `cancelled`, `already_finished` (409 or a terminal view), `not_found` (404 with the envelope) or `unsupported` (404 without the envelope, 405). Other HTTP/network errors raise `ProviderFailure`; a 10 s timeout applies. The mock provider returns `unsupported`.
- Queue: an operator cancel is committed locally first. Only for a task that was `RUNNING` or `AWAITING_INPUT` and has an external task id, the provider is then asked to cancel, best effort (12 s cap). The outcome is appended to the history as `Provider cancel: cancelled | unsupported | already finished | not found | failed (<category>)`; failures are logged redacted and never undo or fail the local cancel. Queued and paused tasks stay local.
- A polled provider status `cancelled`/`canceled` (gateway restart, another client) fails the task with the non-retryable category `upstream_cancelled` instead of `upstream_generation_failed`.

API: `POST /api/v1/queue/tasks/{task_id}/reply` with exactly one of `text` (1–4000 chars) or `choice` (1–200 chars, must be one of the pending options) — 422 otherwise, 404 for an unknown task, 409 (`category: input_not_pending`) when the task is not `AWAITING_INPUT`. The answer is sent with the task's pinned account credentials; on success the task is `RUNNING` (or still `AWAITING_INPUT` when the provider immediately asks a follow-up), a redacted history entry records the answer and the next step is scheduled. A provider 409 also returns 409 after the task is stepped once to refresh its state; other provider failures use the shared error envelope (e.g. 502 `upstream_5xx`). `QueueTaskOut.pending_input` exposes the question (`kind`, `text`, `options`, `asked_at`) while the task is `AWAITING_INPUT`, otherwise `null`. The page summary includes `awaiting_input_tasks`, counted over the whole project independent of filter and page; the Queue Monitor uses it for its banner and tab title, and can show a browser notification when the count grows (permission requested only on click).

Known limitation: a poll whose provider response was read just before an answer was accepted can briefly put the task back into `AWAITING_INPUT`; the next poll (15 s) corrects it.

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
- `www222fff` sends `image` and `size` only. The accepted representation of `size` is deliberately not inferred from the name or set to an aspect ratio. Set `MUSE2API_VIDEO_SIZE` to a value confirmed for the actual deployed gateway; the application refuses this profile when that value is missing. At muse2api `949f326`, `size` is documented as an aspect ratio such as `16:9` or an OpenAI size such as `1024x1024` (`drivers/base.py`) and `duration` is an integer in [1, 60]; the adapter rounds and clamps fractional shot durations. On 2026-10-23 a real muse.ai account run through the adapter showed that the unpatched gateway ignores the frame: its browser driver only attaches the image to a chat prompt (docs/evidence/muse2api/2026-10-23-live-contract.md). With a driver patch that prefixes the prompt with an instruction to animate the attached image and use it as the first frame, a second run produced a video whose first frame matched the submitted image (SSIM 0.782; docs/evidence/muse2api/2026-10-23-image-to-video.md). **Frame chaining through www222fff/muse2api gives visual continuity only when the deployed gateway carries that patch**; an unpatched `949f326` gateway produces plain text-to-video. A full two-shot MuseStudio chain on the patched gateway matched shot 1's last frame to shot 2's first frame (SSIM 0.998; docs/evidence/muse2api/2026-10-23-frame-chain.md), although one attempt failed because muse.ai answered in text and delegated the render, so retries are expected.

### Detecting continuity support

The patched gateway (the private personal repository khainq2308/muse2api, from `0f76005`; CI cannot check it out and pins public upstream `949f326`) reports `driver.capabilities.video_first_frame` in `GET /readyz`. `POST /api/v1/connections/{id}/test` stores it as `capabilities_snapshot.video_first_frame`:

- `supported`: the gateway advertises `true`.
- `unsupported`: the gateway advertises `false`, or the connection uses the mock provider (placeholder frames).
- `unknown`: the gateway does not advertise it (for example upstream `949f326`) or the probe failed.

Frame chaining is never blocked by this value. `POST /api/v1/projects/{id}/batch/launch` returns a `frame_chain_continuity_not_guaranteed` entry in `warnings`, listing the frame-chained shots, whenever the connection is not `supported`; re-test the connection after upgrading the gateway.

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

# Frame-chain provider handoff contract

## Purpose

Frame chaining has two different contracts to satisfy:

1. MuseStudio must turn its local Asset ID into the actual image bytes. An Asset ID is a database identifier and is not a provider-fetchable URL.
2. The configured muse2api implementation must recognize the input field and the image reference format it receives.

This change addresses the first contract and provides source-backed compatibility aliases for two known request-schema variants. It does not claim live-provider verification.

## Local frame handoff

Before dispatching a frame-chained video task with a first-frame asset, the queue:

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

- `compat` (default) preserves the historical aliases: both `image` and `first_frame_image`, plus both `size` and `aspect_ratio`. This avoids a behavior change before the configured fork is identified, but duplicates a base64 data URI and may enlarge a request substantially.
- `czg86389-hub` sends `first_frame_image` and `aspect_ratio` only.
- `www222fff` sends `image` and `size` only. The accepted representation of `size` is deliberately not inferred from the name or set to an aspect ratio. Set `MUSE2API_VIDEO_SIZE` to a value confirmed for the actual deployed gateway; the application refuses this profile when that value is missing.

The profile removes the duplicate frame field for the two provider-specific configurations. It does not itself prove that the deployment accepts the chosen field or that generated output is visually conditioned on the frame. Keep `compat` until the deployment is identified, and test the selected profile against that exact version before treating frame chaining as verified. The queue continues to enforce the 20-MiB raw-frame limit.

## Verification status and operational test

Status remains **unverified** until a dated evidence artifact is committed under `docs/evidence/` for the actual deployed instance/version. A mock provider or request-body unit test is not enough.

A real-instance acceptance check should use a test connection and a small, non-sensitive image:
1. Generate one upstream clip.
2. Confirm the resulting first/last frame Assets are actual PNGs with matching stored SHA-256 values.
3. Enable `frame_chain` on the next shot and confirm its dispatched JSON contains a data URI under the field understood by the deployed gateway, not an internal Asset ID.
4. Confirm the output video was conditioned on that first frame (for example by checking the first output frame visually against the input). A successful HTTP 2xx alone is not evidence of visual conditioning.
5. Record the UTC date, deployment repository/version, tested fields, outcome, and limitations without including API keys, cookies, proxy credentials, or private media.

This test may consume generation quota. Do not mark the capability verified before the evidence file and dated result exist.

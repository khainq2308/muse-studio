# MuseStudio two-shot frame chain on a live muse2api — 2026-10-23

- **Probe date:** 2026-10-23 04:19–04:45 (local time UTC+7).
- **Gateway:** khainq2308/muse2api branch `feat/image-to-video-first-frame` (upstream `949f326`
  plus the first-frame driver patch `bf26146`), browser driver, one real muse.ai account,
  `MUSE2API_VIDEO_TIMEOUT=1200`. Keys and cookies are not recorded.
- **Client:** the full MuseStudio pipeline (branch `feat/frame-chain-hardening`) running
  in-process against a throwaway SQLite database and media root: script import →
  shot 2 set to `frame_chain` → `POST /batch/launch` (`model_id: muse-video`) →
  `POST /batch/tick` loop, profile `www222fff`, `MUSE2API_VIDEO_SIZE=16:9`.

## Script

Two 5 s shots in one scene: a red apple on a white table by a sunny window with a dolly-in,
then "the camera keeps drifting closer as morning light brightens". Shot 2 used
`continuity_mode: frame_chain` with `previous_shot_id` = shot 1.

## Results

| Step | Result |
| --- | --- |
| Batch launch | Shot 1 enqueued; shot 2 blocked as `blocked_upstream_frame` (no upstream frame yet). |
| Shot 1 | `succeeded` after ~4 min; MuseStudio extracted and stored the last frame as an asset. |
| Shot 2 auto-enqueue | Enqueued with `first_frame_asset_id` = shot 1's last-frame asset. |
| Shot 2, attempt 1 | **failed** `upstream_generation_failed`: the muse.ai agent replied with text only (it said it handed the render to Kling, then PixVerse) and no video arrived within the gateway's media grace period. |
| Shot 2, attempt 2 (`POST /batch/retry-failed`) | `succeeded` after 154 s; still conditioned on shot 1's last frame. |
| Continuity | SSIM between shot 1's last frame and shot 2's first decoded frame (grayscale, 640x360): **0.998**. Side-by-side: docs/evidence/muse2api/2026-10-23-frame-chain.png |

After the run, the gateway (with commit `0f76005`) reports
`/readyz` → `driver.capabilities.video_first_frame: true`, and
`Muse2ApiAdapter.first_frame_capability()` returned `True` against it.

## Limitations

- One chain of two shots, one account. One of two shot-2 attempts failed because muse.ai's
  agent answered in text and delegated the render to another service; retries are needed
  in practice and each attempt costs quota.
- Requires the patched gateway; an unpatched gateway ignores the first frame
  (docs/evidence/muse2api/2026-10-23-live-contract.md).

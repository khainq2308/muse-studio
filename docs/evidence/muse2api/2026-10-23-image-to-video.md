# muse2api image-to-video (first-frame conditioning) - 2026-10-23

- **Probe date:** 2026-10-23 03:55-03:58 (local time UTC+7).
- **Provider instance:** www222fff/muse2api 949f326 **plus a local, uncommitted driver patch**
  (see below), version 0.2.0, local instance at 127.0.0.1:18610, browser driver, one real
  muse.ai account, MUSE2API_VIDEO_TIMEOUT=1200. Keys and cookies are not recorded.
- **Client:** MuseStudio Muse2ApiAdapter from branch feat/muse2api-contract
  (profile www222fff, MUSE2API_VIDEO_SIZE=16:9).

## Gateway patch under test

At 949f326 the browser driver only attaches the first frame to a chat message
("Generate a video (...): PROMPT") and muse.ai ignored it (see
docs/evidence/muse2api/2026-10-23-live-contract.md). The tested patch in
src/muse2api/drivers/browser/driver.py (generate_video) prefixes the prompt when a first
frame is present:

> Animate the attached image into a video. Use the attached image exactly as the first
> frame: keep its composition, subjects, colours and style, and continue the motion from it.

## Request

- POST /v1/videos: model muse-video, duration 5, size "16:9", image = PNG data URI of a
  photo of a red apple on a white table (an image muse.ai generated earlier that day).
- Text prompt: "The camera slowly pushes in while soft daylight shifts across the scene."
  It deliberately does **not** mention the apple, so an apple in the output can only come
  from the attached frame.

## Result

- Task succeeded after 155 s; downloaded through the adapter: H.264, 1920x1080, 5.000 s,
  1,217,610 bytes.
- The first decoded frame shows the same apple, table and cloth with the same composition
  and lighting (slight zoom). SSIM between the input and the first frame (grayscale,
  640x360): **0.782**.
- Side-by-side (input left, generated first frame right):
  docs/evidence/muse2api/2026-10-23-image-to-video-first-frame.png

## Limitations

- One run, one input image, one account. Repeat with different subjects before relying on
  it at scale.
- Requires the gateway patch above; an unpatched 949f326 gateway still ignores the frame.
- Visual continuity across a full MuseStudio frame chain (last frame of shot N used as the
  first frame of shot N+1) was not exercised end to end.

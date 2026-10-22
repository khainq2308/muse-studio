# muse2api live contract run — 2026-10-23

- **Probe date:** 2026-10-23 (local time UTC+7).
- **Provider instance:** www222fff/muse2api `949f326443bcc687dbf25118391cf32b4712d734`
  (version `0.2.0`), local instance at `127.0.0.1:18610`, **browser driver** (Google Chrome,
  headless), **one real muse.ai account** imported by the operator. API key, admin key,
  cookies and account identifiers are not recorded here.
- **Client:** MuseStudio `Muse2ApiAdapter` (branch `feat/muse2api-contract`), driven by
  `apps/orchestrator/tests/test_muse2api_contract.py` with
  `MUSE2API_VIDEO_PAYLOAD_PROFILE=www222fff`, `MUSE2API_VIDEO_SIZE=16:9`,
  `MUSE2API_CONTRACT_REQUIRE=1`, `MUSE2API_CONTRACT_REQUIRE_MP4=1`.

## Readiness during the run

```json
{"ready":true,"driver":{"driver":"browser","ok":true},"accounts":{"total":1,"available":1,"invalid":0}}
```

## Results

| Test | Result | Observation |
| --- | --- | --- |
| `test_live_health_and_models` | passed (twice) | `/healthz` + `/readyz` healthy and ready; `/v1/models` lists `muse-chat`, `muse-image`, `muse-video` and aliases. |
| `test_live_image_round_trip` | passed | `POST /v1/images/generations` with `"async": true`, `size: "16:9"` returned a task; the adapter tagged it `image:<id>`, polled `GET /v1/images/generations/{id}`, read `result.data[0].url` and downloaded it through the SSRF-guarded downloader: WebP image, 479,698 bytes, signature check passed. |
| `test_live_video_round_trip` | **failed** | Gateway accepted the request (`duration: 4`, `size: "16:9"`, `image` data URI) and reported progress up to 95%, then ended the task `failed` with `{"code": "upstream_timeout", "message": "video generation timed out"}` after 608,571 ms (gateway default `MUSE2API_VIDEO_TIMEOUT=600`). |

## Gateway request log excerpt (adapter requests only)

Columns: local time, request, HTTP status, task status, task duration (ms), task error.

| Time | Request | HTTP | Task | ms | Error |
| --- | --- | --- | --- | --- | --- |
| 03:25:53 | GET /v1/models | 200 | - | - | - |
| 03:25:53 | POST /v1/videos | 200 | cancelled | 60411 | - (gateway process restarted at 03:26:59) |
| 03:32:48 | GET /v1/models | 200 | - | - | - |
| 03:32:48 | POST /v1/videos | 200 | failed | 608571 | video generation timed out |
| 03:42:57 | POST /v1/images/generations | 200 | - (async; the log does not record image task status, the test observed `succeeded`) | - | - |

## Known limitations

- The failed video runs used an undecodable 72-byte placeholder PNG as the first frame and,
  in the second run, a concurrent text-to-video request on the same account. The test now
  sends a real 1280x720 PNG; video generation, video download and first-frame conditioning
  remain **unverified** until a run passes.
- A separate operator text-to-video request sent with curl (not through the adapter)
  completed in ~175 s and produced a decodable H.264 1920x1080 5.0 s MP4. It shows the
  gateway and account can render video, not that the adapter's image-to-video path works.
- Single account, single local instance, single run per capability; not a load or
  reliability measurement.

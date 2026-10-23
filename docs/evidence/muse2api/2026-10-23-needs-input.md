# muse2api operator question (needs_input) - 2026-10-23

- **Probe date:** 2026-10-23 13:10-13:11 (local time UTC+7).
- **Provider instance:** khainq2308/muse2api f68ac93 (upstream 949f326 plus the first-frame
  patch, the readyz capability and the needs-input feature), version 0.2.0, local instance
  at 127.0.0.1:18610, browser driver, one real muse.ai account. Keys and cookies are not
  recorded.
- **Client:** MuseStudio Muse2ApiAdapter from branch feat/needs-input (profile www222fff,
  MUSE2API_VIDEO_SIZE=16:9), called from a script; the queue, API and Queue Monitor were not
  part of this run.

## Request

POST /v1/images/generations (`"async": true`, model muse-image, size 16:9) with the prompt:

> A poster of a lighthouse at dusk. Before you generate anything, ask me exactly one
> clarifying question: should the art style be watercolor or neon cyberpunk? End your
> message with the question and wait for my answer.

The prompt asks muse.ai to ask, because a real question cannot be triggered otherwise on
demand. Detection is the gateway's: a text-only reply whose tail contains `?`.

## Result

| Time | Observation |
| --- | --- |
| 13:10:28 | Task created (`image:task_ad460b637a934aee`). |
| 13:10:58 | `GET /v1/images/generations/{id}` returned `status: needs_input` with `question = {kind: "question", text: "Should the art style be watercolor or neon cyberpunk?", options: [], asked_at: 1792735857.55}`; the adapter mapped it to `needs_input` with that question. |
| 13:10:58 | `Muse2ApiAdapter.reply()` sent `{text: "Watercolor, please. Go ahead and generate the image now."}`: gateway `200`. A second reply got `409`, mapped to `input_not_pending`. |
| 13:11:32 | Task `succeeded`; the adapter downloaded a 540 KB WebP image. |

The image is a watercolor lighthouse poster (`2026-10-23-needs-input.png`, downscaled
preview), so the answer reached muse.ai in the same chat and was applied.

`asked_at` is Unix seconds, as the Queue Monitor assumes.

## Limitations

- One run, kind `question` only. `approval` and `stalled` were exercised only with the
  gateway's mock driver (`MOCK_APPROVAL`) and unit tests.
- The question was solicited by the prompt; unprompted questions use the same detection.
- The reply response still reported `needs_input` (snapshot taken before the runner
  resumed); the MuseStudio queue sets the task back to RUNNING itself and the next poll
  confirms it.
- The MuseStudio queue/API/UI path was not driven against the real gateway in this run.

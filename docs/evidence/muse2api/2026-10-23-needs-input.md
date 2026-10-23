# muse2api operator question (needs_input) - 2026-10-23

- **Probe date:** 2026-10-23 13:10-13:11 (local time UTC+7).
- **Provider instance:** khainq2308/muse2api f68ac93 (upstream 949f326 plus the first-frame
  patch, the readyz capability and the needs-input feature), version 0.2.0, local instance
  at 127.0.0.1:18610, browser driver, one real muse.ai account. Keys and cookies are not
  recorded.
- **Client:** MuseStudio branch feat/needs-input (profile www222fff, MUSE2API_VIDEO_SIZE=16:9):
  run 1 calls Muse2ApiAdapter from a script; run 2 drives the full stack (see below).

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

## Run 2: full MuseStudio stack and Queue Monitor (13:15-13:21)

Stack: orchestrator API (uvicorn, throwaway SQLite and media root), a real ARQ worker on a
throwaway `redis:7-alpine` container, studio-web on the Vite dev server, all on 127.0.0.1,
plus the same gateway. A muse2api connection was created through the API (connection test
`healthy`), and an image task was enqueued with `POST /api/v1/queue/tasks` for a shot whose
action asked muse.ai to ask whether the cabin should be shown in daytime or at night with
northern lights.

Task history (`history_log`), verbatim:

```
Dispatched to local muse2api -> external_task_id=image:task_a641da01cfe14fe8.
Poll #1..#3: status=running
Provider is waiting for operator input (question): Should it be daytime or night with northern lights?
Operator answered (text): Night with northern lights, please. Go ahead and generate the image now.
Poll #4, #5: status=running
Completed! Saved asset=ast_dbff9f44e2, take=tke_23b86de39f, last_frame=None.
```

- The task went `running` -> `awaiting_input` with `pending_input = {kind: question, text,
  options: [], asked_at}`; the Queue Monitor showed the badge, the banner and the question
  (`2026-10-23-needs-input-queue-monitor.png`).
- The answer was typed into the Queue Monitor and sent with "Send answer" (Playwright driving
  Chromium): `POST /api/v1/queue/tasks/{id}/reply` -> 200, the card switched to Running, the
  worker kept polling and the task succeeded about 45 s later.
- The stored image shows the cabin at night under northern lights
  (`2026-10-23-needs-input-queue-result.png`, downscaled), so the answer was applied.
- The first UI attempt got `403` because the Vite server ran on port 5174, which is not in
  the default `DEV_ALLOWED_ORIGINS`; the UI showed its token/forbidden error and the task
  stayed `awaiting_input`. After restarting the API with that origin allowed the same answer
  went through. This was a test-setup issue, not a product defect.
- A first enqueue that set the question through `prompt_final` produced an image without a
  question, because the prompt compiler rebuilds `prompt_final` from the shot fields; the
  question was then put in the shot `action`.

## Limitations

- One run, kind `question` only. `approval` and `stalled` were exercised only with the
  gateway's mock driver (`MOCK_APPROVAL`) and unit tests.
- The question was solicited by the prompt; unprompted questions use the same detection.
- The reply response still reported `needs_input` (snapshot taken before the runner
  resumed); the MuseStudio queue sets the task back to RUNNING itself and the next poll
  confirms it.

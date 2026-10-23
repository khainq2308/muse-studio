# M4: real Redis/ARQ worker - 2026-10-23

## Automated (CI job `arq-e2e`)

`apps/orchestrator/tests/test_arq_e2e.py` starts `uvicorn muse_orchestrator.main:app` and
`arq muse_orchestrator.workers.queue_worker.WorkerSettings` as separate processes on one
Alembic-migrated SQLite file and a real `redis:7-alpine` service. Providers: the `mock`
connection and a `muse2api` connection pointed at an in-process fake gateway, so the real
`Muse2ApiAdapter` runs over HTTP. Nothing inside the worker is patched.

| Test | Proves |
| --- | --- |
| `test_enqueue_is_dispatched_polled_and_completed_by_worker_mock_provider` | `POST /api/v1/queue/tasks` schedules a real ARQ job; the worker dispatches, polls and stores the take and asset; no job is left afterwards. |
| `test_muse2api_adapter_task_completes_through_worker` | The adapter dispatches exactly once, polls and downloads the image through the worker. |
| `test_manual_step_supersedes_scheduled_job_without_forking_chain` | An extra `POST .../step` makes the already-scheduled job stale (schedule token); it exits without polling and one chain remains. |
| `test_cancel_running_task_stops_the_worker_chain` | Cancelling a RUNNING task: the pending job runs, sees the terminal row and polls nothing; no take is created. |
| `test_awaiting_input_reply_resumes_and_completes` | `needs_input` → `AWAITING_INPUT` (15 s re-poll) → `POST .../reply` → RUNNING → SUCCEEDED, with history entries in order. |
| `test_recovery_cron_reschedules_queued_task_whose_job_was_lost` | A QUEUED task whose job was deleted from Redis is re-scheduled by the real per-minute recovery cron with the same job id and completes without a second dispatch. |

Local run on 2026-10-23 against a throwaway `redis:7-alpine`: 6 passed (~72 s), twice.

## Manual run with a real provider (2026-10-23 13:14-13:21, UTC+7)

Orchestrator API (uvicorn, throwaway SQLite and media root), a real ARQ worker on a
throwaway `redis:7-alpine` container, studio-web on the Vite dev server and the personal
muse2api gateway (khainq2308/muse2api `f68ac93`, browser driver, one real muse.ai account),
all on 127.0.0.1.

- Two image tasks were enqueued with `POST /api/v1/queue/tasks`; the worker dispatched and
  polled both with backoff.
- Task 1 succeeded and its image was stored as an asset and take.
- Task 2 went running → awaiting_input (muse.ai asked a question) → answered from the Queue
  Monitor → running → succeeded; `history_log` recorded every step. Details and images:
  docs/evidence/muse2api/2026-10-23-needs-input.md.

## Limitations

- SQLite, not PostgreSQL, in both runs; one worker process.
- No worker kill/restart in the middle of a task, no Redis outage.
- CI uses a fake gateway; the real provider was exercised manually once.

# MuseStudio Operations: backup, restore and media cleanup

This document describes behavior implemented in `apps/orchestrator/muse_orchestrator/services/backup_service.py`, `services/maintenance_service.py`, `services/asset_storage.py` and `workers/queue_worker.py`. It is not evidence that a backup taken on one deployment has been restored on another real deployment; that requires a recorded operational test.

## Project backup

`POST /api/v1/projects/{project_id}/backup` writes one ZIP per call under `MEDIA_ROOT/backups/<project>/` and records a `project_backups` row. List, download and delete use `GET /api/v1/projects/{project_id}/backups`, `GET /api/v1/backups/{backup_id}/download` and `DELETE /api/v1/backups/{backup_id}`. Only the newest `BACKUP_RETENTION_COUNT` (default 10) backups per project are kept; older archives are pruned after a successful create.

Contents (format `m7-backup-1`):

- `manifest.json` (project id/name, row counts, excluded tables, missing media);
- `data/<table>.json` for `projects`, `project_revisions`, `script_revisions`, `story_bible_entries`, `scenes`, `shots`, `workflows`, `workflow_versions`, `generation_tasks`, `assets`, `shot_takes`, `timeline_sequences`, `timeline_tracks`, `timeline_clips`, `export_jobs`;
- `media/<canonical path>` for every asset file, stored uncompressed (media is already compressed) and written once even when several asset rows share a file.

Not included: provider connections and connection accounts (environment-scoped secrets), `audit_events`, `workflow_runs`, `node_executions`, `workflow_cache_entries`, `batch_render_jobs`, `project_backups`.

Asset paths in the archive are canonical media-root-relative paths (`media_rel_path`): legacy rows holding non-canonical paths (for example `../../real/proj/x` computed against a symlinked `MEDIA_ROOT`) are rewritten in the archived row so restore finds the bytes. Files that cannot be resolved are listed in `manifest.missing_media`.

The archive is built in a worker thread into a `.part` file and promoted atomically with `os.replace`; a failed or rejected backup leaves no archive behind, and a DB failure after promotion deletes the archive.

## Shared limits (`BackupLimits`)

Create and restore read the same limits from `backup_limits()`, and create enforces every limit restore enforces, so a backup produced by an instance is never rejected by that instance's restore.

| Limit | Value |
|---|---|
| Archive size | 500 MiB |
| Total uncompressed size | 1 GiB |
| ZIP entries | 10,000 |
| One JSON entry | 50 MiB |
| All JSON entries | 64 MiB |
| Rows per table | 50,000 |
| Rows in total | 200,000 |
| One media file | 500 MiB |
| Media compression ratio | at most 100:1 for entries above 1 MiB |

Exceeding a limit returns 413. These are code constants in `services/backup_service.py`, not environment settings; the production Nginx `/api/v1/backups/restore` location allows a 520m body for the 500 MiB archive cap. Asset upload and export limits are configurable (`MAX_UPLOAD_SIZE_BYTES`, `EXPORT_*`); see [ARCHITECTURE.md](ARCHITECTURE.md#timeline-export).

## Restore

`POST /api/v1/backups/restore` (multipart `file`, optional `project_name` query) streams the upload to a temporary file, enforcing the 500 MiB cap while reading, and creates a **new** project with every ID remapped. The response contains the new project id/name and a report (`tables_restored`, `rows_skipped`, `skipped_rows` capped at 200 entries, `media_restored`, `media_missing`, `tasks_neutralized`, `exports_neutralized`, `tasks_with_placeholder_connection`).

The archive is untrusted. Before anything is written, restore rejects: invalid ZIPs, duplicate entry names, absolute or `..` entry paths, encrypted entries, oversized entries or totals, suspicious compression ratios, non-finite or oversized JSON, rows that do not match their table schema or API bounds (durations, fps, aspect ratios, JSON nesting depth, 32-bit integers), invalid workflow graphs, and any archive that does not contain **exactly one project** whose id matches `manifest.project_id`.

While inserting rows:

- **In-flight work is neutralised.** Generation tasks lose `external_task_id`; any task not already terminal becomes FAILED with `error_category = restored_incomplete`, `retryable = false`. Unfinished export jobs become FAILED. Shots in QUEUED/RUNNING become FAILED. Restored tasks and exports are never resumed; the operator starts new work.
- **Archive-supplied paths and commands are discarded.** Export `ffmpeg_args_json` is cleared and path/command keys are stripped from export snapshots. Asset rows are kept only when their bytes are re-extracted from the archive into `MEDIA_ROOT/<project>/<sha prefix>/`, re-hashed and pass the MIME allowlist (PNG, JPEG, WebP, MP4) and magic-byte check; otherwise the row is skipped and listed in `media_missing`.
- **Missing connections.** Tasks whose `connection_id` does not exist on this instance are re-pointed to one disabled, credential-less placeholder connection (`conn_restored_unavailable`, provider type `restored_placeholder`, health `UNAVAILABLE`, `max_concurrency 0`) so their takes and selected take survive. It cannot dispatch work. A missing connection account is cleared from the task.
- References to skipped rows are nulled when the column is nullable, otherwise the dependent row is skipped and reported. Idempotency keys are regenerated (`restored:<new id>`).

Any failure, conflict (409) or client cancellation rolls back the transaction and deletes every media file this restore already wrote; the worker thread is awaited before cleanup so it cannot create files afterwards.

## Orphan media cleanup

`POST /api/v1/maintenance/orphans/scan` reports and `POST /api/v1/maintenance/orphans/cleanup` (`dry_run` defaults to `true`) deletes media files that no `assets` or `project_backups` row references. The ARQ worker also runs the cleanup daily at 02:30 when `ORPHAN_CLEANUP_ENABLED` is true, with `ORPHAN_CLEANUP_RETENTION_HOURS` (default 24).

Scope rules:

- Only files older than the retention window are considered, with a hard floor of 1 hour so in-flight uploads, restores and exports are never touched.
- Only managed subtrees are scanned for deletion: `<MEDIA_ROOT>/<project dir>/...` for an existing project or a directory matching the generated `proj_<10 hex>` pattern, `<MEDIA_ROOT>/backups/<project>/...`, and root-level temporary parts `.asset-<hex>.part` / `.restore-<hex>.part`. Any other file under `MEDIA_ROOT` is never reported or deleted.
- Referenced paths are compared in both stored and canonical form, so a legacy non-canonical path does not make its file look orphaned.
- Symlinked directories are not descended into; a symlink file is unlinked as a link, never resolved to its target.
- Deletion is refused when `MEDIA_ROOT` resolves to a filesystem root. Destructive cleanups write an audit event.

## Worker recovery and readiness

The generation and export recovery crons, the stale RUNNING export sweep and export cancellation are described in [ARCHITECTURE.md](ARCHITECTURE.md#generation-queue-and-arq-worker). Use `/healthz` for liveness and `/readyz` (database and Redis) for readiness probes. Logs go to stdout, secret-redacted, with `X-Request-ID`; configure with `LOG_LEVEL` and `LOG_FORMAT`.

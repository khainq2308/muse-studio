from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy import select

from muse_orchestrator.core.config import SecretRedactor, settings
from muse_orchestrator.core.database import AsyncSessionLocal
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import (
    Asset,
    ExportJobRecord,
    ExportJobState,
    GenerationTaskRecord,
    QueueTaskState,
)
from muse_orchestrator.services.arq_dispatch import enqueue_export_job, enqueue_generation_step
from muse_orchestrator.services.generation_queue import (
    GENERATION_STEP_JOB_TIMEOUT_SECONDS,
    GenerationQueueService,
)
from muse_orchestrator.services.maintenance_service import cleanup_orphan_media
from muse_orchestrator.services.timeline_service import (
    ExportClip,
    ExportError,
    TimelineValidationService,
)

logger = logging.getLogger(__name__)

# FFmpeg exports can render for up to the 1800s subprocess timeout.
EXPORT_JOB_TIMEOUT_SECONDS = 1900
# A RUNNING export older than this has outlived any possible worker job (timeout + grace).
EXPORT_STALE_RUNNING_AFTER_SECONDS = EXPORT_JOB_TIMEOUT_SECONDS + 300
_EXPORT_STARTED_AT_KEY = "worker_started_at"


async def process_generation_task_step(ctx: dict[str, Any], task_id: str) -> dict[str, Any]:
    """
    ARQ Worker Job: chạy một bước của task và lên lịch poll tiếp theo theo backoff.
    """
    async with AsyncSessionLocal() as db:
        task = await GenerationQueueService.step_task(db, task_id)
        redis = ctx.get("redis")
        if redis:
            await enqueue_generation_step(redis, task)

        # Frame-chain auto-continue: once an upstream shot succeeds, enqueue and schedule
        # its downstream shots that are now ready (same rules as POST /batch/tick).
        auto_enqueued: list[str] = []
        if task.state == QueueTaskState.SUCCEEDED:
            repository = StudioRepository(db)
            results = await GenerationQueueService.auto_enqueue_ready_frame_chain_shots(
                repository, task.project_id, upstream_shot_id=task.shot_id
            )
            for _shot, downstream_task, _created in results:
                if redis:
                    await enqueue_generation_step(
                        redis,
                        downstream_task,
                        immediate=(
                            downstream_task.state == QueueTaskState.QUEUED
                            and not downstream_task.external_task_id
                        ),
                    )
                auto_enqueued.append(downstream_task.id)
        return {
            "task_id": task.id,
            "state": task.state.value,
            "external_task_id": task.external_task_id,
            "next_poll_delay_seconds": task.next_poll_delay_seconds,
            "auto_enqueued_task_ids": auto_enqueued,
        }


async def recover_pending_generation_tasks_job(ctx: dict[str, Any]) -> dict[str, int]:
    """Recover queued/running DB tasks after a worker restart or a failed enqueue."""
    redis = ctx.get("redis")
    if redis is None:
        return {"found": 0, "scheduled": 0}

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(GenerationTaskRecord)
            .where(GenerationTaskRecord.state.in_((QueueTaskState.QUEUED, QueueTaskState.RUNNING)))
            .order_by(GenerationTaskRecord.priority.desc(), GenerationTaskRecord.created_at.asc())
        )
        tasks = rows.scalars().all()
        scheduled = 0
        for task in tasks:
            if await enqueue_generation_step(redis, task):
                scheduled += 1
        return {"found": len(tasks), "scheduled": scheduled}


async def process_export_job(ctx: dict[str, Any], export_id: str) -> dict[str, Any]:
    """
    ARQ Worker Job: render one persisted QUEUED export job with FFmpeg.

    The API endpoint only validates and persists the job; the render (a blocking
    FFmpeg subprocess that can take minutes) happens here so no HTTP request is
    held open for the duration. The resolved clip plan was stored on the job at
    launch time; a clip file that vanished since then fails the render and the
    job is recorded FAILED, exactly like a render error.
    """
    async with AsyncSessionLocal() as db:
        repository = StudioRepository(db)
        job = await repository.get_export_job(export_id)
        if job is None:
            return {"export_id": export_id, "state": "missing"}
        if job.state != ExportJobState.QUEUED:
            return {"export_id": export_id, "state": job.state.value}

        job.state = ExportJobState.RUNNING
        manifest = dict(job.manifest_snapshot_json or {})
        manifest[_EXPORT_STARTED_AT_KEY] = datetime.now(timezone.utc).isoformat()
        job.manifest_snapshot_json = manifest
        await repository.update_export_job(job)

        out_dir = os.path.join(settings.media_root, job.project_id, "exports")

        try:
            clips = [ExportClip(**clip) for clip in manifest.get("resolved_clips", [])]
            # Defense in depth for restored/tampered jobs: every clip must live in MEDIA_ROOT.
            for clip in clips:
                if not _path_inside_media_root(clip.path):
                    raise ExportError("Export clip path resolves outside MEDIA_ROOT; refusing to render.")

            # FFmpeg is a blocking subprocess; keep it off the worker event loop.
            out_path, size_bytes, sha256_hex, ffmpeg_args, probe_meta = await asyncio.to_thread(
                TimelineValidationService.render_and_probe_mp4,
                output_dir=out_dir,
                export_id=export_id,
                fps=job.fps,
                resolution=job.resolution,
                clips=clips,
            )
            if not probe_meta.get("valid"):
                raise ExportError(
                    "File export không vượt qua kiểm tra FFprobe.",
                    ffmpeg_args=ffmpeg_args,
                    probe_meta=probe_meta,
                )
        except ExportError as exc:
            job.state = ExportJobState.FAILED
            job.ffmpeg_args_json = exc.ffmpeg_args or []
            job.ffprobe_verification_json = exc.probe_meta or {}
            job.error_message_redacted = SecretRedactor.redact_text(exc.message)
            job.finished_at = datetime.now(timezone.utc)
            await repository.update_export_job(job)
            return {"export_id": export_id, "state": ExportJobState.FAILED.value}
        except Exception as exc:
            job.state = ExportJobState.FAILED
            job.error_message_redacted = f"Unexpected export worker error ({type(exc).__name__})."
            job.finished_at = datetime.now(timezone.utc)
            await repository.update_export_job(job)
            return {"export_id": export_id, "state": ExportJobState.FAILED.value}
        except BaseException as exc:
            # CancelledError (ARQ job timeout / shutdown) must not leave the job RUNNING
            # forever. The current session may be mid-statement, so use a fresh one.
            await _mark_export_failed_fresh_session(
                export_id,
                f"Export worker was interrupted ({type(exc).__name__}); the render did not complete.",
            )
            raise

        probe_meta["unsupported_features"] = manifest.get("unsupported_features", [])
        asset_id = f"ast_exp_{uuid.uuid4().hex[:8]}"
        asset = Asset(
            id=asset_id,
            project_id=job.project_id,
            asset_type="video",
            filename=f"{job.project_id}_{export_id}.mp4",
            storage_rel_path=os.path.relpath(out_path, settings.media_root),
            mime_type="video/mp4",
            size_bytes=size_bytes,
            sha256=sha256_hex,
            is_locked_reference=False,
        )
        job.state = ExportJobState.SUCCEEDED
        job.output_asset_id = asset_id
        job.ffmpeg_args_json = ffmpeg_args
        job.ffprobe_verification_json = probe_meta
        job.finished_at = datetime.now(timezone.utc)
        await repository.create_export_result(asset, job)
        return {
            "export_id": export_id,
            "state": ExportJobState.SUCCEEDED.value,
            "output_asset_id": asset_id,
        }


def _path_inside_media_root(path: str) -> bool:
    root = os.path.realpath(settings.media_root)
    try:
        resolved = os.path.realpath(path)
        return os.path.commonpath([root, resolved]) == root
    except (TypeError, ValueError):
        return False


async def _mark_export_failed_fresh_session(export_id: str, message: str) -> None:
    try:
        async with AsyncSessionLocal() as fresh:
            repository = StudioRepository(fresh)
            job = await repository.get_export_job(export_id)
            if job is None or job.state != ExportJobState.RUNNING:
                return
            job.state = ExportJobState.FAILED
            job.error_message_redacted = SecretRedactor.redact_text(message)
            job.finished_at = datetime.now(timezone.utc)
            await repository.update_export_job(job)
    except Exception as exc:  # noqa: BLE001 - never mask the original interruption
        logger.error(
            "Could not mark interrupted export %s as FAILED (error type %s); the stale-RUNNING sweep will retry",
            export_id,
            type(exc).__name__,
        )


def _export_started_at(job: ExportJobRecord) -> datetime | None:
    raw = (job.manifest_snapshot_json or {}).get(_EXPORT_STARTED_AT_KEY)
    started: datetime | None = None
    if isinstance(raw, str):
        try:
            started = datetime.fromisoformat(raw)
        except ValueError:
            started = None
    if started is None:
        started = job.created_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started


async def fail_stale_running_export_jobs(db: Any, now: datetime | None = None) -> int:
    """Mark RUNNING exports FAILED once they are older than any possible worker job."""
    repository = StudioRepository(db)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=EXPORT_STALE_RUNNING_AFTER_SECONDS)
    failed = 0
    for job in await repository.list_export_jobs_in_state(ExportJobState.RUNNING):
        started = _export_started_at(job)
        if started is None or started > cutoff:
            continue
        job.state = ExportJobState.FAILED
        job.error_message_redacted = (
            "Export worker stopped without finishing (stale RUNNING job); please launch the export again."
        )
        job.finished_at = datetime.now(timezone.utc)
        await repository.update_export_job(job)
        failed += 1
    return failed


async def recover_pending_export_jobs_job(ctx: dict[str, Any]) -> dict[str, int]:
    """Re-enqueue QUEUED exports and fail RUNNING exports abandoned by a dead/timed-out worker."""
    redis = ctx.get("redis")
    async with AsyncSessionLocal() as db:
        stale_failed = await fail_stale_running_export_jobs(db)
        if redis is None:
            return {"found": 0, "scheduled": 0, "stale_failed": stale_failed}
        jobs = await StudioRepository(db).list_export_jobs_in_state(ExportJobState.QUEUED)
        scheduled = 0
        for job in jobs:
            if await enqueue_export_job(redis, job.id):
                scheduled += 1
        return {"found": len(jobs), "scheduled": scheduled, "stale_failed": stale_failed}


async def cleanup_orphan_media_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """M7 (cron): delete orphan media files older than the retention window."""
    async with AsyncSessionLocal() as db:
        report = await cleanup_orphan_media(
            db,
            settings.media_root,
            settings.orphan_cleanup_retention_hours,
            dry_run=False,
        )
        return {
            "deleted": report["deleted"],
            "freed_bytes": report["freed_bytes"],
            "scanned_orphans": report["scanned_orphans"],
            "errors": report["errors"],
        }


class WorkerSettings:
    functions = [
        # keep_result=0 releases the job ID as soon as a step finishes, so recovery can
        # re-enqueue the same DB state; the timeout covers the longest dispatch lease.
        func(
            process_generation_task_step,
            timeout=GENERATION_STEP_JOB_TIMEOUT_SECONDS,
            keep_result=0,
        ),
        # FFmpeg exports can render for up to the 1800s subprocess timeout, far
        # beyond the default job timeout below.
        func(process_export_job, timeout=EXPORT_JOB_TIMEOUT_SECONDS),
        recover_pending_generation_tasks_job,
        recover_pending_export_jobs_job,
        cleanup_orphan_media_job,
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    cron_jobs = [
        # Re-enqueue DB tasks once per minute. Stable job IDs prevent duplicate queue entries.
        cron(recover_pending_generation_tasks_job, second=0),
        # Same recovery for export jobs whose initial dispatch failed.
        cron(recover_pending_export_jobs_job, second=20),
        *(
            [cron(cleanup_orphan_media_job, hour=2, minute=30)]
            if settings.orphan_cleanup_enabled
            else []
        ),
    ]
    max_jobs = 20
    job_timeout = GENERATION_STEP_JOB_TIMEOUT_SECONDS

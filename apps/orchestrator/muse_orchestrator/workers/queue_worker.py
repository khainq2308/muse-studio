from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
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
from muse_orchestrator.services.generation_queue import GenerationQueueService
from muse_orchestrator.services.maintenance_service import cleanup_orphan_media
from muse_orchestrator.services.timeline_service import (
    ExportClip,
    ExportError,
    TimelineValidationService,
)

logger = logging.getLogger(__name__)


async def process_generation_task_step(ctx: dict[str, Any], task_id: str) -> dict[str, Any]:
    """
    ARQ Worker Job: chạy một bước của task và lên lịch poll tiếp theo theo backoff.
    """
    async with AsyncSessionLocal() as db:
        task = await GenerationQueueService.step_task(db, task_id)
        redis = ctx.get("redis")
        if redis:
            await enqueue_generation_step(redis, task)
        return {
            "task_id": task.id,
            "state": task.state.value,
            "external_task_id": task.external_task_id,
            "next_poll_delay_seconds": task.next_poll_delay_seconds,
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
        await repository.update_export_job(job)

        manifest = job.manifest_snapshot_json or {}
        clips = [ExportClip(**clip) for clip in manifest.get("resolved_clips", [])]
        out_dir = os.path.join(settings.media_root, job.project_id, "exports")

        try:
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


async def recover_pending_export_jobs_job(ctx: dict[str, Any]) -> dict[str, int]:
    """Re-enqueue QUEUED export jobs whose initial dispatch failed (e.g. Redis was down)."""
    redis = ctx.get("redis")
    if redis is None:
        return {"found": 0, "scheduled": 0}

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(ExportJobRecord)
            .where(ExportJobRecord.state == ExportJobState.QUEUED)
            .order_by(ExportJobRecord.created_at.asc())
        )
        jobs = rows.scalars().all()
        scheduled = 0
        for job in jobs:
            if await enqueue_export_job(redis, job.id):
                scheduled += 1
        return {"found": len(jobs), "scheduled": scheduled}


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
        process_generation_task_step,
        # FFmpeg exports can render for up to the 1800s subprocess timeout, far
        # beyond the 120s default job timeout below.
        func(process_export_job, timeout=1900),
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
    job_timeout = 120

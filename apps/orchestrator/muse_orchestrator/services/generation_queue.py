from __future__ import annotations
import hashlib
import os
import shutil
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.config import settings, SecretRedactor
from muse_orchestrator.core.security import vault
from muse_orchestrator.models.domain import (
    ShotRecord, ShotGenerationState, ProviderConnection, ConnectionHealthState,
    GenerationTaskRecord, QueueTaskState, Asset, ShotTakeRecord
)
from muse_orchestrator.providers.base import (
    VideoProvider, ImageGenerationRequest, VideoGenerationRequest,
    ProviderFailure, ErrorCategory
)
from muse_orchestrator.providers.mock_provider import MockVideoProvider
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter
from muse_orchestrator.services.asset_storage import LocalAssetStorage
from muse_orchestrator.services.frame_chain import (
    FrameExtractionError, FrameInputError, FramePair, InvalidVideoError,
    extract_frames, load_frame_data_uri, write_placeholder_frames,
)


def compute_backoff_seconds(step_count: int, base: float = 5.0, max_delay: float = 60.0) -> float:
    exp = min(max_delay, base * (1.5 ** max(0, step_count - 1)))
    jitter = (hash(f"jitter_{step_count}") % 150) / 100.0
    return round(min(max_delay, exp + jitter), 2)


def build_provider(conn: ProviderConnection) -> VideoProvider:
    if conn.provider_type == "mock":
        return MockVideoProvider()
    raw_key = vault.decrypt(conn.encrypted_api_key)
    return Muse2ApiAdapter(base_url=conn.base_url, api_key=raw_key, timeout=conn.timeout_seconds)


class GenerationQueueService:
    """
    Điều phối vòng đời sinh Ảnh/Video qua muse2api + Tự động tách Boundary Frames
    và lan truyền Frame Chaining giữa các Shot liên tiếp (M4 & M5).
    """

    @staticmethod
    async def propagate_frame_chain_downstream(db: AsyncSession, upstream_shot: ShotRecord) -> list[str]:
        """
        Nếu Shot kế tiếp có continuity_mode == 'frame_chain' và phụ thuộc vào upstream_shot,
        tự động gán first_frame_asset_id = upstream_shot.last_frame_asset_id và mở khoá.
        """
        if not upstream_shot.last_frame_asset_id:
            return []

        downstream_shots = (
            await db.execute(
                select(ShotRecord).where(
                    ShotRecord.project_id == upstream_shot.project_id,
                    ShotRecord.previous_shot_id == upstream_shot.id,
                    ShotRecord.continuity_mode == "frame_chain",
                )
            )
        ).scalars().all()

        unlocked_ids: list[str] = []
        for ds in downstream_shots:
            ds.first_frame_asset_id = upstream_shot.last_frame_asset_id
            if ds.generation_state == ShotGenerationState.BLOCKED_UPSTREAM_FRAME:
                ds.generation_state = ShotGenerationState.NOT_STARTED
            unlocked_ids.append(ds.id)

        if unlocked_ids:
            await db.commit()
        return unlocked_ids

    @staticmethod
    async def enqueue_shot_generation(
        db: AsyncSession,
        shot: ShotRecord,
        conn: ProviderConnection,
        task_type: str = "video",
        model_id: str = "muse-video-mock-v1",
        priority: int = 10,
        idempotency_key: str | None = None,
        force_retry: bool = False,
    ) -> tuple[GenerationTaskRecord, bool]:
        if shot.generation_state == ShotGenerationState.BLOCKED_REFERENCE:
            raise ValueError("Shot đang ở trạng thái BLOCKED_REFERENCE (thiếu file tham chiếu bắt buộc).")

        # Kiểm tra Frame Chaining: Nếu cần nối từ shot trước mà shot trước chưa có last_frame_asset_id -> Chặn!
        if shot.continuity_mode == "frame_chain" and shot.previous_shot_id and not shot.first_frame_asset_id:
            prev_shot = await db.get(ShotRecord, shot.previous_shot_id)
            if prev_shot and prev_shot.last_frame_asset_id:
                shot.first_frame_asset_id = prev_shot.last_frame_asset_id
            else:
                shot.generation_state = ShotGenerationState.BLOCKED_UPSTREAM_FRAME
                await db.commit()
                raise ValueError("Shot đang chờ last_frame từ Shot phía trước (BLOCKED_UPSTREAM_FRAME).")

        frame_chain_seed = shot.first_frame_asset_id or "no_first_frame"
        computed_idem = idempotency_key or hashlib.sha256(
            f"{shot.id}:{task_type}:{model_id}:{shot.prompt_hash}:{frame_chain_seed}".encode("utf-8")
        ).hexdigest()[:32]

        existing = (
            await db.execute(
                select(GenerationTaskRecord).where(GenerationTaskRecord.idempotency_key == computed_idem)
            )
        ).scalar_one_or_none()

        if existing is not None and not force_retry:
            if existing.state in (QueueTaskState.QUEUED, QueueTaskState.RUNNING, QueueTaskState.SUCCEEDED):
                return existing, False

        if existing is not None and force_retry:
            computed_idem = f"{computed_idem}_retry_{uuid.uuid4().hex[:6]}"

        task = GenerationTaskRecord(
            id=f"tsk_{uuid.uuid4().hex[:10]}",
            project_id=shot.project_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            connection_id=conn.id,
            task_type=task_type,
            model_id=model_id,
            idempotency_key=computed_idem,
            priority=priority,
            state=QueueTaskState.QUEUED,
            prompt_snapshot=shot.prompt_final or shot.action,
            negative_prompt_snapshot=shot.negative_prompt,
            prompt_hash=shot.prompt_hash,
            history_log=[f"Task enqueued for shot {shot.id} ({task_type}, first_frame={shot.first_frame_asset_id})."],
        )
        shot.generation_state = ShotGenerationState.QUEUED
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task, True

    @staticmethod
    async def step_task(db: AsyncSession, task_id: str) -> GenerationTaskRecord:
        task = await db.get(GenerationTaskRecord, task_id)
        if not task:
            raise ValueError("Task không tồn tại.")

        if task.state in (QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED, QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT):
            return task

        conn = await db.get(ProviderConnection, task.connection_id)
        shot = await db.get(ShotRecord, task.shot_id)
        if not conn or not shot:
            raise ValueError("Connection hoặc Shot liên kết không tồn tại.")

        provider = build_provider(conn)
        raw_secret = vault.decrypt(conn.encrypted_api_key) if conn.encrypted_api_key else ""

        if not task.external_task_id:
            running_count = (
                await db.execute(
                    select(func.count(GenerationTaskRecord.id)).where(
                        GenerationTaskRecord.connection_id == conn.id,
                        GenerationTaskRecord.state == QueueTaskState.RUNNING,
                        GenerationTaskRecord.id != task.id,
                    )
                )
            ).scalar_one()

            if running_count >= conn.max_concurrency:
                task.history_log = list(task.history_log or []) + [
                    f"Waiting for connection concurrency slot ({running_count}/{conn.max_concurrency} busy)."
                ]
                await db.commit()
                return task

            task.attempt_count += 1
            try:
                if task.task_type == "image":
                    p_task = await provider.generate_image(
                        ImageGenerationRequest(
                            shot_id=shot.id,
                            prompt=task.prompt_snapshot,
                            negative_prompt=task.negative_prompt_snapshot,
                            model_id=task.model_id,
                            aspect_ratio=shot.aspect_ratio,
                            idempotency_key=task.idempotency_key,
                        )
                    )
                else:
                    # A remote provider cannot resolve our asset ids, so send the frame's pixels.
                    first_frame_input = await GenerationQueueService._first_frame_input(db, shot)
                    p_task = await provider.generate_video(
                        VideoGenerationRequest(
                            shot_id=shot.id,
                            prompt=task.prompt_snapshot,
                            negative_prompt=task.negative_prompt_snapshot,
                            model_id=task.model_id,
                            duration_seconds=shot.duration_seconds,
                            aspect_ratio=shot.aspect_ratio,
                            first_frame_url=first_frame_input,
                            idempotency_key=task.idempotency_key,
                        )
                    )

                task.external_task_id = p_task.external_task_id
                task.state = QueueTaskState.RUNNING
                task.next_poll_delay_seconds = compute_backoff_seconds(1)
                shot.generation_state = ShotGenerationState.RUNNING
                task.history_log = list(task.history_log or []) + [
                    f"Dispatched to {conn.name} -> external_task_id={p_task.external_task_id}."
                ]
                await db.commit()
                await db.refresh(task)
                return task

            except ProviderFailure as exc:
                safe_err = SecretRedactor.redact_text(str(exc), [raw_secret])
                task.error_category = exc.category.value
                task.retryable = exc.retryable
                task.error_message_redacted = safe_err

                if not exc.retryable or task.attempt_count >= task.max_attempts:
                    task.state = QueueTaskState.FAILED
                    task.finished_at = datetime.now(timezone.utc)
                    shot.generation_state = ShotGenerationState.FAILED
                    if exc.category == ErrorCategory.AUTH_FAILED:
                        conn.health_state = ConnectionHealthState.AUTH_ERROR
                    elif exc.category == ErrorCategory.QUOTA_EXHAUSTED:
                        conn.health_state = ConnectionHealthState.DEGRADED
                    task.history_log = list(task.history_log or []) + [
                        f"Terminal failure on attempt {task.attempt_count}: {safe_err}"
                    ]
                else:
                    delay = compute_backoff_seconds(task.attempt_count)
                    task.state = QueueTaskState.QUEUED
                    task.next_poll_delay_seconds = delay
                    task.history_log = list(task.history_log or []) + [
                        f"Transient failure on attempt {task.attempt_count} (will retry in {delay}s): {safe_err}"
                    ]

                await db.commit()
                await db.refresh(task)
                return task

        task.poll_count += 1
        if task.poll_count > task.max_polls:
            task.state = QueueTaskState.POLL_TIMEOUT
            task.error_category = ErrorCategory.UPSTREAM_POLL_TIMEOUT.value
            task.retryable = True
            task.error_message_redacted = f"Exceeded max_polls ({task.max_polls}) waiting for upstream task {task.external_task_id}."
            task.finished_at = datetime.now(timezone.utc)
            shot.generation_state = ShotGenerationState.FAILED
            task.history_log = list(task.history_log or []) + [task.error_message_redacted]
            await db.commit()
            await db.refresh(task)
            return task

        p_status = await provider.get_task(task.external_task_id)
        if p_status.account_used:
            task.account_used = p_status.account_used

        if p_status.status in ("queued", "running"):
            delay = compute_backoff_seconds(task.poll_count)
            task.next_poll_delay_seconds = delay
            task.history_log = list(task.history_log or []) + [
                f"Poll #{task.poll_count}: status={p_status.status} (next poll in {delay}s)."
            ]
            await db.commit()
            await db.refresh(task)
            return task

        if p_status.status == "failed":
            task.state = QueueTaskState.FAILED
            task.error_category = "upstream_generation_failed"
            task.retryable = False
            task.error_message_redacted = SecretRedactor.redact_text(p_status.error_message or "Upstream reported generation failed.", [raw_secret])
            task.finished_at = datetime.now(timezone.utc)
            shot.generation_state = ShotGenerationState.FAILED
            task.history_log = list(task.history_log or []) + [f"Upstream task failed: {task.error_message_redacted}"]
            await db.commit()
            await db.refresh(task)
            return task

        dest_dir = os.path.join(settings.media_root, task.project_id, "generated")
        try:
            stored = await provider.download_result(p_status.result_media_url or task.external_task_id, dest_dir)
        except ProviderFailure as exc:
            return await GenerationQueueService._handle_download_failure(db, task, shot, conn, exc, raw_secret)

        # The downloader names the type from the file's own bytes; it must be what this task asked for.
        expected_kind = "video/" if task.task_type == "video" else "image/"
        if not stored.mime_type.startswith(expected_kind):
            await GenerationQueueService._discard_download(db, stored.local_path)
            return await GenerationQueueService._fail_task(
                db, task, shot, ErrorCategory.INVALID_UPSTREAM_MEDIA.value,
                f"Provider returned {stored.mime_type} for a {task.task_type} task.",
                "Result rejected",
            )
        rel_path = os.path.relpath(stored.local_path, settings.media_root)

        existing_takes_count = (
            await db.execute(select(func.count(ShotTakeRecord.id)).where(ShotTakeRecord.shot_id == shot.id))
        ).scalar_one()
        take_num = existing_takes_count + 1
        is_first_take = existing_takes_count == 0
        take_id = f"tke_{uuid.uuid4().hex[:10]}"

        # First/last frame of a video take, for frame chaining. Real providers get real frames from
        # FFmpeg; only the mock provider (whose "videos" are not decodable) gets placeholder frames.
        frames: FramePair | None = None
        frame_warning: str | None = None
        if task.task_type == "video":
            frames_dir = os.path.join(dest_dir, "frames", take_id)
            try:
                if conn.provider_type == "mock":
                    frames = write_placeholder_frames(frames_dir, f"{stored.sha256}:{shot.id}:{take_num}")
                else:
                    frames = await extract_frames(stored.local_path, frames_dir)
            except InvalidVideoError as exc:
                shutil.rmtree(frames_dir, ignore_errors=True)
                await GenerationQueueService._discard_download(db, stored.local_path)
                return await GenerationQueueService._fail_task(
                    db, task, shot, ErrorCategory.INVALID_UPSTREAM_MEDIA.value,
                    "Provider returned a video that cannot be decoded: "
                    + SecretRedactor.redact_text(str(exc), [raw_secret]),
                    "Result rejected",
                )
            except FrameExtractionError as exc:
                # FFmpeg itself is unavailable: keep the (paid for) video, but say frames are missing.
                shutil.rmtree(frames_dir, ignore_errors=True)
                frame_warning = SecretRedactor.redact_text(str(exc), [raw_secret])

        asset_id = f"ast_{uuid.uuid4().hex[:10]}"
        db.add(Asset(
            id=asset_id,
            project_id=task.project_id,
            scene_id=task.scene_id,
            shot_id=task.shot_id,
            asset_type=task.task_type,
            filename=os.path.basename(stored.local_path),
            storage_rel_path=rel_path,
            mime_type=stored.mime_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            provider_id=conn.id,
            model_id=task.model_id,
            is_locked_reference=(task.task_type == "image"),
        ))

        first_frame_aid: str | None = None
        last_frame_aid: str | None = None
        if frames is not None:
            first_frame_aid = f"ast_ff_{uuid.uuid4().hex[:8]}"
            last_frame_aid = f"ast_lf_{uuid.uuid4().hex[:8]}"
            for frame_aid, label, frame_path, frame_sha in (
                (first_frame_aid, "first", frames.first_path, frames.first_sha256),
                (last_frame_aid, "last", frames.last_path, frames.last_sha256),
            ):
                db.add(Asset(
                    id=frame_aid,
                    project_id=task.project_id,
                    scene_id=task.scene_id,
                    shot_id=shot.id,
                    asset_type="reference",
                    filename=f"{shot.id}_take{take_num}_{label}.png",
                    storage_rel_path=os.path.relpath(frame_path, settings.media_root),
                    mime_type="image/png",
                    size_bytes=os.path.getsize(frame_path),
                    sha256=frame_sha,
                    provider_id=conn.id,
                    model_id=task.model_id,
                    is_locked_reference=True,
                ))

        db.add(ShotTakeRecord(
            id=take_id,
            project_id=task.project_id,
            shot_id=shot.id,
            take_number=take_num,
            task_id=task.id,
            asset_id=asset_id,
            first_frame_asset_id=first_frame_aid,
            last_frame_asset_id=last_frame_aid,
            model_id=task.model_id,
            prompt_hash=task.prompt_hash,
            account_used=task.account_used,
            is_selected=is_first_take,
        ))

        if is_first_take:
            shot.selected_take_id = take_id
            # shot.first_frame_asset_id is an *input* (the previous shot's last frame, or one the
            # operator set); it is deliberately not overwritten with this take's own first frame.
            if last_frame_aid:
                shot.last_frame_asset_id = last_frame_aid

        shot.generation_state = ShotGenerationState.SUCCEEDED
        task.result_asset_id = asset_id
        task.take_id = take_id
        task.state = QueueTaskState.SUCCEEDED
        task.finished_at = datetime.now(timezone.utc)
        task.history_log = list(task.history_log or []) + [
            f"Completed! Saved asset={asset_id}, take={take_id}, last_frame={last_frame_aid}."
        ]
        if frame_warning:
            task.error_category = "frame_extraction_unavailable"
            task.error_message_redacted = frame_warning
            task.history_log = task.history_log + [
                f"WARNING: boundary frames were not extracted, frame chaining from this take is blocked: {frame_warning}"
            ]

        await db.commit()
        if is_first_take and last_frame_aid:
            await GenerationQueueService.propagate_frame_chain_downstream(db, shot)
        await db.refresh(task)
        return task

    @staticmethod
    async def _first_frame_input(db: AsyncSession, shot: ShotRecord) -> str | None:
        """The shot's input first frame as a data: URI, or None when it has none."""
        if not shot.first_frame_asset_id:
            return None
        asset = await db.get(Asset, shot.first_frame_asset_id)
        if asset is None or asset.project_id != shot.project_id:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                f"First frame asset '{shot.first_frame_asset_id}' does not exist in this project.",
            )
        try:
            return load_frame_data_uri(LocalAssetStorage(settings.media_root), asset.storage_rel_path)
        except FrameInputError as exc:
            raise ProviderFailure(ErrorCategory.INVALID_REQUEST, str(exc)) from exc

    @staticmethod
    async def _discard_download(db: AsyncSession, local_path: str) -> None:
        """Delete a rejected download, unless an existing asset already stores the same file."""
        rel_path = os.path.relpath(local_path, settings.media_root)
        references = (
            await db.execute(select(func.count(Asset.id)).where(Asset.storage_rel_path == rel_path))
        ).scalar_one()
        if references == 0:
            try:
                os.unlink(local_path)
            except OSError:
                pass

    @staticmethod
    async def _fail_task(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        category: str,
        message: str,
        label: str,
    ) -> GenerationTaskRecord:
        task.state = QueueTaskState.FAILED
        task.error_category = category
        task.retryable = False
        task.error_message_redacted = message
        task.finished_at = datetime.now(timezone.utc)
        shot.generation_state = ShotGenerationState.FAILED
        task.history_log = list(task.history_log or []) + [f"{label}: {message}"]
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def _handle_download_failure(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        conn: ProviderConnection,
        exc: ProviderFailure,
        raw_secret: str,
    ) -> GenerationTaskRecord:
        safe_err = SecretRedactor.redact_text(str(exc), [raw_secret])
        if exc.retryable:
            # Stay RUNNING: the next step polls and downloads again (bounded by max_polls).
            delay = compute_backoff_seconds(task.poll_count)
            task.next_poll_delay_seconds = delay
            task.history_log = list(task.history_log or []) + [
                f"Result download failed (will retry in {delay}s): {safe_err}"
            ]
            await db.commit()
            await db.refresh(task)
            return task
        if exc.category == ErrorCategory.AUTH_FAILED:
            conn.health_state = ConnectionHealthState.AUTH_ERROR
        elif exc.category == ErrorCategory.QUOTA_EXHAUSTED:
            conn.health_state = ConnectionHealthState.DEGRADED
        return await GenerationQueueService._fail_task(
            db, task, shot, exc.category.value, safe_err, "Result download failed"
        )

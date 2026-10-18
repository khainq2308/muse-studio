from __future__ import annotations
import asyncio
import hashlib
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.config import settings, SecretRedactor
from muse_orchestrator.core.security import vault
from muse_orchestrator.models.domain import (
    ShotRecord, ShotGenerationState, ProviderConnection, ConnectionHealthState,
    GenerationTaskRecord, GenerationDispatchLeaseRecord, QueueTaskState, Asset, ShotTakeRecord
)
from muse_orchestrator.providers.base import (
    VideoProvider, ImageGenerationRequest, VideoGenerationRequest,
    ProviderFailure, ErrorCategory
)
from muse_orchestrator.providers.mock_provider import MockVideoProvider
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter


def compute_backoff_seconds(step_count: int, base: float = 5.0, max_delay: float = 60.0) -> float:
    exp = min(max_delay, base * (1.5 ** max(0, step_count - 1)))
    # random.uniform: str hash() is salted per process, so it gave identical jitter to every task.
    jitter = random.uniform(0.0, 1.5)
    return round(min(max_delay, exp + jitter), 2)


def build_provider(conn: ProviderConnection) -> VideoProvider:
    if conn.provider_type == "mock":
        return MockVideoProvider()
    raw_key = vault.decrypt(conn.encrypted_api_key)
    return Muse2ApiAdapter(base_url=conn.base_url, api_key=raw_key, timeout=conn.timeout_seconds)


def _create_boundary_frame_png(dest_dir: str, shot_id: str, take_num: int, position: str, seed_hash: str) -> tuple[str, int, str]:
    """Tạo file ảnh PNG boundary frame (first_frame / last_frame) phục vụ Frame Chaining."""
    os.makedirs(dest_dir, exist_ok=True)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        + f"BOUNDARY_FRAME:{shot_id}:take_{take_num}:{position}:{seed_hash}".encode("utf-8")
    )
    digest = hashlib.sha256(png_bytes).hexdigest()
    filename = f"{shot_id}_take{take_num}_{position}_{digest[:10]}.png"
    full_path = os.path.join(dest_dir, filename)
    with open(full_path, "wb") as f:
        f.write(png_bytes)
    return full_path, len(png_bytes), digest



async def _verify_downloaded_artifact(path: str, task_type: str, provider_type: str, mime_type: str) -> None:
    """Reject empty, mislabeled, or undecodable media before the task can succeed."""
    if provider_type == "mock":
        # Mock provider artifacts are deliberately synthetic and used only in tests/demo flows.
        return
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Provider returned an empty media file.")
        with open(path, "rb") as stream:
            header = stream.read(32)
    except OSError as exc:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "Downloaded media cannot be read from disk.", retryable=False
        ) from exc

    if task_type == "image":
        valid_signatures = {
            "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": header.startswith(b"\xff\xd8\xff"),
            "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
        }
        if mime_type not in valid_signatures or not valid_signatures[mime_type]:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST, "Downloaded image failed format/signature validation."
            )
        return

    if task_type != "video" or mime_type != "video/mp4" or len(header) < 12 or header[4:8] != b"ftyp":
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "Downloaded video failed MP4 signature validation."
        )

    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_type", "-of", "json", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
    except (OSError, asyncio.TimeoutError) as exc:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "FFprobe could not verify the downloaded video.", retryable=False
        ) from exc
    if process.returncode != 0:
        raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Downloaded MP4 is not decodable by FFprobe.")
    try:
        streams = json.loads(stdout.decode("utf-8", "replace")).get("streams", [])
    except (json.JSONDecodeError, AttributeError):
        streams = []
    if not any(stream.get("codec_type") == "video" for stream in streams if isinstance(stream, dict)):
        raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Downloaded MP4 contains no valid video stream.")


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
            # Strict idempotency: the same key returns the same task, whatever its
            # state. A new generation can cost provider quota, so it requires an
            # explicit force_retry=True instead of happening on a plain retry.
            return existing, False

        if existing is not None:
            # force_retry=True: idempotency_key is UNIQUE, so a fresh task needs a
            # fresh key (was an IntegrityError).
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
    async def _handle_poll_failure(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        conn: ProviderConnection,
        exc: ProviderFailure,
        raw_secret: str,
        phase: str,
    ) -> GenerationTaskRecord:
        """A poll/download error must never leave the task RUNNING with no follow-up job.

        Retryable errors keep the task RUNNING with a backoff (poll_count already bounds the
        number of tries via max_polls); anything else is a terminal FAILED that frees the slot.
        """
        safe_err = SecretRedactor.redact_text(str(exc), [raw_secret])
        # The network lease was committed before the call; clear it on every failure path.
        await db.refresh(task)
        await db.refresh(shot)
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            await db.delete(lease)
        if task.state in (
            QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
            QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
        ):
            await db.commit()
            await db.refresh(task)
            return task

        task.error_category = exc.category.value
        task.retryable = exc.retryable
        task.error_message_redacted = safe_err
        if exc.retryable:
            delay = compute_backoff_seconds(task.poll_count)
            task.next_poll_delay_seconds = delay
            task.history_log = list(task.history_log or []) + [
                f"Transient {phase} failure (poll #{task.poll_count}, retry in {delay}s): {safe_err}"
            ]
        else:
            task.state = QueueTaskState.FAILED
            task.finished_at = datetime.now(timezone.utc)
            shot.generation_state = ShotGenerationState.FAILED
            if exc.category == ErrorCategory.AUTH_FAILED:
                conn.health_state = ConnectionHealthState.AUTH_ERROR
            elif exc.category == ErrorCategory.QUOTA_EXHAUSTED:
                conn.health_state = ConnectionHealthState.DEGRADED
            task.history_log = list(task.history_log or []) + [
                f"Terminal {phase} failure: {safe_err}"
            ]
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def step_task(db: AsyncSession, task_id: str) -> GenerationTaskRecord:
        # Row lock (no-op on SQLite) so two workers cannot step/dispatch the same task at once.
        task = (
            await db.execute(
                select(GenerationTaskRecord)
                .where(GenerationTaskRecord.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not task:
            raise ValueError("Task không tồn tại.")

        if task.state in (QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED, QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT):
            return task

        # Claim any in-flight dispatch/poll/download before releasing the task row lock.
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        now = datetime.now(timezone.utc)
        if lease is not None:
            expiry = lease.expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > now:
                task.next_poll_delay_seconds = max(1.0, min(30.0, (expiry - now).total_seconds()))
                await db.commit()
                await db.refresh(task)
                return task
            await db.delete(lease)
            await db.flush()

        # Polling an existing task does not consume a new slot. Lock only for dispatch reservation.
        if task.external_task_id:
            conn = await db.get(ProviderConnection, task.connection_id)
        else:
            conn = (
                await db.execute(
                    select(ProviderConnection)
                    .where(ProviderConnection.id == task.connection_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
        shot = await db.get(ShotRecord, task.shot_id)
        if not conn or not shot:
            raise ValueError("Connection hoặc Shot liên kết không tồn tại.")

        provider = build_provider(conn)
        raw_secret = vault.decrypt(conn.encrypted_api_key) if conn.encrypted_api_key else ""

        if not task.external_task_id:
            # Reserve the slot in a short transaction, then release DB locks before network I/O.
            now = datetime.now(timezone.utc)
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
                task.state = QueueTaskState.QUEUED
                shot.generation_state = ShotGenerationState.QUEUED
                task.next_poll_delay_seconds = compute_backoff_seconds(1)
                task.history_log = list(task.history_log or []) + [
                    f"Waiting for connection concurrency slot ({running_count}/{conn.max_concurrency} busy)."
                ]
                await db.commit()
                await db.refresh(task)
                return task

            task.attempt_count += 1
            task.state = QueueTaskState.RUNNING
            shot.generation_state = ShotGenerationState.RUNNING
            task.next_poll_delay_seconds = compute_backoff_seconds(1)
            lease_seconds = max(90.0, float(conn.timeout_seconds) * 2.0 + 30.0)
            db.add(GenerationDispatchLeaseRecord(
                task_id=task.id,
                connection_id=conn.id,
                expires_at=now + timedelta(seconds=lease_seconds),
            ))
            # expire_on_commit=False keeps the claimed task snapshot available without opening a
            # new DB transaction while the provider request is in flight.
            await db.commit()

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
                    p_task = await provider.generate_video(
                        VideoGenerationRequest(
                            shot_id=shot.id,
                            prompt=task.prompt_snapshot,
                            negative_prompt=task.negative_prompt_snapshot,
                            model_id=task.model_id,
                            duration_seconds=shot.duration_seconds,
                            aspect_ratio=shot.aspect_ratio,
                            first_frame_url=shot.first_frame_asset_id,
                            idempotency_key=task.idempotency_key,
                        )
                    )
            except ProviderFailure as exc:
                await db.refresh(task)
                await db.refresh(shot)
                lease = await db.get(GenerationDispatchLeaseRecord, task.id)
                if lease is not None:
                    await db.delete(lease)
                if task.state in (
                    QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
                    QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
                ):
                    await db.commit()
                    await db.refresh(task)
                    return task

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
                    shot.generation_state = ShotGenerationState.QUEUED
                    task.next_poll_delay_seconds = delay
                    task.history_log = list(task.history_log or []) + [
                        f"Transient failure on attempt {task.attempt_count} (will retry in {delay}s): {safe_err}"
                    ]
                await db.commit()
                await db.refresh(task)
                return task
            except Exception as exc:
                # Unexpected adapter failures are terminal rather than leaving RUNNING with no upstream ID.
                await db.refresh(task)
                await db.refresh(shot)
                lease = await db.get(GenerationDispatchLeaseRecord, task.id)
                if lease is not None:
                    await db.delete(lease)
                if task.state not in (
                    QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
                    QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
                ):
                    task.state = QueueTaskState.FAILED
                    task.finished_at = datetime.now(timezone.utc)
                    task.retryable = False
                    task.error_category = "provider_internal_error"
                    task.error_message_redacted = f"Unexpected provider adapter error ({type(exc).__name__})."
                    shot.generation_state = ShotGenerationState.FAILED
                    task.history_log = list(task.history_log or []) + [task.error_message_redacted]
                await db.commit()
                await db.refresh(task)
                return task

            # Another operator may have paused/canceled the task while the request was in flight.
            await db.refresh(task)
            await db.refresh(shot)
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
            task.external_task_id = p_task.external_task_id
            task.history_log = list(task.history_log or []) + [
                f"Dispatched to {conn.name} -> external_task_id={p_task.external_task_id}."
            ]
            if task.state not in (
                QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
                QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
            ):
                task.state = QueueTaskState.RUNNING
                shot.generation_state = ShotGenerationState.RUNNING
            else:
                task.history_log = list(task.history_log or []) + [
                    "Provider accepted the request after the local task state changed; local state was preserved."
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

        # Poll/download can take seconds; reserve with a short-lived lease and commit first.
        db.add(GenerationDispatchLeaseRecord(
            task_id=task.id,
            connection_id=conn.id,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=max(90.0, float(conn.timeout_seconds) * 2.0 + 30.0)),
        ))
        await db.commit()
        try:
            p_status = await provider.get_task(task.external_task_id)
        except ProviderFailure as exc:
            return await GenerationQueueService._handle_poll_failure(db, task, shot, conn, exc, raw_secret, "poll")
        await db.refresh(task)
        await db.refresh(shot)
        if task.state in (
            QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
            QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
        ):
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
            await db.commit()
            await db.refresh(task)
            return task

        if p_status.account_used:
            task.account_used = p_status.account_used

        if p_status.status in ("queued", "running"):
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
            delay = compute_backoff_seconds(task.poll_count)
            task.next_poll_delay_seconds = delay
            task.history_log = list(task.history_log or []) + [
                f"Poll #{task.poll_count}: status={p_status.status} (next poll in {delay}s)."
            ]
            await db.commit()
            await db.refresh(task)
            return task

        if p_status.status == "failed":
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
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
        stored = None
        try:
            stored = await provider.download_result(p_status.result_media_url or task.external_task_id, dest_dir)
            await _verify_downloaded_artifact(
                stored.local_path, task.task_type, conn.provider_type, stored.mime_type
            )
        except ProviderFailure as exc:
            if stored is not None:
                try:
                    os.unlink(stored.local_path)
                except OSError:
                    pass
            return await GenerationQueueService._handle_poll_failure(db, task, shot, conn, exc, raw_secret, "download")
        await db.refresh(task)
        await db.refresh(shot)
        if task.state in (
            QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
            QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
        ):
            try:
                os.unlink(stored.local_path)
            except OSError:
                pass
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
            await db.commit()
            await db.refresh(task)
            return task

        rel_path = os.path.relpath(stored.local_path, settings.media_root)

        asset_id = f"ast_{uuid.uuid4().hex[:10]}"
        asset = Asset(
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
        )
        db.add(asset)

        existing_takes_count = (
            await db.execute(select(func.count(ShotTakeRecord.id)).where(ShotTakeRecord.shot_id == shot.id))
        ).scalar_one()
        take_num = existing_takes_count + 1
        is_first_take = existing_takes_count == 0

        # Trích xuất First Frame & Last Frame cho Video Take để phục vụ Frame Chaining (M5)
        first_frame_aid: str | None = None
        last_frame_aid: str | None = None
        if task.task_type == "video":
            ff_path, ff_size, ff_sha = _create_boundary_frame_png(dest_dir, shot.id, take_num, "first", stored.sha256)
            lf_path, lf_size, lf_sha = _create_boundary_frame_png(dest_dir, shot.id, take_num, "last", stored.sha256)
            first_frame_aid = f"ast_ff_{uuid.uuid4().hex[:8]}"
            last_frame_aid = f"ast_lf_{uuid.uuid4().hex[:8]}"
            db.add_all([
                Asset(
                    id=first_frame_aid,
                    project_id=task.project_id,
                    scene_id=task.scene_id,
                    shot_id=shot.id,
                    asset_type="reference",
                    filename=os.path.basename(ff_path),
                    storage_rel_path=os.path.relpath(ff_path, settings.media_root),
                    mime_type="image/png",
                    size_bytes=ff_size,
                    sha256=ff_sha,
                    provider_id=conn.id,
                    model_id=task.model_id,
                    is_locked_reference=True,
                ),
                Asset(
                    id=last_frame_aid,
                    project_id=task.project_id,
                    scene_id=task.scene_id,
                    shot_id=shot.id,
                    asset_type="reference",
                    filename=os.path.basename(lf_path),
                    storage_rel_path=os.path.relpath(lf_path, settings.media_root),
                    mime_type="image/png",
                    size_bytes=lf_size,
                    sha256=lf_sha,
                    provider_id=conn.id,
                    model_id=task.model_id,
                    is_locked_reference=True,
                ),
            ])

        take_id = f"tke_{uuid.uuid4().hex[:10]}"
        take = ShotTakeRecord(
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
        )
        db.add(take)

        if is_first_take:
            shot.selected_take_id = take_id
            if not shot.first_frame_asset_id:
                shot.first_frame_asset_id = first_frame_aid
            shot.last_frame_asset_id = last_frame_aid

        shot.generation_state = ShotGenerationState.SUCCEEDED
        task.result_asset_id = asset_id
        task.take_id = take_id
        task.state = QueueTaskState.SUCCEEDED
        task.finished_at = datetime.now(timezone.utc)
        task.history_log = list(task.history_log or []) + [
            f"Completed! Saved asset={asset_id}, take={take_id}, last_frame={last_frame_aid}."
        ]

        await db.commit()
        if is_first_take and last_frame_aid:
            await GenerationQueueService.propagate_frame_chain_downstream(db, shot)
        await db.refresh(task)
        return task

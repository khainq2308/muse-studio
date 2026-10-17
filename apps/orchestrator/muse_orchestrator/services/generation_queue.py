from __future__ import annotations
import hashlib
import os
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


def compute_backoff_seconds(step_count: int, base: float = 5.0, max_delay: float = 60.0) -> float:
    """Tính thời gian chờ Exponential Backoff + Jitter giới hạn (Mục 3.7 PRD)."""
    exp = min(max_delay, base * (1.5 ** max(0, step_count - 1)))
    jitter = (hash(f"jitter_{step_count}") % 150) / 100.0  # 0.00s -> 1.49s
    return round(min(max_delay, exp + jitter), 2)


def build_provider(conn: ProviderConnection) -> VideoProvider:
    if conn.provider_type == "mock":
        return MockVideoProvider()
    raw_key = vault.decrypt(conn.encrypted_api_key)
    return Muse2ApiAdapter(base_url=conn.base_url, api_key=raw_key, timeout=conn.timeout_seconds)


class GenerationQueueService:
    """
    Điều phối toàn bộ vòng đời sinh Ảnh/Video qua muse2api:
    - Chống sinh trùng lặp bằng Idempotency Key.
    - Kiểm soát Concurrency theo từng Connection.
    - Phân loại lỗi Retry (dừng ngay khi gặp lỗi Auth/Quota).
    - Tự động tải file, đăng ký Asset Library và tạo ShotTakeRecord khi hoàn tất.
    """

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

        computed_idem = idempotency_key or hashlib.sha256(
            f"{shot.id}:{task_type}:{model_id}:{shot.prompt_hash}".encode("utf-8")
        ).hexdigest()[:32]

        existing = (
            await db.execute(
                select(GenerationTaskRecord).where(GenerationTaskRecord.idempotency_key == computed_idem)
            )
        ).scalar_one_or_none()

        if existing is not None and not force_retry:
            if existing.state in (QueueTaskState.QUEUED, QueueTaskState.RUNNING, QueueTaskState.SUCCEEDED):
                return existing, False  # Idempotent hit: không tạo task trùng lặp!

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
            history_log=[f"Task enqueued for shot {shot.id} ({task_type})."],
        )
        shot.generation_state = ShotGenerationState.QUEUED
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task, True

    @staticmethod
    async def step_task(db: AsyncSession, task_id: str) -> GenerationTaskRecord:
        """
        Thực thi 1 bước không chặn (non-blocking step) cho Task:
        - Nếu chưa có external_task_id: Kiểm tra concurrency và gọi POST /v1/videos (hoặc /v1/images/generations).
        - Nếu đã có external_task_id: Gọi GET /v1/videos/{task_id} để poll trạng thái và tải Media khi hoàn tất.
        """
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

        # PHASE 1: DISPATCH LÊN UPSTREAM NẾU CHƯA CÓ EXTERNAL_TASK_ID
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
                    p_task = await provider.generate_video(
                        VideoGenerationRequest(
                            shot_id=shot.id,
                            prompt=task.prompt_snapshot,
                            negative_prompt=task.negative_prompt_snapshot,
                            model_id=task.model_id,
                            duration_seconds=shot.duration_seconds,
                            aspect_ratio=shot.aspect_ratio,
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

                # Lỗi Auth / Quota / Invalid Request -> KHÔNG retry vô hạn, khoá ngay!
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

        # PHASE 2: POLL TRẠNG THÁI TASK BẤT ĐỒNG BỘ TỪ MUSE2API
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

        # KHI SUCCEEDED -> TẢI MEDIA VỀ ASSET LIBRARY & TẠO SHOT TAKE RECORD
        dest_dir = os.path.join(settings.media_root, task.project_id, "generated")
        stored = await provider.download_result(p_status.result_media_url or task.external_task_id, dest_dir)
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
        is_first_take = existing_takes_count == 0

        take_id = f"tke_{uuid.uuid4().hex[:10]}"
        take = ShotTakeRecord(
            id=take_id,
            project_id=task.project_id,
            shot_id=shot.id,
            take_number=existing_takes_count + 1,
            task_id=task.id,
            asset_id=asset_id,
            model_id=task.model_id,
            prompt_hash=task.prompt_hash,
            account_used=task.account_used,
            is_selected=is_first_take,
        )
        db.add(take)

        if is_first_take:
            shot.selected_take_id = take_id

        shot.generation_state = ShotGenerationState.SUCCEEDED
        task.result_asset_id = asset_id
        task.take_id = take_id
        task.state = QueueTaskState.SUCCEEDED
        task.finished_at = datetime.now(timezone.utc)
        task.history_log = list(task.history_log or []) + [
            f"Completed! Saved asset={asset_id} (sha256={stored.sha256[:10]}), take={take_id}."
        ]

        await db.commit()
        await db.refresh(task)
        return task

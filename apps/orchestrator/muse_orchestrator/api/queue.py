from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.database import get_db
from muse_orchestrator.services.arq_dispatch import enqueue_generation_step
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import (
    ShotRecord, ShotGenerationState, ProviderConnection,
    GenerationTaskRecord, QueueTaskState, ShotTakeRecord
)
from muse_orchestrator.services.generation_queue import GenerationQueueService

router = APIRouter(prefix="/api/v1/queue", tags=["M4 Generation Queue & Takes"])
logger = logging.getLogger(__name__)


async def _schedule_generation_task(request: Request, task: GenerationTaskRecord) -> None:
    """Publish an already-persisted active task to ARQ, failing visibly if Redis is down."""
    if task.state not in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
        return
    redis = getattr(request.app.state, "arq_pool", None)
    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "task_id": task.id,
                "message": "Task is saved, but the ARQ scheduler is not initialized. Retry the same idempotent request.",
            },
        )
    try:
        await enqueue_generation_step(redis, task)
    except Exception as exc:
        logger.exception("Could not schedule generation task %s in ARQ", task.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "task_id": task.id,
                "message": "Task is saved, but Redis scheduling failed. Retry the same idempotent request.",
            },
        ) from exc


class EnqueueTaskIn(BaseModel):
    shot_id: str
    connection_id: str
    task_type: str = Field(default="video", pattern=r"^(image|video)$")
    model_id: str = Field(default="muse-video-mock-v1")
    priority: int = Field(default=10, ge=1, le=100)
    idempotency_key: str | None = None
    force_retry: bool = False
    max_polls: int = Field(default=60, ge=1, le=500)


def _serialize_task(t: GenerationTaskRecord) -> dict[str, Any]:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "scene_id": t.scene_id,
        "shot_id": t.shot_id,
        "connection_id": t.connection_id,
        "task_type": t.task_type,
        "model_id": t.model_id,
        "idempotency_key": t.idempotency_key,
        "priority": t.priority,
        "state": t.state.value,
        "external_task_id": t.external_task_id,
        "account_used": t.account_used,
        "attempt_count": t.attempt_count,
        "max_attempts": t.max_attempts,
        "poll_count": t.poll_count,
        "max_polls": t.max_polls,
        "next_poll_delay_seconds": t.next_poll_delay_seconds,
        "error_category": t.error_category,
        "retryable": t.retryable,
        "error_message_redacted": t.error_message_redacted,
        "result_asset_id": t.result_asset_id,
        "take_id": t.take_id,
        "history_log": t.history_log,
    }


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def enqueue_generation_task(
    payload: EnqueueTaskIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    shot = await db.get(ShotRecord, payload.shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot không tồn tại.")

    conn = await db.get(ProviderConnection, payload.connection_id)
    if not conn or not conn.enabled:
        raise HTTPException(status_code=400, detail="Connection không tồn tại hoặc đang bị vô hiệu hoá.")

    try:
        task, created = await GenerationQueueService.enqueue_shot_generation(
            db=db,
            shot=shot,
            conn=conn,
            task_type=payload.task_type,
            model_id=payload.model_id,
            priority=payload.priority,
            idempotency_key=payload.idempotency_key,
            force_retry=payload.force_retry,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if created and payload.max_polls != 60:
        task.max_polls = payload.max_polls
        await db.commit()
        await db.refresh(task)

    await _schedule_generation_task(request, task)

    out = _serialize_task(task)
    out["created_new"] = created
    return out


@router.get("/tasks")
async def list_queue_tasks(
    project_id: str | None = None,
    connection_id: str | None = None,
    state: str | None = None,
    model_id: str | None = None,
    account_used: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(GenerationTaskRecord).order_by(
        GenerationTaskRecord.priority.desc(),
        GenerationTaskRecord.created_at.desc(),
    )
    if project_id:
        stmt = stmt.where(GenerationTaskRecord.project_id == project_id)
    if connection_id:
        stmt = stmt.where(GenerationTaskRecord.connection_id == connection_id)
    if state:
        try:
            state_filter = QueueTaskState(state)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"state không hợp lệ. Giá trị cho phép: {[s.value for s in QueueTaskState]}",
            )
        stmt = stmt.where(GenerationTaskRecord.state == state_filter)
    if model_id:
        stmt = stmt.where(GenerationTaskRecord.model_id == model_id)
    if account_used:
        stmt = stmt.where(GenerationTaskRecord.account_used == account_used)

    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize_task(r) for r in rows]


@router.post("/tasks/{task_id}/step")
async def step_generation_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    try:
        task = await GenerationQueueService.step_task(db, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize_task(task)


@router.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str, db: AsyncSession = Depends(get_db), actor: str = Depends(verify_single_admin)) -> dict[str, Any]:
    task = await db.get(GenerationTaskRecord, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task không tồn tại.")
    if task.state in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
        task.state = QueueTaskState.PAUSED
        task.history_log = list(task.history_log or []) + ["Paused by operator."]
        await db.commit()
    return _serialize_task(task)


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str, db: AsyncSession = Depends(get_db), actor: str = Depends(verify_single_admin)) -> dict[str, Any]:
    task = await db.get(GenerationTaskRecord, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task không tồn tại.")
    if task.state == QueueTaskState.PAUSED:
        task.state = QueueTaskState.RUNNING if task.external_task_id else QueueTaskState.QUEUED
        task.history_log = list(task.history_log or []) + ["Resumed by operator."]
        await db.commit()
    return _serialize_task(task)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, db: AsyncSession = Depends(get_db), actor: str = Depends(verify_single_admin)) -> dict[str, Any]:
    task = await db.get(GenerationTaskRecord, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task không tồn tại.")
    if task.state in (
        QueueTaskState.SUCCEEDED,
        QueueTaskState.FAILED,
        QueueTaskState.CANCELED,
        QueueTaskState.POLL_TIMEOUT,
    ):
        # Do not rewrite a finished task (and its shot) into CANCELED.
        return _serialize_task(task)
    task.state = QueueTaskState.CANCELED
    task.finished_at = datetime.now(timezone.utc)
    shot = await db.get(ShotRecord, task.shot_id)
    if shot:
        shot.generation_state = ShotGenerationState.CANCELED
    task.history_log = list(task.history_log or []) + ["Canceled by operator."]
    await db.commit()
    return _serialize_task(task)


@router.get("/shots/{shot_id}/takes")
async def list_shot_takes(shot_id: str, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(ShotTakeRecord)
            .where(ShotTakeRecord.shot_id == shot_id)
            .order_by(ShotTakeRecord.take_number.asc())
        )
    ).scalars().all()
    return [
        {
            "id": t.id,
            "shot_id": t.shot_id,
            "take_number": t.take_number,
            "task_id": t.task_id,
            "asset_id": t.asset_id,
            "model_id": t.model_id,
            "prompt_hash": t.prompt_hash,
            "account_used": t.account_used,
            "first_frame_asset_id": t.first_frame_asset_id,
            "last_frame_asset_id": t.last_frame_asset_id,
            "continuity_flagged": t.continuity_flagged,
            "director_notes": t.director_notes,
            "is_selected": t.is_selected,
        }
        for t in rows
    ]


@router.post("/shots/{shot_id}/takes/{take_id}/select")
async def select_shot_take(shot_id: str, take_id: str, db: AsyncSession = Depends(get_db), actor: str = Depends(verify_single_admin)) -> dict[str, Any]:
    shot = await db.get(ShotRecord, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot không tồn tại.")

    takes = (
        await db.execute(select(ShotTakeRecord).where(ShotTakeRecord.shot_id == shot_id))
    ).scalars().all()
    found = False
    for t in takes:
        if t.id == take_id:
            t.is_selected = True
            found = True
        else:
            t.is_selected = False

    if not found:
        raise HTTPException(status_code=404, detail="Take không thuộc về Shot này.")

    selected_obj = next(t for t in takes if t.id == take_id)
    shot.selected_take_id = take_id
    if selected_obj.last_frame_asset_id:
        shot.last_frame_asset_id = selected_obj.last_frame_asset_id
    await db.commit()
    unlocked = await GenerationQueueService.propagate_frame_chain_downstream(db, shot)
    return {
        "shot_id": shot_id,
        "selected_take_id": take_id,
        "last_frame_asset_id": shot.last_frame_asset_id,
        "propagated_downstream_shots": unlocked,
    }

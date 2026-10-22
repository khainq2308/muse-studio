from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from muse_orchestrator.api.dependencies import get_studio_repository
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.api.arq_dispatch import schedule_generation_task
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import (
    ShotGenerationState,
    GenerationTaskRecord,
    QueueTaskState,
)
from muse_orchestrator.services.generation_queue import GenerationQueueService

router = APIRouter(prefix="/api/v1/queue", tags=["M4 Generation Queue & Takes"])


class EnqueueTaskIn(BaseModel):
    shot_id: str
    connection_id: str
    task_type: Literal["image", "video"] = "video"
    model_id: str = Field(default="muse-video-mock-v1")
    priority: int = Field(default=10, ge=1, le=100)
    idempotency_key: str | None = None
    force_retry: bool = False
    max_polls: int = Field(default=60, ge=1, le=500)


class QueueTaskOut(BaseModel):
    """Stable response contract for queue tasks; never expose internal prompt snapshots."""

    id: str
    project_id: str
    scene_id: str | None
    shot_id: str
    connection_id: str
    task_type: Literal["image", "video"]
    model_id: str
    idempotency_key: str
    priority: int
    state: QueueTaskState
    external_task_id: str | None
    account_used: str | None
    attempt_count: int
    max_attempts: int
    poll_count: int
    max_polls: int
    next_poll_delay_seconds: float
    error_category: str | None
    retryable: bool
    error_message_redacted: str | None
    result_asset_id: str | None
    take_id: str | None
    history_log: list[str]


class EnqueuedQueueTaskOut(QueueTaskOut):
    created_new: bool


class QueueTaskSummaryOut(BaseModel):
    total_tasks: int
    active_tasks: int
    failed_tasks: int


class QueueTaskPageOut(BaseModel):
    items: list[QueueTaskOut]
    total: int
    limit: int
    offset: int
    has_more: bool
    summary: QueueTaskSummaryOut


class ShotTakeOut(BaseModel):
    id: str
    shot_id: str
    take_number: int
    task_id: str
    asset_id: str
    model_id: str
    prompt_hash: str
    account_used: str | None
    first_frame_asset_id: str | None
    last_frame_asset_id: str | None
    continuity_flagged: bool
    director_notes: str
    is_selected: bool


class ShotTakeSelectionOut(BaseModel):
    shot_id: str
    selected_take_id: str
    last_frame_asset_id: str | None
    propagated_downstream_shots: list[str]


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


@router.post("/tasks", response_model=EnqueuedQueueTaskOut, status_code=status.HTTP_201_CREATED)
async def enqueue_generation_task(
    payload: EnqueueTaskIn,
    request: Request,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> EnqueuedQueueTaskOut:
    shot = await repository.get_shot(payload.shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot không tồn tại.")

    conn = await repository.get_connection(payload.connection_id)
    if not conn or not conn.enabled:
        raise HTTPException(status_code=400, detail="Connection không tồn tại hoặc đang bị vô hiệu hoá.")

    try:
        task, created = await GenerationQueueService.enqueue_shot_generation(
            db=repository.session,
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
        await repository.save_queue_task(task, refresh=True)

    await schedule_generation_task(request, task)

    out = _serialize_task(task)
    out["created_new"] = created
    return out


@router.get("/tasks", response_model=QueueTaskPageOut)
async def list_queue_tasks(
    project_id: str | None = None,
    connection_id: str | None = None,
    state: QueueTaskState | None = None,
    model_id: str | None = None,
    account_used: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> QueueTaskPageOut:
    rows = await repository.list_queue_tasks(
        project_id=project_id,
        connection_id=connection_id,
        state=state,
        model_id=model_id,
        account_used=account_used,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_queue_tasks(
        project_id=project_id,
        connection_id=connection_id,
        state=state,
        model_id=model_id,
        account_used=account_used,
    )
    summary = await repository.summarize_queue_tasks(project_id=project_id)
    return {
        "items": [_serialize_task(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "summary": summary,
    }


@router.post("/tasks/{task_id}/step", response_model=QueueTaskOut)
async def step_generation_task(
    task_id: str,
    request: Request,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> QueueTaskOut:
    try:
        task = await GenerationQueueService.step_task(repository.session, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await schedule_generation_task(request, task)
    return _serialize_task(task)


@router.post("/tasks/{task_id}/pause", response_model=QueueTaskOut)
async def pause_task(task_id: str, repository: StudioRepository = Depends(get_studio_repository), actor: str = Depends(verify_single_admin)) -> QueueTaskOut:
    task = await repository.get_generation_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task không tồn tại.")
    if task.state not in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot pause a task in state '{task.state.value}'.",
        )
    task.state = QueueTaskState.PAUSED
    task.history_log = list(task.history_log or []) + ["Paused by operator."]
    await repository.save_queue_task(task)
    return _serialize_task(task)


@router.post("/tasks/{task_id}/resume", response_model=QueueTaskOut)
async def resume_task(task_id: str, request: Request, repository: StudioRepository = Depends(get_studio_repository), actor: str = Depends(verify_single_admin)) -> QueueTaskOut:
    task = await repository.get_generation_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task không tồn tại.")
    if task.state != QueueTaskState.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot resume a task in state '{task.state.value}'.",
        )
    task.state = QueueTaskState.RUNNING if task.external_task_id else QueueTaskState.QUEUED
    task.history_log = list(task.history_log or []) + ["Resumed by operator."]
    await repository.save_queue_task(task)
    await schedule_generation_task(request, task)
    return _serialize_task(task)


@router.post("/tasks/{task_id}/cancel", response_model=QueueTaskOut)
async def cancel_task(task_id: str, repository: StudioRepository = Depends(get_studio_repository), actor: str = Depends(verify_single_admin)) -> QueueTaskOut:
    task = await repository.get_generation_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task không tồn tại.")
    if task.state in (
        QueueTaskState.SUCCEEDED,
        QueueTaskState.FAILED,
        QueueTaskState.CANCELED,
        QueueTaskState.POLL_TIMEOUT,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a task in terminal state '{task.state.value}'.",
        )
    task.state = QueueTaskState.CANCELED
    task.finished_at = datetime.now(timezone.utc)
    shot = await repository.get_shot(task.shot_id)
    if shot:
        shot.generation_state = ShotGenerationState.CANCELED
    task.history_log = list(task.history_log or []) + ["Canceled by operator."]
    await repository.save_queue_task(task, shot=shot)
    return _serialize_task(task)


@router.get("/shots/{shot_id}/takes", response_model=list[ShotTakeOut])
async def list_shot_takes(
    shot_id: str,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> list[ShotTakeOut]:
    rows = await repository.list_shot_takes(shot_id)
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


@router.post("/shots/{shot_id}/takes/{take_id}/select", response_model=ShotTakeSelectionOut)
async def select_shot_take(shot_id: str, take_id: str, repository: StudioRepository = Depends(get_studio_repository), actor: str = Depends(verify_single_admin)) -> ShotTakeSelectionOut:
    shot = await repository.get_shot(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot không tồn tại.")

    takes = await repository.list_shot_takes(shot_id)
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
    await repository.save_selected_take(shot, takes)
    unlocked = await GenerationQueueService.propagate_frame_chain_downstream(repository.session, shot)
    return {
        "shot_id": shot_id,
        "selected_take_id": take_id,
        "last_frame_asset_id": shot.last_frame_asset_id,
        "propagated_downstream_shots": unlocked,
    }

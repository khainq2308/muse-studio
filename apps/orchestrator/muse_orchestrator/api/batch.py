from __future__ import annotations
import uuid
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.api.dependencies import get_studio_repository
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import (
    ShotGenerationState,
    GenerationTaskRecord,
    QueueTaskState,
    PROVIDER_SLOT_TASK_STATES,
    SCHEDULABLE_GENERATION_TASK_STATES,
    ShotTakeRecord,
    BatchRenderJobRecord,
)
from muse_orchestrator.api.pipeline import _recompile_project_shots
from muse_orchestrator.api.arq_dispatch import schedule_generation_task, schedule_generation_tasks
from muse_orchestrator.services.generation_queue import (
    GenerationQueueService,
    conditioning_first_frame_of,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["M5 Batch Render & Continuity QA"])


class BatchLaunchIn(BaseModel):
    connection_id: str
    scene_id: str | None = None
    shot_ids: list[str] | None = None
    model_id: str = Field(default="muse-video-mock-v1", min_length=1, max_length=128)
    only_changed: bool = Field(default=True)
    priority_shot_ids: list[str] = Field(default_factory=list)


class ContinuityFlagIn(BaseModel):
    continuity_flagged: bool = True
    director_notes: str = Field(default="Continuity mismatch detected during review.")
    regenerate_now: bool = False
    connection_id: str | None = None
    model_id: str = Field(default="muse-video-mock-v1", min_length=1, max_length=128)


class BatchWarningOut(BaseModel):
    code: Literal["frame_chain_continuity_not_guaranteed"]
    message: str
    shot_ids: list[str]


class BatchLaunchOut(BaseModel):
    batch_id: str
    total_shots: int
    enqueued_count: int
    skipped_unchanged_count: int
    blocked_count: int
    enqueued_task_ids: list[str]
    skipped_shot_ids: list[str]
    blocked_shot_ids: list[str]
    warnings: list[BatchWarningOut] = Field(default_factory=list)


def _frame_chain_warnings(conn: Any, shots: list[Any], skipped: list[str]) -> list[dict[str, Any]]:
    """Warn when frame-chained shots will render on a connection that has not shown
    real first-frame conditioning; such gateways may silently ignore the frame."""
    status_value = (conn.capabilities_snapshot or {}).get("video_first_frame", "unknown")
    if status_value == "supported":
        return []
    chained = [
        s.id for s in shots
        if s.continuity_mode == "frame_chain" and s.previous_shot_id and s.id not in skipped
    ]
    if not chained:
        return []
    return [{
        "code": "frame_chain_continuity_not_guaranteed",
        "message": (
            f"Connection '{conn.name}' first-frame conditioning is '{status_value}': frame-chained "
            "shots will render, but the video may not start from the previous shot's last frame. "
            "Run a connection test against a gateway that advertises video_first_frame support."
        ),
        "shot_ids": chained,
    }]


class BatchTickStepOut(BaseModel):
    task_id: str
    shot_id: str
    state: QueueTaskState
    poll_count: int | None = None
    auto_enqueued_from_frame_chain: bool | None = None


class BatchTickOut(BaseModel):
    project_id: str
    stepped_count: int
    stepped_tasks: list[BatchTickStepOut]
    queue_summary: dict[str, int]
    tick_limit: int
    active_tasks_fetched: int


class RetryFailedTasksOut(BaseModel):
    retried_count: int
    new_task_ids: list[str]


class ContinuityFlagOut(BaseModel):
    shot_id: str
    take_id: str
    continuity_flagged: bool
    director_notes: str
    regenerated_task_id: str | None


class TakeComparisonOut(BaseModel):
    take_id: str
    take_number: int
    asset_id: str
    first_frame_asset_id: str | None
    last_frame_asset_id: str | None
    model_id: str
    prompt_hash: str
    account_used: str | None
    continuity_flagged: bool
    director_notes: str
    is_selected: bool


class CompareShotTakesOut(BaseModel):
    shot_id: str
    same_prompt_hash: bool
    same_model: bool
    take_a: TakeComparisonOut
    take_b: TakeComparisonOut


@router.post("/batch/launch", response_model=BatchLaunchOut, status_code=status.HTTP_201_CREATED)
async def launch_batch_render(
    project_id: str,
    payload: BatchLaunchIn,
    request: Request,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> BatchLaunchOut:
    """
    Fan-out N shots thành các task bất đồng bộ (Mục 3.7, M5 & Tiêu chí nghiệm thu #6, #10 PRD):
    - Tự động chạy Continuity Linter trước khi đưa vào queue.
    - Không gửi request cho các shot bị BLOCKED_REFERENCE.
    - Tạm khoá các shot có continuity_mode == 'frame_chain' nếu shot phía trước chưa hoàn tất.
    - Nếu only_changed=True: Không render lại những shot đã succeeded và không đổi prompt_hash.
    """
    proj = await repository.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

    # Validate the scene before recompiling prompts or creating a batch job.
    if payload.scene_id is not None:
        scene = await repository.get_scene(payload.scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="Scene không tồn tại.")
        if scene.project_id != project_id:
            raise HTTPException(status_code=422, detail="Scene không thuộc project đang thao tác.")

    conn = await repository.get_connection(payload.connection_id)
    if not conn or not conn.enabled:
        raise HTTPException(status_code=400, detail="Connection không tồn tại hoặc đang bị tắt.")

    # Biên dịch lại toàn bộ prompt_hash và lint_report_json mới nhất
    all_shots = await _recompile_project_shots(project_id, repository)

    target_shots = [
        s for s in all_shots
        if (payload.scene_id is None or s.scene_id == payload.scene_id)
        and (payload.shot_ids is None or s.id in payload.shot_ids)
    ]

    enqueued_task_ids: list[str] = []
    tasks_to_schedule: list[GenerationTaskRecord] = []
    skipped_shot_ids: list[str] = []
    blocked_shot_ids: list[str] = []

    for s in target_shots:
        # 1. Smart cache: skip only when the selected take matches the current prompt_hash,
        #    the requested model and the conditioning frame the shared resolver yields now.
        if payload.only_changed and s.generation_state == ShotGenerationState.SUCCEEDED and s.selected_take_id:
            sel_take = await repository.get_shot_take(s.selected_take_id)
            if (
                sel_take is not None
                and sel_take.prompt_hash == s.prompt_hash
                and sel_take.model_id == payload.model_id
            ):
                conditioning = await GenerationQueueService.resolve_conditioning_frame(
                    repository.session, s
                )
                sel_task = await repository.get_generation_task(sel_take.task_id)
                if (
                    not conditioning.missing
                    and sel_task is not None
                    and sel_task.connection_id == conn.id
                    and conditioning_first_frame_of(sel_task) == conditioning.asset_id
                ):
                    skipped_shot_ids.append(s.id)
                    continue

        # 2. Shared guard (lint blockers, BLOCKED_REFERENCE, upstream frame) + one active
        #    task per shot are enforced inside enqueue_shot_generation.
        prio = 50 if s.id in payload.priority_shot_ids else max(1, 30 - s.sequence_index)
        try:
            task, _created = await GenerationQueueService.enqueue_shot_generation(
                db=repository.session,
                shot=s,
                conn=conn,
                task_type="video",
                model_id=payload.model_id,
                priority=prio,
                force_retry=not payload.only_changed,
            )
        except ValueError:
            blocked_shot_ids.append(s.id)
            continue
        if task.state not in SCHEDULABLE_GENERATION_TASK_STATES:
            # An existing SUCCEEDED/FAILED/CANCELED/POLL_TIMEOUT/PAUSED task for the same
            # inputs: nothing was enqueued (use retry-failed / only_changed=false / resume).
            skipped_shot_ids.append(s.id)
            continue
        enqueued_task_ids.append(task.id)
        tasks_to_schedule.append(task)

    batch_id = f"bat_{uuid.uuid4().hex[:10]}"
    batch_job = BatchRenderJobRecord(
        id=batch_id,
        project_id=project_id,
        scene_id=payload.scene_id,
        connection_id=conn.id,
        model_id=payload.model_id,
        status="running" if enqueued_task_ids else "completed",
        total_shots=len(target_shots),
        enqueued_count=len(enqueued_task_ids),
        skipped_unchanged_count=len(skipped_shot_ids),
        blocked_count=len(blocked_shot_ids),
        task_ids_json=enqueued_task_ids,
        skipped_shot_ids_json=skipped_shot_ids,
        blocked_shot_ids_json=blocked_shot_ids,
    )
    await repository.create_batch_render_job(batch_job)

    # Publish only after the database records are committed. The periodic worker
    # recovery job can re-enqueue persisted tasks if Redis is temporarily unavailable.
    # Try every task so one Redis failure does not hide the rest, and tell the client
    # which batch was persisted so it does not relaunch it.
    unscheduled_task_ids: list[str] = []
    for task in tasks_to_schedule:
        try:
            await schedule_generation_task(request, task)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_503_SERVICE_UNAVAILABLE:
                raise
            unscheduled_task_ids.append(task.id)
    if unscheduled_task_ids:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "queue_dispatch_unavailable",
                "batch_id": batch_id,
                "task_ids": unscheduled_task_ids,
                "message": "Batch and tasks are saved, but some tasks could not be scheduled. "
                "Background recovery will retry after Redis is reachable; do not relaunch the batch.",
            },
        )

    return {
        "batch_id": batch_id,
        "total_shots": len(target_shots),
        "enqueued_count": len(enqueued_task_ids),
        "skipped_unchanged_count": len(skipped_shot_ids),
        "blocked_count": len(blocked_shot_ids),
        "enqueued_task_ids": enqueued_task_ids,
        "skipped_shot_ids": skipped_shot_ids,
        "blocked_shot_ids": blocked_shot_ids,
        "warnings": _frame_chain_warnings(conn, target_shots, skipped_shot_ids),
    }


async def _resolve_frame_chain_generation_config(
    repository: StudioRepository,
    project_id: str,
    shot: Any,
    requested_connection_id: str | None = None,
) -> tuple[str, str] | None:
    """Backward-compatible alias; the rule lives in the service layer."""
    return await GenerationQueueService.resolve_frame_chain_generation_config(
        repository, project_id, shot, requested_connection_id=requested_connection_id
    )


@router.post("/batch/tick", response_model=BatchTickOut)
async def tick_project_batch_queue(
    project_id: str,
    request: Request,
    connection_id: str | None = None,
    max_steps: int = Query(default=50, ge=1, le=200),
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> BatchTickOut:
    """
    Thực thi 1 chu kỳ điều phối (scheduler tick) trên toàn bộ hàng đợi của Project:
    - Tôn trọng tuyệt đối max_concurrency của từng Connection.
    - Lỗi ở 1 shot không làm ảnh hưởng đến tiến độ của các shot khác.
    - Khi 1 shot hoàn tất và mở khoá Frame Chain cho shot kế tiếp, tự động enqueue shot kế tiếp.
    - Mỗi tick chỉ bước tối đa `max_steps` task (mặc định 50) để một request không
      làm I/O provider không giới hạn; phần còn lại để tick sau xử lý.
    """
    if not await repository.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project không tồn tại.")
    active_tasks = await repository.list_active_generation_tasks(
        project_id, connection_id, limit=max_steps
    )

    # Ưu tiên bước Poll cho các task đang RUNNING trước, sau đó mới Dispatch các task QUEUED theo slot trống
    running_tasks = [t for t in active_tasks if t.state in PROVIDER_SLOT_TASK_STATES]
    queued_tasks = [t for t in active_tasks if t.state == QueueTaskState.QUEUED]

    stepped_results: list[dict[str, Any]] = []
    to_schedule: list[GenerationTaskRecord] = []
    for t in running_tasks + queued_tasks:
        updated = await GenerationQueueService.step_task(repository.session, t.id)
        to_schedule.append(updated)
        stepped_results.append({
            "task_id": updated.id,
            "shot_id": updated.shot_id,
            "state": updated.state.value,
            "poll_count": updated.poll_count,
        })

    # Kiểm tra xem có shot nào vừa được mở khoá khỏi BLOCKED_UPSTREAM_FRAME nhờ Frame Chaining không
    # Nếu có và vừa được chuyển về NOT_STARTED (có first_frame_asset_id), tự động enqueue vào cùng Connection!
    # Shots without a valid config/enabled connection stay NOT_STARTED for a later tick.
    auto_results = await GenerationQueueService.auto_enqueue_ready_frame_chain_shots(
        repository, project_id, requested_connection_id=connection_id
    )
    for cs, new_t, created in auto_results:
        to_schedule.append(new_t)
        if created:
            stepped_results.append({
                "task_id": new_t.id,
                "shot_id": cs.id,
                "state": new_t.state.value,
                "auto_enqueued_from_frame_chain": True,
            })

    # Every step and auto-enqueue is committed; publish them all before reporting a 503.
    await schedule_generation_tasks(request, to_schedule)

    all_project_tasks = await repository.list_project_generation_tasks(project_id)
    counts: dict[str, int] = {}
    for pt in all_project_tasks:
        counts[pt.state.value] = counts.get(pt.state.value, 0) + 1

    return {
        "project_id": project_id,
        "stepped_count": len(stepped_results),
        "stepped_tasks": stepped_results,
        "queue_summary": counts,
        "tick_limit": max_steps,
        "active_tasks_fetched": len(active_tasks),
    }


@router.post("/batch/retry-failed", response_model=RetryFailedTasksOut)
async def retry_failed_project_tasks(
    project_id: str,
    request: Request,
    connection_id: str | None = None,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> RetryFailedTasksOut:
    """Chỉ retry các Shot bị lỗi (failed / poll_timeout), giữ nguyên thành quả các Shot đã succeeded.

    Everything is validated before the first task is created so a bad shot or connection
    never leaves a partially committed retry behind (400 disabled/missing connection,
    422 for shots that cannot be enqueued). Shots whose previous connection is now
    disabled/missing are skipped when no explicit ``connection_id`` is given.
    """
    if not await repository.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project không tồn tại.")
    explicit_conn = None
    if connection_id is not None:
        explicit_conn = await repository.get_connection(connection_id)
        if explicit_conn is None or not explicit_conn.enabled:
            raise HTTPException(status_code=400, detail="Connection không tồn tại hoặc đang bị tắt.")

    failed_shots = await repository.list_failed_project_shots(project_id)
    if not failed_shots:
        return {"retried_count": 0, "new_task_ids": []}

    plan: list[tuple[Any, Any, str]] = []
    precondition_errors: list[dict[str, str]] = []
    for s in failed_shots:
        last_task = await repository.get_latest_generation_task_for_shot(s.id)
        conn = explicit_conn
        if conn is None:
            if last_task is None:
                continue
            conn = await repository.get_connection(last_task.connection_id)
            if conn is None or not conn.enabled:
                continue
        error = await GenerationQueueService.check_enqueue_preconditions(repository.session, s, conn)
        if error is not None:
            precondition_errors.append({"shot_id": s.id, "message": error})
            continue
        model_id = last_task.model_id if last_task else "muse-video-mock-v1"
        plan.append((s, conn, model_id))

    if precondition_errors:
        raise HTTPException(
            status_code=422,
            detail={"code": "retry_precondition_failed", "shots": precondition_errors},
        )

    new_task_ids: list[str] = []
    created_tasks: list[GenerationTaskRecord] = []
    for s, conn, model_id in plan:
        try:
            t, _ = await GenerationQueueService.enqueue_shot_generation(
                db=repository.session,
                shot=s,
                conn=conn,
                task_type="video",
                model_id=model_id,
                force_retry=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        new_task_ids.append(t.id)
        created_tasks.append(t)

    # Publish only after every retry task exists, so a Redis failure cannot skip shots.
    await schedule_generation_tasks(request, created_tasks)
    return {"retried_count": len(new_task_ids), "new_task_ids": new_task_ids}


@router.post("/shots/{shot_id}/takes/{take_id}/flag-continuity", response_model=ContinuityFlagOut)
async def flag_take_continuity_and_regenerate(
    project_id: str,
    shot_id: str,
    take_id: str,
    payload: ContinuityFlagIn,
    request: Request,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> ContinuityFlagOut:
    """Đánh dấu lỗi Continuity trên một Take và tuỳ chọn kích hoạt Regenerate có mục tiêu (Mục 1 & 3.8 PRD)."""
    take = await repository.get_shot_take(take_id)
    shot = await repository.get_shot(shot_id)
    if not take or not shot or take.shot_id != shot_id or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="Shot hoặc Take không tồn tại.")

    # Validate the regenerate request before persisting anything.
    conn = None
    if payload.regenerate_now:
        orig_task = await repository.get_generation_task(take.task_id)
        conn_id = payload.connection_id or (orig_task.connection_id if orig_task else None)
        if not conn_id:
            raise HTTPException(status_code=400, detail="Cần cung cấp connection_id để regenerate.")
        conn = await repository.get_connection(conn_id)
        if not conn:
            raise HTTPException(status_code=404, detail="Connection không tồn tại.")
        if not conn.enabled:
            raise HTTPException(status_code=400, detail="Connection đang bị tắt.")
        error = await GenerationQueueService.check_enqueue_preconditions(repository.session, shot, conn)
        if error is not None:
            raise HTTPException(status_code=422, detail=error)

    previous_flag = (take.continuity_flagged, take.director_notes)
    take.continuity_flagged = payload.continuity_flagged
    take.director_notes = payload.director_notes
    await repository.save_take_continuity(take)

    regen_task_id: str | None = None
    if conn is not None:
        try:
            new_task, _ = await GenerationQueueService.enqueue_shot_generation(
                db=repository.session,
                shot=shot,
                conn=conn,
                task_type="video",
                model_id=payload.model_id or take.model_id,
                priority=80,
                force_retry=True,
            )
        except ValueError as exc:
            # The guard refused after the pre-check (state changed meanwhile): undo the flag
            # so a 422 never leaves half of the request applied.
            take = await repository.get_shot_take(take_id)
            if take is not None:
                take.continuity_flagged, take.director_notes = previous_flag
                await repository.save_take_continuity(take)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        regen_task_id = new_task.id
        await schedule_generation_task(request, new_task)

    return {
        "shot_id": shot_id,
        "take_id": take_id,
        "continuity_flagged": take.continuity_flagged,
        "director_notes": take.director_notes,
        "regenerated_task_id": regen_task_id,
    }


@router.get("/shots/{shot_id}/compare-takes", response_model=CompareShotTakesOut)
async def compare_shot_takes_ab(
    project_id: str,
    shot_id: str,
    take_a_id: str,
    take_b_id: str,
    repository: StudioRepository = Depends(get_studio_repository),
) -> CompareShotTakesOut:
    """So sánh A/B giữa 2 Takes của cùng một Shot (Mục 3.8 PRD)."""
    ta = await repository.get_shot_take(take_a_id)
    tb = await repository.get_shot_take(take_b_id)
    if (
        not ta
        or not tb
        or ta.project_id != project_id
        or tb.project_id != project_id
        or ta.shot_id != shot_id
        or tb.shot_id != shot_id
    ):
        raise HTTPException(status_code=404, detail="Một trong hai Takes không thuộc về Shot này.")

    def _info(t: ShotTakeRecord) -> dict[str, Any]:
        return {
            "take_id": t.id,
            "take_number": t.take_number,
            "asset_id": t.asset_id,
            "first_frame_asset_id": t.first_frame_asset_id,
            "last_frame_asset_id": t.last_frame_asset_id,
            "model_id": t.model_id,
            "prompt_hash": t.prompt_hash,
            "account_used": t.account_used,
            "continuity_flagged": t.continuity_flagged,
            "director_notes": t.director_notes,
            "is_selected": t.is_selected,
        }

    return {
        "shot_id": shot_id,
        "same_prompt_hash": ta.prompt_hash == tb.prompt_hash,
        "same_model": ta.model_id == tb.model_id,
        "take_a": _info(ta),
        "take_b": _info(tb),
    }

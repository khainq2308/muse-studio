from __future__ import annotations
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.api.dependencies import get_studio_repository
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import (
    ShotGenerationState,
    GenerationTaskRecord,
    QueueTaskState,
    ShotTakeRecord,
    BatchRenderJobRecord,
    SceneRecord,
)
from muse_orchestrator.api.pipeline import _recompile_project_shots
from muse_orchestrator.api.arq_dispatch import schedule_generation_task
from muse_orchestrator.services.generation_queue import GenerationQueueService

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["M5 Batch Render & Continuity QA"])


class BatchLaunchIn(BaseModel):
    connection_id: str
    scene_id: str | None = None
    shot_ids: list[str] | None = None
    model_id: str = Field(default="muse-video-mock-v1")
    only_changed: bool = Field(default=True)
    priority_shot_ids: list[str] = Field(default_factory=list)


class ContinuityFlagIn(BaseModel):
    continuity_flagged: bool = True
    director_notes: str = Field(default="Continuity mismatch detected during review.")
    regenerate_now: bool = False
    connection_id: str | None = None
    model_id: str = Field(default="muse-video-mock-v1")


class BatchLaunchOut(BaseModel):
    batch_id: str
    total_shots: int
    enqueued_count: int
    skipped_unchanged_count: int
    blocked_count: int
    enqueued_task_ids: list[str]
    skipped_shot_ids: list[str]
    blocked_shot_ids: list[str]


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
        scene = await repository.session.get(SceneRecord, payload.scene_id)
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
        # 1. Kiểm tra Smart Cache: Nếu shot đã có selected_take khớp với prompt_hash hiện tại -> Bỏ qua!
        if payload.only_changed and s.generation_state == ShotGenerationState.SUCCEEDED and s.selected_take_id:
            sel_take = await repository.get_shot_take(s.selected_take_id)
            if sel_take and sel_take.prompt_hash == s.prompt_hash:
                skipped_shot_ids.append(s.id)
                continue

        # 2. Kiểm tra Blocker từ Prompt Linter (VD: BLOCKED_REFERENCE, CAMERA_MOTION_CONFLICT)
        has_blocker = any(i.get("severity") == "blocker" for i in (s.lint_report_json or []))
        if has_blocker or s.generation_state == ShotGenerationState.BLOCKED_REFERENCE:
            s.generation_state = ShotGenerationState.BLOCKED_REFERENCE
            blocked_shot_ids.append(s.id)
            continue

        # 3. Kiểm tra phụ thuộc Frame Chaining
        if s.continuity_mode == "frame_chain" and s.previous_shot_id:
            prev_shot = await repository.get_shot(s.previous_shot_id)
            if prev_shot and prev_shot.last_frame_asset_id and prev_shot.generation_state == ShotGenerationState.SUCCEEDED:
                s.first_frame_asset_id = prev_shot.last_frame_asset_id
            else:
                s.generation_state = ShotGenerationState.BLOCKED_UPSTREAM_FRAME
                blocked_shot_ids.append(s.id)
                continue

        prio = 50 if s.id in payload.priority_shot_ids else max(1, 30 - s.sequence_index)
        task, _ = await GenerationQueueService.enqueue_shot_generation(
            db=repository.session,
            shot=s,
            conn=conn,
            task_type="video",
            model_id=payload.model_id,
            priority=prio,
            force_retry=not payload.only_changed,
        )
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
    for task in tasks_to_schedule:
        await schedule_generation_task(request, task)

    return {
        "batch_id": batch_id,
        "total_shots": len(target_shots),
        "enqueued_count": len(enqueued_task_ids),
        "skipped_unchanged_count": len(skipped_shot_ids),
        "blocked_count": len(blocked_shot_ids),
        "enqueued_task_ids": enqueued_task_ids,
        "skipped_shot_ids": skipped_shot_ids,
        "blocked_shot_ids": blocked_shot_ids,
    }


async def _resolve_frame_chain_generation_config(
    repository: StudioRepository,
    project_id: str,
    shot: Any,
    requested_connection_id: str | None = None,
) -> tuple[str, str] | None:
    """Resolve frame-chain config from the take that owns the upstream frame.

    Failed/canceled regenerations must not replace the config for a successful
    upstream take. An explicit connection override can reuse the upstream model
    when the target connection has no history yet.
    """
    upstream_task = None
    previous_shot_id = getattr(shot, "previous_shot_id", None)
    if previous_shot_id:
        upstream_shot = await repository.get_shot(previous_shot_id)
        if upstream_shot is not None and upstream_shot.project_id == project_id:
            # The selected take is authoritative because its last frame is what
            # is propagated to the downstream shot.
            selected_take_id = getattr(upstream_shot, "selected_take_id", None)
            if selected_take_id:
                selected_take = await repository.get_shot_take(selected_take_id)
                if (
                    selected_take is not None
                    and selected_take.project_id == project_id
                    and selected_take.shot_id == previous_shot_id
                ):
                    candidate = await repository.get_generation_task(selected_take.task_id)
                    if (
                        candidate is not None
                        and candidate.project_id == project_id
                        and candidate.shot_id == previous_shot_id
                        and candidate.state == QueueTaskState.SUCCEEDED
                    ):
                        upstream_task = candidate

            if upstream_task is None:
                upstream_task = await repository.get_latest_successful_generation_task_for_shot(
                    previous_shot_id
                )
                if (
                    upstream_task is not None
                    and (
                        upstream_task.project_id != project_id
                        or upstream_task.shot_id != previous_shot_id
                        or upstream_task.state != QueueTaskState.SUCCEEDED
                    )
                ):
                    upstream_task = None

    if requested_connection_id is None and upstream_task is not None:
        return upstream_task.connection_id, upstream_task.model_id
    if (
        requested_connection_id is not None
        and upstream_task is not None
        and upstream_task.connection_id == requested_connection_id
    ):
        return requested_connection_id, upstream_task.model_id

    # Repository results are ordered oldest-to-newest. Failed/canceled tasks
    # are never valid config sources. This fallback also supports a first shot
    # with a manual first frame when the project has a successful prior config.
    project_tasks = await repository.list_project_generation_tasks(project_id)
    successful_tasks = [
        task
        for task in project_tasks
        if task.project_id == project_id and task.state == QueueTaskState.SUCCEEDED
    ]
    if requested_connection_id is None:
        if successful_tasks:
            latest = successful_tasks[-1]
            return latest.connection_id, latest.model_id
        return None

    matching_tasks = [
        task for task in successful_tasks
        if task.connection_id == requested_connection_id
    ]
    if matching_tasks:
        return requested_connection_id, matching_tasks[-1].model_id

    # The operator explicitly selected a new connection. Reuse the successful
    # upstream take's model instead of leaving the shot stuck due to no history
    # on this connection or choosing a failed/unrelated task's model.
    if upstream_task is not None:
        return requested_connection_id, upstream_task.model_id
    if successful_tasks:
        return requested_connection_id, successful_tasks[-1].model_id
    return None


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
    active_tasks = await repository.list_active_generation_tasks(
        project_id, connection_id, limit=max_steps
    )

    # Ưu tiên bước Poll cho các task đang RUNNING trước, sau đó mới Dispatch các task QUEUED theo slot trống
    running_tasks = [t for t in active_tasks if t.state == QueueTaskState.RUNNING]
    queued_tasks = [t for t in active_tasks if t.state == QueueTaskState.QUEUED]

    stepped_results: list[dict[str, Any]] = []
    for t in running_tasks + queued_tasks:
        updated = await GenerationQueueService.step_task(repository.session, t.id)
        await schedule_generation_task(request, updated)
        stepped_results.append({
            "task_id": updated.id,
            "shot_id": updated.shot_id,
            "state": updated.state.value,
            "poll_count": updated.poll_count,
        })

    # Kiểm tra xem có shot nào vừa được mở khoá khỏi BLOCKED_UPSTREAM_FRAME nhờ Frame Chaining không
    # Nếu có và vừa được chuyển về NOT_STARTED (có first_frame_asset_id), tự động enqueue vào cùng Connection!
    ready_chained_shots = await repository.list_ready_frame_chain_shots(project_id)
    for cs in ready_chained_shots:
        config = await _resolve_frame_chain_generation_config(
            repository, project_id, cs, requested_connection_id=connection_id
        )
        if config is None:
            # Leave the shot NOT_STARTED rather than dispatching it with an arbitrary
            # historical model or the mock provider. A later tick with valid config can retry.
            continue
        resolved_connection_id, resolved_model_id = config
        conn = await repository.get_connection(resolved_connection_id)
        if conn is None or not conn.enabled:
            continue
        new_t, created = await GenerationQueueService.enqueue_shot_generation(
            db=repository.session,
            shot=cs,
            conn=conn,
            task_type="video",
            model_id=resolved_model_id,
            priority=max(1, 30 - cs.sequence_index),
        )
        await schedule_generation_task(request, new_t)
        if created:
            stepped_results.append({
                "task_id": new_t.id,
                "shot_id": cs.id,
                "state": new_t.state.value,
                "auto_enqueued_from_frame_chain": True,
            })

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
    """Chỉ retry các Shot bị lỗi (failed / poll_timeout), giữ nguyên thành quả các Shot đã succeeded."""
    failed_shots = await repository.list_failed_project_shots(project_id)

    if not failed_shots:
        return {"retried_count": 0, "new_task_ids": []}

    new_task_ids: list[str] = []
    for s in failed_shots:
        last_task = await repository.get_latest_generation_task_for_shot(s.id)
        target_conn_id = connection_id or (last_task.connection_id if last_task else None)
        if not target_conn_id:
            continue
        conn = await repository.get_connection(target_conn_id)
        if not conn:
            continue
        model_id = last_task.model_id if last_task else "muse-video-mock-v1"
        t, _ = await GenerationQueueService.enqueue_shot_generation(
            db=repository.session,
            shot=s,
            conn=conn,
            task_type="video",
            model_id=model_id,
            force_retry=True,
        )
        new_task_ids.append(t.id)
        await schedule_generation_task(request, t)

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

    take.continuity_flagged = payload.continuity_flagged
    take.director_notes = payload.director_notes
    await repository.save_take_continuity(take)

    regen_task_id: str | None = None
    if payload.regenerate_now:
        orig_task = await repository.get_generation_task(take.task_id)
        conn_id = payload.connection_id or (orig_task.connection_id if orig_task else None)
        if not conn_id:
            raise HTTPException(status_code=400, detail="Cần cung cấp connection_id để regenerate.")
        conn = await repository.get_connection(conn_id)
        if not conn:
            raise HTTPException(status_code=404, detail="Connection không tồn tại.")
        new_task, _ = await GenerationQueueService.enqueue_shot_generation(
            db=repository.session,
            shot=shot,
            conn=conn,
            task_type="video",
            model_id=payload.model_id or take.model_id,
            priority=80,
            force_retry=True,
        )
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

from __future__ import annotations
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.database import get_db
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import (
    Project, ShotRecord, ShotGenerationState, ProviderConnection,
    GenerationTaskRecord, QueueTaskState, ShotTakeRecord, BatchRenderJobRecord
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


@router.post("/batch/launch", status_code=status.HTTP_201_CREATED)
async def launch_batch_render(
    project_id: str,
    payload: BatchLaunchIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """
    Fan-out N shots thành các task bất đồng bộ (Mục 3.7, M5 & Tiêu chí nghiệm thu #6, #10 PRD):
    - Tự động chạy Continuity Linter trước khi đưa vào queue.
    - Không gửi request cho các shot bị BLOCKED_REFERENCE.
    - Tạm khoá các shot có continuity_mode == 'frame_chain' nếu shot phía trước chưa hoàn tất.
    - Nếu only_changed=True: Không render lại những shot đã succeeded và không đổi prompt_hash.
    """
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

    conn = await db.get(ProviderConnection, payload.connection_id)
    if not conn or not conn.enabled:
        raise HTTPException(status_code=400, detail="Connection không tồn tại hoặc đang bị tắt.")

    # Biên dịch lại toàn bộ prompt_hash và lint_report_json mới nhất
    all_shots = await _recompile_project_shots(project_id, StudioRepository(db))

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
            sel_take = await db.get(ShotTakeRecord, s.selected_take_id)
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
            prev_shot = await db.get(ShotRecord, s.previous_shot_id)
            if prev_shot and prev_shot.last_frame_asset_id and prev_shot.generation_state == ShotGenerationState.SUCCEEDED:
                s.first_frame_asset_id = prev_shot.last_frame_asset_id
            else:
                s.generation_state = ShotGenerationState.BLOCKED_UPSTREAM_FRAME
                blocked_shot_ids.append(s.id)
                continue

        prio = 50 if s.id in payload.priority_shot_ids else max(1, 30 - s.sequence_index)
        task, _ = await GenerationQueueService.enqueue_shot_generation(
            db=db,
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
    db.add(batch_job)
    await db.commit()

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


@router.post("/batch/tick")
async def tick_project_batch_queue(
    project_id: str,
    request: Request,
    connection_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """
    Thực thi 1 chu kỳ điều phối (scheduler tick) trên toàn bộ hàng đợi của Project:
    - Tôn trọng tuyệt đối max_concurrency của từng Connection.
    - Lỗi ở 1 shot không làm ảnh hưởng đến tiến độ của các shot khác.
    - Khi 1 shot hoàn tất và mở khoá Frame Chain cho shot kế tiếp, tự động enqueue shot kế tiếp.
    """
    stmt = (
        select(GenerationTaskRecord)
        .where(
            GenerationTaskRecord.project_id == project_id,
            GenerationTaskRecord.state.in_([QueueTaskState.RUNNING, QueueTaskState.QUEUED]),
        )
        .order_by(
            GenerationTaskRecord.priority.desc(),
            GenerationTaskRecord.created_at.asc(),
        )
    )
    if connection_id:
        stmt = stmt.where(GenerationTaskRecord.connection_id == connection_id)

    active_tasks = (await db.execute(stmt)).scalars().all()

    # Ưu tiên bước Poll cho các task đang RUNNING trước, sau đó mới Dispatch các task QUEUED theo slot trống
    running_tasks = [t for t in active_tasks if t.state == QueueTaskState.RUNNING]
    queued_tasks = [t for t in active_tasks if t.state == QueueTaskState.QUEUED]

    stepped_results: list[dict[str, Any]] = []
    for t in running_tasks + queued_tasks:
        updated = await GenerationQueueService.step_task(db, t.id)
        await schedule_generation_task(request, updated)
        stepped_results.append({
            "task_id": updated.id,
            "shot_id": updated.shot_id,
            "state": updated.state.value,
            "poll_count": updated.poll_count,
        })

    # Kiểm tra xem có shot nào vừa được mở khoá khỏi BLOCKED_UPSTREAM_FRAME nhờ Frame Chaining không
    # Nếu có và vừa được chuyển về NOT_STARTED (có first_frame_asset_id), tự động enqueue vào cùng Connection!
    if active_tasks:
        default_conn_id = connection_id or active_tasks[0].connection_id
        default_model_id = active_tasks[0].model_id
        conn = await db.get(ProviderConnection, default_conn_id)
        if conn:
            ready_chained_shots = (
                await db.execute(
                    select(ShotRecord).where(
                        ShotRecord.project_id == project_id,
                        ShotRecord.continuity_mode == "frame_chain",
                        ShotRecord.first_frame_asset_id.is_not(None),
                        ShotRecord.generation_state == ShotGenerationState.NOT_STARTED,
                    )
                )
            ).scalars().all()
            for cs in ready_chained_shots:
                new_t, created = await GenerationQueueService.enqueue_shot_generation(
                    db=db,
                    shot=cs,
                    conn=conn,
                    task_type="video",
                    model_id=default_model_id,
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

    all_project_tasks = (
        await db.execute(select(GenerationTaskRecord).where(GenerationTaskRecord.project_id == project_id))
    ).scalars().all()
    counts: dict[str, int] = {}
    for pt in all_project_tasks:
        counts[pt.state.value] = counts.get(pt.state.value, 0) + 1

    return {
        "project_id": project_id,
        "stepped_count": len(stepped_results),
        "stepped_tasks": stepped_results,
        "queue_summary": counts,
    }


@router.post("/batch/retry-failed")
async def retry_failed_project_tasks(
    project_id: str,
    request: Request,
    connection_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """Chỉ retry các Shot bị lỗi (failed / poll_timeout), giữ nguyên thành quả các Shot đã succeeded."""
    failed_shots = (
        await db.execute(
            select(ShotRecord).where(
                ShotRecord.project_id == project_id,
                ShotRecord.generation_state == ShotGenerationState.FAILED,
            )
        )
    ).scalars().all()

    if not failed_shots:
        return {"retried_count": 0, "new_task_ids": []}

    new_task_ids: list[str] = []
    for s in failed_shots:
        last_task = (
            await db.execute(
                select(GenerationTaskRecord)
                .where(GenerationTaskRecord.shot_id == s.id)
                .order_by(GenerationTaskRecord.created_at.desc())
            )
        ).scalars().first()
        target_conn_id = connection_id or (last_task.connection_id if last_task else None)
        if not target_conn_id:
            continue
        conn = await db.get(ProviderConnection, target_conn_id)
        if not conn:
            continue
        model_id = last_task.model_id if last_task else "muse-video-mock-v1"
        t, _ = await GenerationQueueService.enqueue_shot_generation(
            db=db,
            shot=s,
            conn=conn,
            task_type="video",
            model_id=model_id,
            force_retry=True,
        )
        new_task_ids.append(t.id)
        await schedule_generation_task(request, t)

    return {"retried_count": len(new_task_ids), "new_task_ids": new_task_ids}


@router.post("/shots/{shot_id}/takes/{take_id}/flag-continuity")
async def flag_take_continuity_and_regenerate(
    project_id: str,
    shot_id: str,
    take_id: str,
    payload: ContinuityFlagIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """Đánh dấu lỗi Continuity trên một Take và tuỳ chọn kích hoạt Regenerate có mục tiêu (Mục 1 & 3.8 PRD)."""
    take = await db.get(ShotTakeRecord, take_id)
    shot = await db.get(ShotRecord, shot_id)
    if not take or not shot or take.shot_id != shot_id or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="Shot hoặc Take không tồn tại.")

    take.continuity_flagged = payload.continuity_flagged
    take.director_notes = payload.director_notes
    await db.commit()

    regen_task_id: str | None = None
    if payload.regenerate_now:
        orig_task = await db.get(GenerationTaskRecord, take.task_id)
        conn_id = payload.connection_id or (orig_task.connection_id if orig_task else None)
        if not conn_id:
            raise HTTPException(status_code=400, detail="Cần cung cấp connection_id để regenerate.")
        conn = await db.get(ProviderConnection, conn_id)
        if not conn:
            raise HTTPException(status_code=404, detail="Connection không tồn tại.")
        new_task, _ = await GenerationQueueService.enqueue_shot_generation(
            db=db,
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


@router.get("/shots/{shot_id}/compare-takes")
async def compare_shot_takes_ab(
    project_id: str,
    shot_id: str,
    take_a_id: str,
    take_b_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """So sánh A/B giữa 2 Takes của cùng một Shot (Mục 3.8 PRD)."""
    ta = await db.get(ShotTakeRecord, take_a_id)
    tb = await db.get(ShotTakeRecord, take_b_id)
    if not ta or not tb or ta.shot_id != shot_id or tb.shot_id != shot_id:
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

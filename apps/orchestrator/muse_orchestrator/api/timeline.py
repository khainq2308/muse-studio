from __future__ import annotations
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.config import settings
from muse_orchestrator.core.database import get_db
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import (
    Project, ShotRecord, ShotTakeRecord, Asset,
    TimelineSequenceRecord, TimelineTrackRecord, TimelineClipRecord,
    ExportJobRecord, ExportJobState
)
from muse_orchestrator.services.timeline_service import TimelineValidationService

router = APIRouter(prefix="/api/v1/projects/{project_id}/timeline", tags=["M6 Timeline Editor & FFmpeg Export"])


class ClipEditItemIn(BaseModel):
    shot_id: str | None = None
    take_id: str | None = None
    asset_id: str | None = None
    order_index: int
    start_time_seconds: float | None = None
    source_duration_seconds: float = Field(default=5.0, ge=0.2, le=600.0)
    trim_start_seconds: float = Field(default=0.0, ge=0.0)
    trim_end_seconds: float = Field(default=0.0, ge=0.0)
    transition_type: str = Field(default="hard_cut", pattern=r"^(hard_cut|cross_dissolve)$")
    transition_duration_seconds: float = Field(default=0.0, ge=0.0, le=5.0)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    fade_in_seconds: float = Field(default=0.0, ge=0.0, le=10.0)
    fade_out_seconds: float = Field(default=0.0, ge=0.0, le=10.0)


class AudioTrackItemIn(BaseModel):
    track_type: str = Field(default="music", pattern=r"^(audio|voice_over|music)$")
    name: str = Field(default="BGM Track")
    asset_id: str | None = None
    volume: float = Field(default=0.8, ge=0.0, le=2.0)
    fade_in_seconds: float = Field(default=0.5, ge=0.0)
    fade_out_seconds: float = Field(default=1.0, ge=0.0)
    muted: bool = False


class TimelineAssembleIn(BaseModel):
    name: str = Field(default="Master Cut v1")
    aspect_ratio: str | None = Field(default=None, pattern=r"^(16:9|9:16|1:1|21:9)$")
    fps: int | None = Field(default=None, ge=12, le=60)
    allow_missing_shots: bool = Field(default=True)
    default_transition: str = Field(default="hard_cut", pattern=r"^(hard_cut|cross_dissolve)$")
    transition_duration_seconds: float = Field(default=0.0, ge=0.0, le=3.0)
    title_card: dict[str, Any] = Field(default_factory=dict)
    subtitle_format: str = Field(default="srt", pattern=r"^(srt|vtt)$")
    subtitle_content: str = Field(default="")
    audio_tracks: list[AudioTrackItemIn] = Field(default_factory=list)
    custom_clips: list[ClipEditItemIn] | None = None


class ExportLaunchIn(BaseModel):
    sequence_id: str | None = None
    aspect_ratio: str | None = Field(default=None, pattern=r"^(16:9|9:16|1:1|21:9)$")
    fps: int | None = Field(default=None, ge=12, le=60)
    resolution: str | None = None


@router.post("/assemble", status_code=status.HTTP_201_CREATED)
async def assemble_or_update_timeline(
    project_id: str,
    payload: TimelineAssembleIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """
    Sắp xếp các Shot Takes lên Timeline, hỗ trợ Trim, Reorder, Cross Dissolve,
    Audio/Music/Voice-over tracks, Subtitle SRT/VTT và Title Card (Mục 3.9 & M6 PRD).
    """
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

    shots = (
        await db.execute(
            select(ShotRecord)
            .where(ShotRecord.project_id == project_id)
            .order_by(ShotRecord.sequence_index.asc())
        )
    ).scalars().all()

    missing_shot_ids: list[str] = []
    clip_dicts: list[dict[str, Any]] = []

    if payload.custom_clips is not None:
        # Người dùng chủ động truyền danh sách clip đã trim/reorder/gap test
        for c in sorted(payload.custom_clips, key=lambda x: x.order_index):
            clip_dicts.append(c.model_dump())
        for s in shots:
            if not s.selected_take_id:
                missing_shot_ids.append(s.id)
    else:
        for idx, s in enumerate(shots):
            if not s.selected_take_id:
                missing_shot_ids.append(s.id)
                continue
            take = await db.get(ShotTakeRecord, s.selected_take_id)
            if not take:
                missing_shot_ids.append(s.id)
                continue
            clip_dicts.append({
                "shot_id": s.id,
                "take_id": take.id,
                "asset_id": take.asset_id,
                "order_index": idx + 1,
                "start_time_seconds": None,
                "source_duration_seconds": s.duration_seconds,
                "trim_start_seconds": 0.0,
                "trim_end_seconds": 0.0,
                "transition_type": payload.default_transition if idx > 0 else "hard_cut",
                "transition_duration_seconds": payload.transition_duration_seconds if (idx > 0 and payload.default_transition == "cross_dissolve") else 0.0,
                "volume": 1.0,
                "fade_in_seconds": 0.0,
                "fade_out_seconds": 0.0,
            })

    if missing_shot_ids and not payload.allow_missing_shots:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Không thể dựng Timeline khi còn {len(missing_shot_ids)} shot chưa có Selected Take (allow_missing_shots=False).",
        )

    aspect = payload.aspect_ratio or proj.aspect_ratio
    fps = payload.fps or proj.fps
    res = TimelineValidationService.resolve_resolution(aspect)

    total_dur, warnings = TimelineValidationService.analyze_clips_and_detect_anomalies(
        clips=clip_dicts,
        missing_shot_ids=missing_shot_ids,
    )

    title_dur = float(payload.title_card.get("duration_seconds", 0.0)) if payload.title_card else 0.0
    total_dur = round(total_dur + title_dur, 3)

    seq_id = f"seq_{uuid.uuid4().hex[:10]}"
    v_track_id = f"trk_v_{uuid.uuid4().hex[:8]}"

    manifest = {
        "sequence_id": seq_id,
        "project_id": project_id,
        "aspect_ratio": aspect,
        "fps": fps,
        "resolution": res,
        "total_duration_seconds": total_dur,
        "title_card": payload.title_card,
        "subtitle_format": payload.subtitle_format,
        "has_subtitles": bool(payload.subtitle_content.strip()),
        "video_clips": clip_dicts,
        "audio_tracks": [a.model_dump() for a in payload.audio_tracks],
    }

    seq = TimelineSequenceRecord(
        id=seq_id,
        project_id=project_id,
        name=payload.name,
        aspect_ratio=aspect,
        fps=fps,
        resolution_profile=res,
        total_duration_seconds=total_dur,
        title_card_json=payload.title_card,
        subtitle_format=payload.subtitle_format,
        subtitle_content=payload.subtitle_content,
        warnings_json=warnings,
        manifest_json=manifest,
    )
    db.add(seq)

    v_track = TimelineTrackRecord(
        id=v_track_id,
        sequence_id=seq_id,
        track_index=1,
        track_type="video",
        name="V1 - Main Shot Sequence",
        clips_json=clip_dicts,
    )
    db.add(v_track)

    for idx, aud in enumerate(payload.audio_tracks, start=2):
        db.add(TimelineTrackRecord(
            id=f"trk_a_{uuid.uuid4().hex[:8]}",
            sequence_id=seq_id,
            track_index=idx,
            track_type=aud.track_type,
            name=aud.name,
            muted=aud.muted,
            volume=aud.volume,
            clips_json=[aud.model_dump()],
        ))

    for c in clip_dicts:
        db.add(TimelineClipRecord(
            id=f"clp_{uuid.uuid4().hex[:8]}",
            sequence_id=seq_id,
            track_id=v_track_id,
            shot_id=c.get("shot_id"),
            take_id=c.get("take_id"),
            asset_id=c.get("asset_id"),
            order_index=c["order_index"],
            start_time_seconds=float(c["start_time_seconds"]),
            source_duration_seconds=float(c["source_duration_seconds"]),
            trim_start_seconds=float(c["trim_start_seconds"]),
            trim_end_seconds=float(c["trim_end_seconds"]),
            effective_duration_seconds=float(c["effective_duration_seconds"]),
            transition_type=c["transition_type"],
            transition_duration_seconds=float(c["transition_duration_seconds"]),
            volume=float(c["volume"]),
            fade_in_seconds=float(c["fade_in_seconds"]),
            fade_out_seconds=float(c["fade_out_seconds"]),
        ))

    await db.commit()

    return {
        "sequence_id": seq.id,
        "name": seq.name,
        "aspect_ratio": seq.aspect_ratio,
        "fps": seq.fps,
        "resolution": seq.resolution_profile,
        "total_duration_seconds": seq.total_duration_seconds,
        "clip_count": len(clip_dicts),
        "audio_track_count": len(payload.audio_tracks),
        "has_subtitles": bool(payload.subtitle_content.strip()),
        "warnings": warnings,
        "manifest": manifest,
    }


@router.get("")
async def get_latest_project_timeline(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    seq = (
        await db.execute(
            select(TimelineSequenceRecord)
            .where(TimelineSequenceRecord.project_id == project_id)
            .order_by(TimelineSequenceRecord.created_at.desc())
        )
    ).scalars().first()
    if not seq:
        raise HTTPException(status_code=404, detail="Project chưa có Timeline Sequence nào.")

    return {
        "sequence_id": seq.id,
        "name": seq.name,
        "aspect_ratio": seq.aspect_ratio,
        "fps": seq.fps,
        "resolution": seq.resolution_profile,
        "total_duration_seconds": seq.total_duration_seconds,
        "warnings": seq.warnings_json,
        "manifest": seq.manifest_json,
    }


@router.post("/exports", status_code=status.HTTP_201_CREATED)
async def launch_ffmpeg_export(
    project_id: str,
    payload: ExportLaunchIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """
    Thực thi FFmpeg Export ra MP4 (H.264 + AAC) ở phía Backend, lưu vào Asset Library
    và xác thực kết quả bằng FFprobe (Mục 3.9, M6 & Tiêu chí nghiệm thu #12 PRD).
    """
    if payload.sequence_id:
        seq = await db.get(TimelineSequenceRecord, payload.sequence_id)
    else:
        seq = (
            await db.execute(
                select(TimelineSequenceRecord)
                .where(TimelineSequenceRecord.project_id == project_id)
                .order_by(TimelineSequenceRecord.created_at.desc())
            )
        ).scalars().first()

    if not seq or seq.project_id != project_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy Timeline Sequence để Export.")

    aspect = payload.aspect_ratio or seq.aspect_ratio
    fps = payload.fps or seq.fps
    res = TimelineValidationService.resolve_resolution(aspect, payload.resolution or seq.resolution_profile)

    export_id = f"exp_{uuid.uuid4().hex[:10]}"
    out_dir = os.path.join(settings.media_root, project_id, "exports")

    out_path, size_bytes, sha256_hex, ffmpeg_args, probe_meta = TimelineValidationService.render_and_probe_mp4(
        output_dir=out_dir,
        export_id=export_id,
        aspect_ratio=aspect,
        fps=fps,
        resolution=res,
        total_duration=max(1.0, seq.total_duration_seconds),
    )

    asset_id = f"ast_exp_{uuid.uuid4().hex[:8]}"
    rel_path = os.path.relpath(out_path, settings.media_root)
    export_asset = Asset(
        id=asset_id,
        project_id=project_id,
        asset_type="video",
        filename=f"{project_id}_{export_id}.mp4",
        storage_rel_path=rel_path,
        mime_type="video/mp4",
        size_bytes=size_bytes,
        sha256=sha256_hex,
        is_locked_reference=False,
    )
    db.add(export_asset)

    job = ExportJobRecord(
        id=export_id,
        project_id=project_id,
        sequence_id=seq.id,
        state=ExportJobState.SUCCEEDED if probe_meta.get("valid") else ExportJobState.FAILED,
        aspect_ratio=aspect,
        fps=fps,
        resolution=res,
        video_codec="h264",
        audio_codec="aac",
        ffmpeg_args_json=ffmpeg_args,
        manifest_snapshot_json=seq.manifest_json,
        output_asset_id=asset_id,
        ffprobe_verification_json=probe_meta,
        finished_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.commit()

    return {
        "export_id": job.id,
        "sequence_id": seq.id,
        "state": job.state.value,
        "aspect_ratio": job.aspect_ratio,
        "fps": job.fps,
        "resolution": job.resolution,
        "video_codec": job.video_codec,
        "audio_codec": job.audio_codec,
        "output_asset_id": asset_id,
        "download_url": f"/api/v1/assets/{asset_id}/download",
        "ffmpeg_args": ffmpeg_args,
        "ffprobe_verification": probe_meta,
    }

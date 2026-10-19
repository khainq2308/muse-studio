from __future__ import annotations
import asyncio
import functools as _p3_functools
import inspect as _p3_inspect
import json as _p3_json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse as _P3JSONResponse
from pydantic import BaseModel, Field

from muse_orchestrator.api.dependencies import get_studio_repository
from muse_orchestrator.core.config import SecretRedactor, settings
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import (
    Asset, TimelineSequenceRecord, TimelineTrackRecord, TimelineClipRecord,
    ExportJobRecord, ExportJobState
)
from muse_orchestrator.services.asset_storage import AssetStorageError, LocalAssetStorage
from muse_orchestrator.services.timeline_service import (
    ExportClip, ExportError, TimelineValidationService
)

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
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """
    Sắp xếp các Shot Takes lên Timeline, hỗ trợ Trim, Reorder, Cross Dissolve,
    Audio/Music/Voice-over tracks, Subtitle SRT/VTT và Title Card (Mục 3.9 & M6 PRD).
    """
    proj = await repository.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

    shots = await repository.list_project_shots(project_id)
    if shots is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

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
            take = await repository.get_shot_take(s.selected_take_id)
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
    v_track = TimelineTrackRecord(
        id=v_track_id,
        sequence_id=seq_id,
        track_index=1,
        track_type="video",
        name="V1 - Main Shot Sequence",
        clips_json=clip_dicts,
    )

    audio_track_rows = [
        TimelineTrackRecord(
            id=f"trk_a_{uuid.uuid4().hex[:8]}",
            sequence_id=seq_id,
            track_index=idx,
            track_type=aud.track_type,
            name=aud.name,
            muted=aud.muted,
            volume=aud.volume,
            clips_json=[aud.model_dump()],
        )
        for idx, aud in enumerate(payload.audio_tracks, start=2)
    ]
    clip_rows = [
        TimelineClipRecord(
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
        )
        for c in clip_dicts
    ]
    await repository.create_timeline_bundle(seq, [v_track, *audio_track_rows], clip_rows)

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
    repository: StudioRepository = Depends(get_studio_repository),
) -> dict[str, Any]:
    seq = await repository.get_latest_timeline_sequence(project_id)
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


async def _resolve_export_clips(
    repository: StudioRepository, project_id: str, sequence_id: str
) -> tuple[list[ExportClip], list[str]]:
    """Map the sequence's clip rows to real video files that belong to this project."""
    rows = await repository.list_timeline_clips(sequence_id)
    storage = LocalAssetStorage(settings.media_root)
    clips: list[ExportClip] = []
    unresolved: list[str] = []
    for row in rows:
        asset_id = row.asset_id
        if not asset_id and row.take_id:
            take = await repository.get_shot_take(row.take_id)
            if take is not None and take.project_id == project_id:
                asset_id = take.asset_id
        asset = await repository.get_asset(asset_id) if asset_id else None
        if asset is None or asset.project_id != project_id or not asset.mime_type.startswith("video/"):
            unresolved.append(f"clip #{row.order_index}: không có asset video thuộc project này")
            continue
        try:
            path = storage.resolve_safe_path(asset.storage_rel_path)
        except AssetStorageError:
            unresolved.append(f"clip #{row.order_index}: file asset không còn trên đĩa")
            continue
        clips.append(
            ExportClip(
                path=str(path),
                effective_duration=row.effective_duration_seconds,
                trim_start=row.trim_start_seconds,
                transition_type=row.transition_type,
                transition_duration=row.transition_duration_seconds,
                start_time=row.start_time_seconds,
            )
        )
    return clips, unresolved


def _unsupported_export_features(seq: TimelineSequenceRecord) -> list[str]:
    """Timeline features the exporter does not render yet, so the caller is never misled."""
    manifest = seq.manifest_json or {}
    features = ["clip_audio"]  # output always carries a silent AAC track, clip audio is not mixed
    if manifest.get("title_card"):
        features.append("title_card")
    if manifest.get("has_subtitles"):
        features.append("subtitles")
    if manifest.get("audio_tracks"):
        features.append("audio_tracks")
    return features


@router.post("/exports", status_code=status.HTTP_201_CREATED)
async def launch_ffmpeg_export(
    project_id: str,
    payload: ExportLaunchIn,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """
    Render the timeline's real clips to MP4 (H.264 + AAC) with FFmpeg, verify the file with FFprobe
    and store it in the Asset Library. A failed render or a failed verification is recorded as a
    FAILED export job and returned as an error; no asset is created for it.
    """
    if payload.sequence_id:
        seq = await repository.get_timeline_sequence(payload.sequence_id)
    else:
        seq = await repository.get_latest_timeline_sequence(project_id)

    if not seq or seq.project_id != project_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy Timeline Sequence để Export.")

    aspect = payload.aspect_ratio or seq.aspect_ratio
    fps = payload.fps or seq.fps
    res = TimelineValidationService.resolve_resolution(aspect, payload.resolution)

    clips, unresolved = await _resolve_export_clips(repository, project_id, seq.id)
    if unresolved:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Không thể export vì còn clip thiếu video: " + "; ".join(unresolved),
        )
    unsupported = _unsupported_export_features(seq)

    export_id = f"exp_{uuid.uuid4().hex[:10]}"
    out_dir = os.path.join(settings.media_root, project_id, "exports")

    try:
        # FFmpeg is a blocking subprocess; keep it off the event loop.
        out_path, size_bytes, sha256_hex, ffmpeg_args, probe_meta = await asyncio.to_thread(
            TimelineValidationService.render_and_probe_mp4,
            output_dir=out_dir,
            export_id=export_id,
            fps=fps,
            resolution=res,
            clips=clips,
        )
        if not probe_meta.get("valid"):
            raise ExportError(
                "File export không vượt qua kiểm tra FFprobe.",
                ffmpeg_args=ffmpeg_args,
                probe_meta=probe_meta,
            )
    except ExportError as exc:
        message = SecretRedactor.redact_text(exc.message)
        failed_job = ExportJobRecord(
            id=export_id,
            project_id=project_id,
            sequence_id=seq.id,
            state=ExportJobState.FAILED,
            aspect_ratio=aspect,
            fps=fps,
            resolution=res,
            video_codec="h264",
            audio_codec="aac",
            ffmpeg_args_json=exc.ffmpeg_args,
            manifest_snapshot_json=seq.manifest_json,
            ffprobe_verification_json=exc.probe_meta,
            error_message_redacted=message,
            finished_at=datetime.now(timezone.utc),
        )
        await repository.create_export_job(failed_job)
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "export_id": export_id,
                "state": ExportJobState.FAILED.value,
                "message": message,
                "unsupported_features": unsupported,
            },
        ) from exc

    probe_meta["unsupported_features"] = unsupported
    asset_id = f"ast_exp_{uuid.uuid4().hex[:8]}"
    rel_path = os.path.relpath(out_path, settings.media_root)
    asset = Asset(
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
    job = ExportJobRecord(
        id=export_id,
        project_id=project_id,
        sequence_id=seq.id,
        state=ExportJobState.SUCCEEDED,
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
    await repository.create_export_result(asset, job)

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
        "unsupported_features": unsupported,
    }

# --- P3 HARDENING: PRESERVE ROUTE SIGNATURE & INCLUDE unsupported_features IN ALL 422 RESPONSES ---
def _install_p3_timeline_api_patch() -> None:
    orig_ep = globals().get("launch_ffmpeg_export")
    if orig_ep is None or getattr(orig_ep, "_p3_installed", False):
        return

    orig_sig = _p3_inspect.signature(orig_ep)

    @_p3_functools.wraps(orig_ep)
    async def _p3_export_wrapper(*args: Any, **kwargs: Any) -> Any:
        bound = orig_sig.bind_partial(*args, **kwargs)
        b_args = bound.arguments
        project_id = b_args.get("project_id")
        payload = b_args.get("payload")
        repository = b_args.get("repository")

        early_unsupported: list[str] = []
        if repository is not None and project_id:
            try:
                seq_id = getattr(payload, "sequence_id", None) if payload else None
                if seq_id:
                    seq_obj = await repository.get_timeline_sequence(seq_id)
                else:
                    seq_obj = await repository.get_latest_timeline_sequence(project_id)
                if seq_obj and seq_obj.project_id == project_id:
                    early_unsupported = _unsupported_export_features(seq_obj)
            except Exception:
                early_unsupported = []

        try:
            resp = await orig_ep(*args, **kwargs)
            if isinstance(resp, _P3JSONResponse) and resp.status_code == 422:
                data = _p3_json.loads(resp.body.decode("utf-8") or "{}")
                if isinstance(data, dict) and "unsupported_features" not in data:
                    data["unsupported_features"] = early_unsupported
                    return _P3JSONResponse(status_code=422, content=data)
            return resp
        except HTTPException as exc:
            if exc.status_code == 422:
                if isinstance(exc.detail, dict):
                    content = dict(exc.detail)
                    content.setdefault("unsupported_features", early_unsupported)
                else:
                    content = {"detail": str(exc.detail), "unsupported_features": early_unsupported}
                return _P3JSONResponse(status_code=422, content=content)
            raise

    _p3_export_wrapper.__signature__ = orig_sig
    _p3_export_wrapper._p3_installed = True
    globals()["launch_ffmpeg_export"] = _p3_export_wrapper

    for r in router.routes:
        if getattr(r, "endpoint", None) is orig_ep:
            r.endpoint = _p3_export_wrapper
            if hasattr(r, "dependant") and hasattr(r.dependant, "call"):
                r.dependant.call = _p3_export_wrapper


_install_p3_timeline_api_patch()

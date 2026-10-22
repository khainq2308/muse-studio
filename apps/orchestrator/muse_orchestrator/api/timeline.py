from __future__ import annotations
import math
import uuid
from dataclasses import asdict
from typing import Any, Literal
from typing_extensions import TypeAliasType
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from muse_orchestrator.api.arq_dispatch import schedule_export_job
from muse_orchestrator.api.dependencies import get_studio_repository
from muse_orchestrator.core.config import settings
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import (
    TimelineSequenceRecord, TimelineTrackRecord, TimelineClipRecord,
    ExportJobRecord, ExportJobState
)
from muse_orchestrator.services.asset_storage import AssetStorageError, LocalAssetStorage
from muse_orchestrator.services.timeline_service import (
    ExportClip, TimelineValidationService
)

router = APIRouter(prefix="/api/v1/projects/{project_id}/timeline", tags=["M6 Timeline Editor & FFmpeg Export"])


MAX_EXPORT_DIMENSION = 4096
MIN_EXPORT_DIMENSION = 16


class ClipEditItemIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    shot_id: str | None = None
    take_id: str | None = None
    asset_id: str | None = None
    order_index: int
    start_time_seconds: float | None = Field(default=None, ge=0.0, le=86400.0)
    source_duration_seconds: float = Field(default=5.0, ge=0.2, le=600.0)
    trim_start_seconds: float = Field(default=0.0, ge=0.0, le=600.0)
    trim_end_seconds: float = Field(default=0.0, ge=0.0, le=600.0)
    transition_type: str = Field(default="hard_cut", pattern=r"^(hard_cut|cross_dissolve)$")
    transition_duration_seconds: float = Field(default=0.0, ge=0.0, le=5.0)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    fade_in_seconds: float = Field(default=0.0, ge=0.0, le=10.0)
    fade_out_seconds: float = Field(default=0.0, ge=0.0, le=10.0)

    @model_validator(mode="after")
    def _trims_leave_playable_media(self) -> "ClipEditItemIn":
        if self.trim_start_seconds + self.trim_end_seconds >= self.source_duration_seconds:
            raise ValueError(
                "trim_start_seconds + trim_end_seconds must be less than source_duration_seconds."
            )
        return self


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

    @field_validator("title_card")
    @classmethod
    def _validate_title_card_duration(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "duration_seconds" in value:
            raw = value["duration_seconds"]
            if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
                raise ValueError("title_card.duration_seconds must be a number.")
            try:
                duration = float(raw)
            except ValueError as exc:
                raise ValueError("title_card.duration_seconds must be a number.") from exc
            if not math.isfinite(duration) or duration < 0 or duration > 60:
                raise ValueError("title_card.duration_seconds must be between 0 and 60.")
            value = {**value, "duration_seconds": duration}
        return value


class ExportLaunchIn(BaseModel):
    sequence_id: str | None = None
    aspect_ratio: str | None = Field(default=None, pattern=r"^(16:9|9:16|1:1|21:9)$")
    fps: int | None = Field(default=None, ge=12, le=60)
    resolution: str | None = Field(default=None, pattern=r"^\d{2,4}x\d{2,4}$")

    @field_validator("resolution")
    @classmethod
    def _validate_resolution(cls, value: str | None) -> str | None:
        if value is None:
            return value
        width, height = (int(part) for part in value.split("x"))
        for dim in (width, height):
            if dim % 2 or not MIN_EXPORT_DIMENSION <= dim <= MAX_EXPORT_DIMENSION:
                raise ValueError(
                    f"resolution dimensions must be even and between {MIN_EXPORT_DIMENSION} "
                    f"and {MAX_EXPORT_DIMENSION}."
                )
        return value


TimelineJsonValue = TypeAliasType(
    "TimelineJsonValue",
    str | int | float | bool | None
    | list["TimelineJsonValue"]
    | dict[str, "TimelineJsonValue"],
)


class TimelineClipOut(BaseModel):
    shot_id: str | None = None
    take_id: str | None = None
    asset_id: str | None = None
    order_index: int
    start_time_seconds: float
    source_duration_seconds: float
    trim_start_seconds: float
    trim_end_seconds: float
    effective_duration_seconds: float
    transition_type: Literal["hard_cut", "cross_dissolve"]
    transition_duration_seconds: float
    volume: float
    fade_in_seconds: float
    fade_out_seconds: float


class TimelineAudioTrackOut(BaseModel):
    track_type: Literal["audio", "voice_over", "music"]
    name: str
    asset_id: str | None = None
    volume: float
    fade_in_seconds: float
    fade_out_seconds: float
    muted: bool


class TimelineWarningOut(BaseModel):
    code: str
    severity: Literal["warning"]
    message: str
    shot_id: str | None = None
    clip_index: int | None = None


class TimelineManifestOut(BaseModel):
    sequence_id: str
    project_id: str
    aspect_ratio: str
    fps: int
    resolution: str
    total_duration_seconds: float
    title_card: dict[str, TimelineJsonValue]
    subtitle_format: Literal["srt", "vtt"]
    has_subtitles: bool
    video_clips: list[TimelineClipOut]
    audio_tracks: list[TimelineAudioTrackOut]


class TimelineAssembleOut(BaseModel):
    sequence_id: str
    name: str
    aspect_ratio: str
    fps: int
    resolution: str
    total_duration_seconds: float
    clip_count: int
    audio_track_count: int
    has_subtitles: bool
    warnings: list[TimelineWarningOut]
    manifest: TimelineManifestOut


class TimelineSequenceOut(BaseModel):
    sequence_id: str
    name: str
    aspect_ratio: str
    fps: int
    resolution: str
    total_duration_seconds: float
    warnings: list[TimelineWarningOut]
    manifest: TimelineManifestOut


class ExportLaunchOut(BaseModel):
    export_id: str
    sequence_id: str
    state: Literal["queued"]
    aspect_ratio: str
    fps: int
    resolution: str
    unsupported_features: list[str]


class ExportJobStatusOut(BaseModel):
    export_id: str
    sequence_id: str
    state: ExportJobState
    aspect_ratio: str
    fps: int
    resolution: str
    video_codec: str
    audio_codec: str
    unsupported_features: list[str]
    output_asset_id: str | None = None
    download_url: str | None = None
    ffmpeg_args: list[str] | None = None
    ffprobe_verification: dict[str, TimelineJsonValue] | None = None
    message: str | None = None


async def _validate_custom_clip_ownership(
    project_id: str,
    position: int,
    clip: ClipEditItemIn,
    repository: StudioRepository,
) -> None:
    """Reject clips that reference another project's shot/take/asset or mismatched pairs."""

    def reject(reason: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"custom_clips[{position}]: {reason}",
        )

    if clip.shot_id is not None:
        shot = await repository.get_shot(clip.shot_id)
        if shot is None or shot.project_id != project_id:
            raise reject("shot_id không thuộc dự án này.")
    if clip.take_id is not None:
        take = await repository.get_timeline_shot_take(clip.take_id)
        if take is None or take.project_id != project_id:
            raise reject("take_id không thuộc dự án này.")
        if clip.shot_id is not None and take.shot_id != clip.shot_id:
            raise reject("take_id không thuộc shot_id đã chọn.")
        if clip.asset_id is not None and take.asset_id != clip.asset_id:
            raise reject("asset_id không khớp với asset của take_id.")
    if clip.asset_id is not None:
        asset = await repository.get_asset(clip.asset_id)
        if asset is None or asset.project_id != project_id:
            raise reject("asset_id không thuộc dự án này.")


@router.post("/assemble", response_model=TimelineAssembleOut, status_code=status.HTTP_201_CREATED)
async def assemble_or_update_timeline(
    project_id: str,
    payload: TimelineAssembleIn,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> TimelineAssembleOut:
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
        for position, clip in enumerate(payload.custom_clips):
            await _validate_custom_clip_ownership(project_id, position, clip, repository)
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
            take = await repository.get_timeline_shot_take(s.selected_take_id)
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


def _clip_record_to_manifest_item(row: TimelineClipRecord) -> dict[str, Any]:
    """Reconstruct one manifest clip from its normalized database row."""
    transition = row.transition_type
    if transition not in {"hard_cut", "cross_dissolve"}:
        transition = "hard_cut"
    return {
        "shot_id": row.shot_id,
        "take_id": row.take_id,
        "asset_id": row.asset_id,
        "order_index": row.order_index,
        "start_time_seconds": row.start_time_seconds,
        "source_duration_seconds": row.source_duration_seconds,
        "trim_start_seconds": row.trim_start_seconds,
        "trim_end_seconds": row.trim_end_seconds,
        "effective_duration_seconds": row.effective_duration_seconds,
        "transition_type": transition,
        "transition_duration_seconds": row.transition_duration_seconds,
        "volume": row.volume,
        "fade_in_seconds": row.fade_in_seconds,
        "fade_out_seconds": row.fade_out_seconds,
    }


def _audio_track_record_to_manifest_item(row: TimelineTrackRecord) -> dict[str, Any] | None:
    """Reconstruct an audio manifest item from a persisted track row."""
    if row.track_type not in {"audio", "voice_over", "music"}:
        return None
    clip = row.clips_json[0] if row.clips_json and isinstance(row.clips_json[0], dict) else {}
    return {
        "track_type": row.track_type,
        "name": row.name,
        "asset_id": clip.get("asset_id"),
        "volume": row.volume,
        "fade_in_seconds": clip.get("fade_in_seconds", 0.0),
        "fade_out_seconds": clip.get("fade_out_seconds", 0.0),
        "muted": row.muted,
    }


async def _normalize_timeline_manifest(
    seq: TimelineSequenceRecord,
    repository: StudioRepository,
) -> dict[str, Any]:
    """Fill legacy manifests from sequence columns and normalized child records.

    Older rows can have an empty/default manifest_json. Returning that raw value
    through TimelineManifestOut would turn a previously successful GET into a
    500 response-validation error. Use persisted sequence/clip/track records as
    the source of truth for any missing top-level manifest data.
    """
    raw = seq.manifest_json if isinstance(seq.manifest_json, dict) else {}
    clip_rows = await repository.list_timeline_clips(seq.id)
    track_rows = await repository.list_timeline_tracks(seq.id)

    video_clips = raw.get("video_clips")
    if not isinstance(video_clips, list) or (not video_clips and clip_rows):
        video_clips = [_clip_record_to_manifest_item(row) for row in clip_rows]

    audio_tracks = raw.get("audio_tracks")
    persisted_audio_tracks = [
        item
        for row in track_rows
        if (item := _audio_track_record_to_manifest_item(row)) is not None
    ]
    if not isinstance(audio_tracks, list) or (not audio_tracks and persisted_audio_tracks):
        audio_tracks = persisted_audio_tracks

    title_card = raw.get("title_card")
    if not isinstance(title_card, dict):
        title_card = seq.title_card_json if isinstance(seq.title_card_json, dict) else {}

    subtitle_format = raw.get("subtitle_format")
    if subtitle_format not in {"srt", "vtt"}:
        subtitle_format = seq.subtitle_format if seq.subtitle_format in {"srt", "vtt"} else "srt"

    return {
        "sequence_id": raw.get("sequence_id") or seq.id,
        "project_id": raw.get("project_id") or seq.project_id,
        "aspect_ratio": raw.get("aspect_ratio") or seq.aspect_ratio,
        "fps": raw.get("fps") if raw.get("fps") is not None else seq.fps,
        "resolution": raw.get("resolution") or seq.resolution_profile,
        "total_duration_seconds": (
            raw.get("total_duration_seconds")
            if raw.get("total_duration_seconds") is not None
            else seq.total_duration_seconds
        ),
        "title_card": title_card,
        "subtitle_format": subtitle_format,
        "has_subtitles": (
            raw.get("has_subtitles")
            if raw.get("has_subtitles") is not None
            else bool((seq.subtitle_content or "").strip())
        ),
        "video_clips": video_clips,
        "audio_tracks": audio_tracks,
    }


@router.get("", response_model=TimelineSequenceOut)
async def get_latest_project_timeline(
    project_id: str,
    repository: StudioRepository = Depends(get_studio_repository),
) -> TimelineSequenceOut:
    seq = await repository.get_latest_timeline_sequence(project_id)
    if not seq:
        raise HTTPException(status_code=404, detail="Project chưa có Timeline Sequence nào.")

    warnings = seq.warnings_json if isinstance(seq.warnings_json, list) else []
    manifest = await _normalize_timeline_manifest(seq, repository)
    return {
        "sequence_id": seq.id,
        "name": seq.name,
        "aspect_ratio": seq.aspect_ratio,
        "fps": seq.fps,
        "resolution": seq.resolution_profile,
        "total_duration_seconds": seq.total_duration_seconds,
        "warnings": warnings,
        "manifest": manifest,
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
            take = await repository.get_timeline_shot_take(row.take_id)
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


@router.post("/exports", response_model=ExportLaunchOut, status_code=status.HTTP_202_ACCEPTED)
async def launch_ffmpeg_export(
    project_id: str,
    payload: ExportLaunchIn,
    request: Request,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> ExportLaunchOut:
    """
    Validate the timeline, persist a QUEUED export job and hand the render to the
    ARQ worker (process_export_job in workers/queue_worker.py). The FFmpeg render
    no longer runs inside this request; poll GET /exports/{export_id} for the
    result. A failed render is recorded as a FAILED export job by the worker; no
    asset is created for it.
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
    job = ExportJobRecord(
        id=export_id,
        project_id=project_id,
        sequence_id=seq.id,
        state=ExportJobState.QUEUED,
        aspect_ratio=aspect,
        fps=fps,
        resolution=res,
        video_codec="h264",
        audio_codec="aac",
        manifest_snapshot_json={
            **(seq.manifest_json or {}),
            # The worker renders from this resolved plan instead of re-reading
            # the timeline; a clip file deleted after launch fails the render.
            "resolved_clips": [asdict(clip) for clip in clips],
            "unsupported_features": unsupported,
        },
    )
    await repository.create_export_job(job)
    # On dispatch failure this raises 503; the job stays QUEUED and the worker's
    # recovery cron re-enqueues it once Redis is reachable.
    await schedule_export_job(request, export_id)

    return {
        "export_id": export_id,
        "sequence_id": seq.id,
        "state": ExportJobState.QUEUED.value,
        "aspect_ratio": aspect,
        "fps": fps,
        "resolution": res,
        "unsupported_features": unsupported,
    }


@router.get("/exports/{export_id}", response_model=ExportJobStatusOut, response_model_exclude_unset=True)
async def get_export_job_status(
    project_id: str,
    export_id: str,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> ExportJobStatusOut:
    """Poll one export job: queued/running until the worker records succeeded/failed."""
    job = await repository.get_export_job(export_id)
    if not job or job.project_id != project_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy Export Job.")

    result: dict[str, Any] = {
        "export_id": job.id,
        "sequence_id": job.sequence_id,
        "state": job.state.value,
        "aspect_ratio": job.aspect_ratio,
        "fps": job.fps,
        "resolution": job.resolution,
        "video_codec": job.video_codec,
        "audio_codec": job.audio_codec,
        "unsupported_features": (job.manifest_snapshot_json or {}).get("unsupported_features", []),
    }
    if job.state == ExportJobState.SUCCEEDED:
        result["output_asset_id"] = job.output_asset_id
        if job.output_asset_id:
            result["download_url"] = f"/api/v1/assets/{job.output_asset_id}/download"
        else:
            result["message"] = "Export is marked succeeded but has no output asset."
        result["ffmpeg_args"] = job.ffmpeg_args_json
        result["ffprobe_verification"] = job.ffprobe_verification_json
    elif job.state == ExportJobState.FAILED:
        result["message"] = job.error_message_redacted or "Export failed."
    return result

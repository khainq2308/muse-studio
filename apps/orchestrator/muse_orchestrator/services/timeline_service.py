from __future__ import annotations
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from muse_orchestrator.core.config import settings

ASPECT_RESOLUTION_MAP: dict[str, str] = {
    "16:9": "1920x1080",
    "9:16": "1080x1920",
    "1:1": "1080x1080",
    "21:9": "2560x1080",
}

_SAFE_RES_RE = re.compile(r"^\d{2,4}x\d{2,4}$")

# Thresholds shared with analyze_clips_and_detect_anomalies.
_GAP_TOLERANCE_SECONDS = 0.05
_MIN_TRANSITION_MARGIN_SECONDS = 0.05
# ARCH-005: limits come from Settings (env EXPORT_FFMPEG_TIMEOUT_SECONDS,
# EXPORT_FFPROBE_TIMEOUT_SECONDS, EXPORT_MAX_DURATION_SECONDS, EXPORT_MAX_CLIPS).
_FFMPEG_TIMEOUT_SECONDS = float(settings.export_ffmpeg_timeout_seconds)
_FFPROBE_TIMEOUT_SECONDS = float(settings.export_ffprobe_timeout_seconds)
_STDERR_TAIL_CHARS = 800
# Hard caps for one export render; restored or tampered timelines must not be able
# to request an unbounded encode (e.g. a 10^7 s black gap).
MAX_EXPORT_DURATION_SECONDS = float(settings.export_max_duration_seconds)
MAX_EXPORT_CLIPS = int(settings.export_max_clips)
# A verified output may differ from the plan by max(0.5 s, 1 %): enough for codec
# frame rounding, small enough to catch a dropped or duplicated clip.
_VERIFY_MIN_TOLERANCE_SECONDS = 0.5
_VERIFY_RELATIVE_TOLERANCE = 0.01
_CANCEL_POLL_SECONDS = 0.25
# ARCH-005: total wall-clock budget build_export_plan may spend probing source clips.
# One ARQ export job covers source probing + the FFmpeg render + output verification
# (two FFprobe calls). Settings guarantees job timeout > render + verification; half of
# the remaining slack goes to source probing, the other half stays as headroom for
# DB/filesystem work, so per-clip probing can never push the job past its ARQ timeout.
_EXPORT_PROBE_BUDGET_SECONDS = max(
    0.0,
    (
        float(settings.export_job_timeout_seconds)
        - _FFMPEG_TIMEOUT_SECONDS
        - float(settings.export_verify_budget_seconds)
    )
    / 2.0,
)


def _finite(value: Any, label: str, position: int) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ExportError(f"Clip #{position}: {label} is not a number.") from exc
    if not math.isfinite(number):
        raise ExportError(f"Clip #{position}: {label} must be a finite number.")
    return number


class ExportError(Exception):
    """An export could not produce a verified MP4. Never raised for a "valid" fake output."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 422,
        ffmpeg_args: list[str] | None = None,
        probe_meta: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.ffmpeg_args = ffmpeg_args or []
        self.probe_meta = probe_meta or {}


class ExportCanceledError(ExportError):
    """The render was cancelled by the caller; the child FFmpeg was killed and output removed."""

    def __init__(self, message: str = "Export was cancelled.", **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 409)
        super().__init__(message, **kwargs)


@dataclass
class ExportClip:
    """A video clip resolved to a real file on disk, ready to be placed on the timeline."""

    path: str
    effective_duration: float
    trim_start: float = 0.0
    transition_type: str = "hard_cut"
    transition_duration: float = 0.0
    start_time: float | None = None


@dataclass
class ExportPlan:
    args: list[str]
    expected_duration: float
    width: int
    height: int
    fps: int
    notes: list[str] = field(default_factory=list)


class TimelineValidationService:
    """
    Tính toán vị trí thời gian của các Clip trên Timeline, phát hiện Gap/Overlap,
    cảnh báo các Shot thiếu Selected Take, và dựng + chạy lệnh FFmpeg an toàn (argv, không dùng shell).
    """

    @staticmethod
    def resolve_resolution(aspect_ratio: str, custom_res: str | None = None) -> str:
        if custom_res and _SAFE_RES_RE.match(custom_res):
            width, height = (int(part) for part in custom_res.split("x"))
            # libx264/yuv420p requires even dimensions; also bound the encode size.
            if all(dim % 2 == 0 and 16 <= dim <= 4096 for dim in (width, height)):
                return custom_res
        return ASPECT_RESOLUTION_MAP.get(aspect_ratio, "1920x1080")

    @staticmethod
    def analyze_clips_and_detect_anomalies(
        clips: list[dict[str, Any]],
        missing_shot_ids: list[str],
    ) -> tuple[float, list[dict[str, Any]]]:
        warnings: list[dict[str, Any]] = []
        for m_id in missing_shot_ids:
            warnings.append({
                "code": "MISSING_SELECTED_TAKE",
                "severity": "warning",
                "message": f"Shot '{m_id}' chưa có Selected Take hợp lệ để đưa vào Timeline.",
                "shot_id": m_id,
            })

        total_duration = 0.0
        prev_end: float | None = None

        for idx, c in enumerate(clips):
            src_dur = float(c.get("source_duration_seconds", 5.0))
            trim_s = max(0.0, float(c.get("trim_start_seconds", 0.0)))
            trim_e = max(0.0, float(c.get("trim_end_seconds", 0.0)))
            eff_dur = max(0.1, round(src_dur - trim_s - trim_e, 3))
            c["effective_duration_seconds"] = eff_dur

            start_t = c.get("start_time_seconds")
            if start_t is None:
                # Tự động xếp nối tiếp; nếu là cross_dissolve thì lùi nhẹ theo transition_duration
                trans_dur = float(c.get("transition_duration_seconds", 0.0)) if c.get("transition_type") == "cross_dissolve" else 0.0
                start_t = max(0.0, round(total_duration - trans_dur, 3)) if idx > 0 else 0.0
                c["start_time_seconds"] = start_t
            else:
                start_t = float(start_t)

            if prev_end is not None:
                delta = round(start_t - prev_end, 3)
                if delta > _GAP_TOLERANCE_SECONDS:
                    warnings.append({
                        "code": "TIMELINE_GAP",
                        "severity": "warning",
                        "message": f"Phát hiện khoảng trống (gap {delta}s) trước clip #{idx + 1}.",
                        "clip_index": idx,
                    })
                elif delta < -_GAP_TOLERANCE_SECONDS and c.get("transition_type") != "cross_dissolve":
                    warnings.append({
                        "code": "TIMELINE_OVERLAP",
                        "severity": "warning",
                        "message": f"Phát hiện chồng lấp ({abs(delta)}s) tại clip #{idx + 1} khi đang dùng hard_cut.",
                        "clip_index": idx,
                    })

            clip_end = round(start_t + eff_dur, 3)
            prev_end = clip_end
            if clip_end > total_duration:
                total_duration = clip_end

        return round(total_duration, 3), warnings

    @classmethod
    def build_export_plan(
        cls,
        clips: list[ExportClip],
        output_path: str,
        fps: int,
        resolution: str,
        *,
        probe_budget_seconds: float | None = None,
    ) -> ExportPlan:
        """
        Build an FFmpeg argv (list[str], never a shell string) that assembles the real clips:
        each clip is trimmed, scaled/padded to the output frame, then joined with a hard cut
        (concat) or cross dissolve (xfade). Gaps on the timeline are filled with black.
        A clip that starts before the previous one ends is only accepted as a cross dissolve
        whose length equals that overlap; any other overlap raises ExportError, so the rendered
        duration always matches the timeline instead of silently growing.

        Clip audio is not mixed: the output carries a silent AAC track. Callers must tell the
        user about every timeline feature that is not rendered.

        Source clips are probed with FFprobe under one overall deadline
        (``probe_budget_seconds``, default ``_EXPORT_PROBE_BUDGET_SECONDS``); each probe gets
        at most the remaining budget and ExportError(504) is raised once it is exhausted.
        """
        if not clips:
            raise ExportError("Timeline không có clip video nào có asset hợp lệ để export.")
        if len(clips) > MAX_EXPORT_CLIPS:
            raise ExportError(f"Timeline có {len(clips)} clip, vượt giới hạn export {MAX_EXPORT_CLIPS} clip.")
        # Reject NaN/Infinity before any arithmetic: restored rows bypass API validation.
        clips = [
            ExportClip(
                path=clip.path,
                effective_duration=_finite(clip.effective_duration, "effective_duration", position),
                trim_start=_finite(clip.trim_start, "trim_start", position),
                transition_type=clip.transition_type,
                transition_duration=_finite(clip.transition_duration, "transition_duration", position),
                start_time=(
                    None if clip.start_time is None else _finite(clip.start_time, "start_time", position)
                ),
            )
            for position, clip in enumerate(clips, start=1)
        ]
        max_duration = float(MAX_EXPORT_DURATION_SECONDS)
        probe_budget = _EXPORT_PROBE_BUDGET_SECONDS if probe_budget_seconds is None else float(probe_budget_seconds)
        probe_deadline: float | None = None  # started lazily at the first real probe

        def probe_timeout(position: int) -> float:
            nonlocal probe_deadline
            if probe_deadline is None:
                probe_deadline = time.monotonic() + max(0.0, probe_budget)
            remaining = probe_deadline - time.monotonic()
            if remaining <= 0:
                raise ExportError(
                    f"FFprobe source-clip probing exceeded its {probe_budget:.0f}s budget "
                    f"at clip #{position}; export aborted.",
                    status_code=504,
                )
            return min(_FFPROBE_TIMEOUT_SECONDS, remaining)

        safe_res = cls.resolve_resolution("", resolution)
        width, height = (int(v) for v in safe_res.split("x"))
        safe_fps = max(12, min(60, int(fps)))

        inputs: list[str] = []
        filters: list[str] = []
        state: dict[str, Any] = {"inputs": 0, "labels": 0, "acc": None, "end": 0.0, "last": 0.0}

        def next_label(prefix: str) -> str:
            state["labels"] += 1
            return f"{prefix}{state['labels']}"

        def add_black(duration: float) -> str:
            idx = state["inputs"]
            state["inputs"] += 1
            inputs.extend([
                "-f", "lavfi", "-t", f"{duration:.3f}",
                "-i", f"color=c=black:s={width}x{height}:r={safe_fps}",
            ])
            label = next_label("g")
            filters.append(f"[{idx}:v]setpts=PTS-STARTPTS,setsar=1,format=yuv420p[{label}]")
            return label

        def add_clip(clip: ExportClip) -> str:
            idx = state["inputs"]
            state["inputs"] += 1
            # Local files only: stops a crafted container from making ffmpeg open other protocols.
            inputs.extend(["-protocol_whitelist", "file,pipe", "-i", clip.path])
            label = next_label("v")
            start = max(0.0, clip.trim_start)
            end = start + clip.effective_duration
            filters.append(
                f"[{idx}:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={safe_fps},format=yuv420p[{label}]"
            )
            return label

        def append_segment(label: str, duration: float, dissolve: float) -> None:
            if state["end"] + duration - dissolve > max_duration:
                raise ExportError(
                    f"Timeline dài hơn giới hạn export {max_duration:.0f}s; hãy rút ngắn timeline."
                )
            if state["acc"] is None:
                state["acc"], state["end"], state["last"] = label, duration, duration
                return
            out = next_label("c")
            if dissolve > 0:
                offset = round(state["end"] - dissolve, 3)
                filters.append(
                    f"[{state['acc']}][{label}]xfade=transition=fade:"
                    f"duration={dissolve:.3f}:offset={offset:.3f}[{out}]"
                )
                state["end"] = round(state["end"] + duration - dissolve, 3)
            else:
                filters.append(f"[{state['acc']}][{label}]concat=n=2:v=1:a=0[{out}]")
                state["end"] = round(state["end"] + duration, 3)
            state["acc"], state["last"] = out, duration

        notes: list[str] = []
        for position, clip in enumerate(clips, start=1):
            duration = max(0.1, round(float(clip.effective_duration), 3))
            if duration > max_duration or abs(clip.trim_start) > max_duration:
                raise ExportError(f"Clip #{position} dài hơn giới hạn export {max_duration:.0f}s.")
            if clip.path and os.path.isfile(clip.path) and shutil.which("ffprobe"):
                req_span = round(max(0.0, float(clip.trim_start)) + duration, 3)
                ok_probe, actual_v_dur = cls._probe_video_stream_duration(
                    clip.path, timeout=probe_timeout(position)
                )
                if not ok_probe:
                    # A probe cut short by the overall budget must not be read as "duration
                    # unknown, carry on": fail the export instead of skipping the check.
                    probe_timeout(position)
                if ok_probe and actual_v_dur is not None and (actual_v_dur + 0.15) < req_span:
                    raise ExportError(
                        f"Source video stream for clip #{position} is shorter ({actual_v_dur:.2f}s) "
                        f"than required clip duration ({req_span:.2f}s)."
                    )
            wants_dissolve = clip.transition_type == "cross_dissolve" and state["acc"] is not None
            requested = max(0.0, float(clip.transition_duration)) if wants_dissolve else 0.0
            dissolve = requested

            if clip.start_time is not None:
                # Where the clip must start relative to what has been laid out so far:
                # > 0 is a real gap on the timeline, < 0 is an overlap with the previous clip.
                # The transition is not part of the gap; it only exists where clips overlap.
                lead = round(float(clip.start_time) - state["end"], 3)
                if state["end"] + lead > max_duration:
                    raise ExportError(
                        f"Clip #{position} bắt đầu sau giới hạn export {max_duration:.0f}s."
                    )
                if lead > _GAP_TOLERANCE_SECONDS:
                    append_segment(add_black(lead), lead, 0.0)
                    dissolve = 0.0
                    if requested > 0:
                        notes.append("cross_dissolve_dropped_across_gap")
                elif lead < -_GAP_TOLERANCE_SECONDS:
                    if state["acc"] is None:
                        raise ExportError(
                            f"Clip #{position} có start_time âm ({float(clip.start_time):.3f}s); "
                            "timeline phải bắt đầu từ 0s trở đi."
                        )
                    overlap = -lead
                    # The only overlap that FFmpeg can render faithfully here is a cross dissolve
                    # whose length equals the overlap. Anything else would silently change the
                    # timeline's duration, so it is refused instead of being exported wrongly.
                    if not wants_dissolve or abs(overlap - requested) > _GAP_TOLERANCE_SECONDS:
                        raise ExportError(
                            f"Clip #{position} chồng lấp {overlap:.3f}s lên clip trước nhưng "
                            + (
                                f"transition cross_dissolve chỉ dài {requested:.3f}s."
                                if wants_dissolve
                                else "không dùng cross_dissolve; export không hỗ trợ chồng lấp kiểu này."
                            )
                            + " Hãy chỉnh start_time hoặc transition cho khớp.",
                        )
                else:
                    # Clips touch without overlapping, so there is nothing to dissolve across.
                    dissolve = 0.0
                    if requested > 0:
                        notes.append("cross_dissolve_ignored_without_overlap")

            if dissolve > 0:
                # A dissolve cannot be longer than either neighbouring segment.
                limit = min(state["last"], duration) - _MIN_TRANSITION_MARGIN_SECONDS
                if dissolve > limit:
                    dissolve = max(0.0, round(limit, 3))
                    notes.append("cross_dissolve_clamped_to_clip_length")

            append_segment(add_clip(ExportClip(
                path=clip.path,
                effective_duration=duration,
                trim_start=clip.trim_start,
            )), duration, dissolve)

        expected = round(float(state["end"]), 3)
        if expected > max_duration:
            raise ExportError(f"Timeline dài hơn giới hạn export {max_duration:.0f}s.")
        audio_idx = state["inputs"]
        inputs.extend([
            "-f", "lavfi", "-t", f"{expected:.3f}",
            "-i", "anullsrc=r=44100:cl=stereo",
        ])

        args = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", f"[{state['acc']}]",
            "-map", f"{audio_idx}:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-t", f"{expected:.3f}",
            output_path,
        ]
        return ExportPlan(
            args=args,
            expected_duration=expected,
            width=width,
            height=height,
            fps=safe_fps,
            notes=notes,
        )

    @staticmethod
    def _stderr_tail(text: str) -> str:
        return (text or "").strip()[-_STDERR_TAIL_CHARS:]

    @staticmethod
    def _parse_fps(rate_str: Any, fallback: float = 24.0) -> float:
        if not rate_str or rate_str in ("0/0", "N/A"):
            return float(fallback)
        s = str(rate_str)
        if "/" in s:
            n_s, d_s = s.split("/", 1)
            try:
                n, d = float(n_s), float(d_s)
                if n > 0 and d > 0:
                    return n / d
            except ValueError:
                return float(fallback)
        try:
            v = float(s)
            return v if v > 0 else float(fallback)
        except ValueError:
            return float(fallback)

    @classmethod
    def _probe_video_stream_duration(
        cls, file_path: str, fallback_fps: float = 24.0, *, timeout: float | None = None
    ) -> tuple[bool, float | None]:
        if not shutil.which("ffprobe") or not os.path.isfile(file_path):
            return False, None
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,duration,nb_frames,avg_frame_rate,r_frame_rate",
            "-of", "json", str(file_path),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=_FFPROBE_TIMEOUT_SECONDS if timeout is None else timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False, None
        if proc.returncode != 0:
            return False, None
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return False, None
        streams = data.get("streams") or []
        if not streams:
            return True, None
        st = streams[0]
        raw_dur = st.get("duration")
        if raw_dur not in (None, "", "N/A"):
            try:
                d = float(raw_dur)
                if d > 0:
                    return True, d
            except (TypeError, ValueError):
                pass
        raw_frames = st.get("nb_frames")
        if raw_frames not in (None, "", "N/A"):
            try:
                frames = float(raw_frames)
                fps = cls._parse_fps(st.get("avg_frame_rate") or st.get("r_frame_rate"), fallback=fallback_fps)
                if frames > 0 and fps > 0:
                    return True, frames / fps
            except (TypeError, ValueError):
                pass
        return True, None

    @classmethod
    def verify_output(cls, out_path: str, plan: ExportPlan) -> dict[str, Any]:
        """Probe the written file with ffprobe and compare it with what the plan promised."""
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,width,height,duration",
            "-show_entries", "format=duration",
            "-of", "json", out_path,
        ]
        meta: dict[str, Any] = {
            "valid": False,
            "probed_by": "ffprobe",
            "expected_duration_seconds": plan.expected_duration,
            "expected_width": plan.width,
            "expected_height": plan.height,
            "problems": [],
        }
        problems: list[str] = meta["problems"]
        try:
            result = subprocess.run(
                probe_cmd, capture_output=True, text=True, check=False, timeout=_FFPROBE_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            problems.append(f"ffprobe could not run: {type(exc).__name__}")
            return meta
        if result.returncode != 0:
            problems.append(f"ffprobe failed: {cls._stderr_tail(result.stderr)}")
            return meta
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            problems.append("ffprobe returned invalid JSON.")
            return meta

        streams = payload.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if video is None:
            problems.append("Output has no video stream.")
        else:
            meta.update({
                "codec_name": video.get("codec_name"),
                "width": video.get("width"),
                "height": video.get("height"),
            })
            if video.get("codec_name") != "h264":
                problems.append(f"Video codec is {video.get('codec_name')!r}, expected 'h264'.")
            if (video.get("width"), video.get("height")) != (plan.width, plan.height):
                problems.append(
                    f"Output is {video.get('width')}x{video.get('height')}, expected {plan.width}x{plan.height}."
                )
        if audio is None:
            problems.append("Output has no audio stream.")
        else:
            meta["audio_codec"] = audio.get("codec_name")
            if audio.get("codec_name") != "aac":
                problems.append(f"Audio codec is {audio.get('codec_name')!r}, expected 'aac'.")

        try:
            duration = float((payload.get("format") or {}).get("duration"))
        except (TypeError, ValueError):
            duration = 0.0
        meta["duration_seconds"] = round(duration, 3)
        tolerance = max(_VERIFY_MIN_TOLERANCE_SECONDS, plan.expected_duration * _VERIFY_RELATIVE_TOLERANCE)
        if duration <= 0:
            problems.append("Output duration could not be read.")
        elif abs(duration - plan.expected_duration) > tolerance:
            problems.append(
                f"Output is {duration:.2f}s long, expected about {plan.expected_duration:.2f}s."
            )

        ok_v_probe, v_dur = cls._probe_video_stream_duration(out_path, fallback_fps=float(plan.fps))
        if ok_v_probe:
            if v_dur is None or abs(v_dur - plan.expected_duration) > tolerance:
                problems.append(
                    f"Video stream duration ({v_dur}s) does not match expected timeline duration ({plan.expected_duration}s)."
                )
            else:
                meta["video_duration_seconds"] = round(v_dur, 3)

        meta["valid"] = not problems
        return meta

    @classmethod
    def render_and_probe_mp4(
        cls,
        *,
        output_dir: str,
        export_id: str,
        fps: int,
        resolution: str,
        clips: list[ExportClip],
        cancel_event: threading.Event | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[str, int, str, list[str], dict[str, Any]]:
        """
        Really render the timeline with FFmpeg and verify the result with FFprobe.

        Raises ExportError when FFmpeg/FFprobe are missing, FFmpeg fails, or the output does not
        match the plan. It never fabricates an MP4 or reports an unverified file as valid.

        Blocking; call it from a worker thread. ``cancel_event`` (optional) lets the caller
        stop the render: once it is set the FFmpeg child is killed, the partial output is
        removed and ExportCanceledError is raised. ``timeout_seconds`` overrides the default
        render deadline (ExportError 504 on expiry).
        """
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            raise ExportError(
                "FFmpeg và FFprobe chưa được cài trên máy chủ nên chưa thể export video.",
                status_code=503,
            )

        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{export_id}.mp4")
        plan = cls.build_export_plan(clips, out_path, fps, resolution)

        deadline = float(timeout_seconds) if timeout_seconds is not None else _FFMPEG_TIMEOUT_SECONDS
        try:
            returncode, stderr = cls._run_render(plan.args, deadline, cancel_event)
        except subprocess.TimeoutExpired as exc:
            cls._discard(out_path)
            raise ExportError("FFmpeg bị quá thời gian khi export.", status_code=504, ffmpeg_args=plan.args) from exc
        except ExportCanceledError as exc:
            cls._discard(out_path)
            exc.ffmpeg_args = plan.args
            raise
        except OSError as exc:
            cls._discard(out_path)
            raise ExportError(
                f"Không chạy được FFmpeg: {type(exc).__name__}.", status_code=503, ffmpeg_args=plan.args
            ) from exc

        if returncode != 0 or not os.path.isfile(out_path):
            cls._discard(out_path)
            raise ExportError(
                f"FFmpeg export thất bại (exit {returncode}): {cls._stderr_tail(stderr)}",
                ffmpeg_args=plan.args,
            )
        if cancel_event is not None and cancel_event.is_set():
            cls._discard(out_path)
            raise ExportCanceledError(ffmpeg_args=plan.args)

        try:
            probe_meta = cls.verify_output(out_path, plan)
        except ExportError:
            # The hardened verifier raises instead of returning valid=False; do not leave the
            # rejected MP4 on disk.
            cls._discard(out_path)
            raise
        probe_meta["notes"] = plan.notes
        if not probe_meta["valid"]:
            cls._discard(out_path)
            raise ExportError(
                "File export không vượt qua kiểm tra FFprobe: " + "; ".join(probe_meta["problems"]),
                ffmpeg_args=plan.args,
                probe_meta=probe_meta,
            )

        digest = hashlib.sha256()
        size = 0
        with open(out_path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return out_path, size, digest.hexdigest(), plan.args, probe_meta

    @staticmethod
    def _run_render(
        args: list[str], timeout_seconds: float, cancel_event: threading.Event | None
    ) -> tuple[int, str]:
        """Run FFmpeg (argv list, no shell) and return (returncode, stderr text).

        Without a cancel event this is a plain subprocess.run with a timeout. With one,
        the child is polled and killed as soon as the event is set or the deadline passes,
        so a cancelled worker job never leaves a stray FFmpeg process behind.
        """
        if cancel_event is None:
            proc = subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout_seconds)
            return proc.returncode, proc.stderr or ""
        if cancel_event.is_set():
            raise ExportCanceledError()
        # stderr goes to a temp file so a chatty child can never block on a full pipe.
        with tempfile.TemporaryFile(mode="w+b") as stderr_file:
            child = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr_file
            )
            try:
                deadline = time.monotonic() + timeout_seconds
                while True:
                    try:
                        returncode = child.wait(timeout=_CANCEL_POLL_SECONDS)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if cancel_event.is_set():
                        raise ExportCanceledError()
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(args, timeout_seconds)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
            stderr_file.seek(0)
            # Only the tail is ever reported; never read an unbounded log into memory.
            stderr_file.seek(max(0, os.fstat(stderr_file.fileno()).st_size - 64 * 1024))
            stderr = stderr_file.read().decode("utf-8", errors="replace")
        return returncode, stderr

    @staticmethod
    def _discard(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

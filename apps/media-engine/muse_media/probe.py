from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any


class MediaProcessingError(RuntimeError):
    """A media probe or transform failed with a safe, actionable error."""


class MediaToolError(MediaProcessingError):
    """FFmpeg/FFprobe could not run or finish (missing binary, timeout).

    This says nothing about the media itself, so callers must not treat the file as corrupt.
    """


@dataclass(frozen=True)
class VideoProbe:
    path: str
    duration_seconds: float
    width: int
    height: int
    fps_numerator: int
    fps_denominator: int
    frame_count: int

    @property
    def fps(self) -> float:
        return self.fps_numerator / self.fps_denominator if self.fps_denominator else 0.0


def _parse_rate(value: str | None) -> tuple[int, int]:
    if not value or value in {"0/0", "N/A"}:
        return 0, 1
    try:
        rate = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return 0, 1
    return rate.numerator, rate.denominator


def probe_video(
    path: str | Path,
    *,
    ffprobe_bin: str = "ffprobe",
    timeout_seconds: float = 20.0,
) -> VideoProbe:
    """Probe a local video using argv (never a shell) and require a decodable video stream."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MediaProcessingError("Source video does not exist or is not a regular file.")

    command = [
        ffprobe_bin,
        "-v", "error",
        "-count_frames",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_type,width,height,avg_frame_rate,nb_read_frames,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        str(source),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise MediaToolError("ffprobe executable was not found.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError("ffprobe timed out while inspecting the video.") from exc

    if result.returncode != 0:
        raise MediaProcessingError("ffprobe could not inspect the video.")
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MediaProcessingError("ffprobe returned invalid JSON.") from exc

    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams:
        raise MediaProcessingError("The asset has no decodable video stream.")
    stream = streams[0]
    try:
        width, height = int(stream["width"]), int(stream["height"])
        frame_count = int(stream.get("nb_read_frames") or 0)
        duration = float((payload.get("format") or {}).get("duration") or stream.get("duration") or 0.0)
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaProcessingError("ffprobe returned incomplete video metadata.") from exc

    if width <= 0 or height <= 0 or duration <= 0:
        raise MediaProcessingError("Video dimensions and duration must be positive.")
    if frame_count <= 0:
        raise MediaProcessingError("ffprobe could not count any decodable video frames.")

    numerator, denominator = _parse_rate(stream.get("avg_frame_rate"))
    return VideoProbe(
        path=str(source),
        duration_seconds=duration,
        width=width,
        height=height,
        fps_numerator=numerator,
        fps_denominator=denominator,
        frame_count=frame_count,
    )

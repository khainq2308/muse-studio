from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .probe import MediaProcessingError, probe_video


@dataclass(frozen=True)
class BoundaryFrames:
    first_frame_path: str
    last_frame_path: str
    first_frame_sha256: str
    last_frame_sha256: str
    source_frame_count: int
    source_duration_seconds: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_frame(
    source: Path,
    output: Path,
    frame_index: int,
    *,
    ffmpeg_bin: str,
    timeout_seconds: float,
) -> None:
    # Escaping the comma is required by FFmpeg's filter parser. argv is passed
    # directly to subprocess.run; no shell interpolation is involved.
    select_filter = f"select=eq(n\\,{frame_index})"
    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        "-i", str(source),
        "-vf", select_filter,
        "-vsync", "0",
        "-frames:v", "1",
        str(output),
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
        raise MediaProcessingError("ffmpeg executable was not found.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaProcessingError("ffmpeg timed out while extracting a boundary frame.") from exc

    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise MediaProcessingError(f"Could not extract boundary frame index {frame_index}.")
    with output.open("rb") as stream:
        signature = stream.read(8)
    if signature != b"\x89PNG\r\n\x1a\n":
        output.unlink(missing_ok=True)
        raise MediaProcessingError("FFmpeg output was not a valid PNG frame.")


def extract_boundary_frames(
    video_path: str | Path,
    output_dir: str | Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_seconds: float = 180.0,
) -> BoundaryFrames:
    """Extract the first and last decoded frames and return content hashes and source metadata."""
    source = Path(video_path).expanduser().resolve()
    metadata = probe_video(source, ffprobe_bin=ffprobe_bin, timeout_seconds=min(timeout_seconds, 30.0))
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    first_path = target_dir / "first_frame.png"
    last_path = target_dir / "last_frame.png"
    _extract_frame(
        source, first_path, 0, ffmpeg_bin=ffmpeg_bin, timeout_seconds=timeout_seconds
    )
    try:
        _extract_frame(
            source,
            last_path,
            metadata.frame_count - 1,
            ffmpeg_bin=ffmpeg_bin,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        first_path.unlink(missing_ok=True)
        raise

    return BoundaryFrames(
        first_frame_path=str(first_path),
        last_frame_path=str(last_path),
        first_frame_sha256=_sha256(first_path),
        last_frame_sha256=_sha256(last_path),
        source_frame_count=metadata.frame_count,
        source_duration_seconds=metadata.duration_seconds,
    )

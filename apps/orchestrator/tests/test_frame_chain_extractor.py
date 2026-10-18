from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from muse_orchestrator.services.generation_queue import _extract_video_boundary_frames


@pytest.mark.asyncio
async def test_generation_queue_extracts_real_boundary_frames_with_ffmpeg(tmp_path: Path):
    """Generate a tiny moving clip and exercise the same extractor path used by production."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg/FFprobe are required for the boundary-frame integration test.")

    source = tmp_path / "moving-test-clip.mp4"
    generated = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=96x64:rate=6:duration=1",
            "-an",
            "-c:v",
            "mpeg4",
            "-q:v",
            "4",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stderr
    assert source.is_file() and source.stat().st_size > 0

    artifacts = await _extract_video_boundary_frames(
        str(source),
        str(tmp_path / "generated"),
        "shot_integration",
        1,
        "muse2api",
    )

    assert [artifact.position for artifact in artifacts] == ["first", "last"]
    for artifact in artifacts:
        frame = Path(artifact.path)
        assert frame.is_file() and frame.stat().st_size > 0
        assert frame.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(frame.read_bytes()).hexdigest() == artifact.sha256

    # The moving test pattern means the first and last decoded frames should be different.
    assert artifacts[0].sha256 != artifacts[1].sha256

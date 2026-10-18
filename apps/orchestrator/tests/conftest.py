from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture
def real_ffmpeg() -> None:
    """Real-media tests skip on a dev machine without FFmpeg, but must never skip in CI.

    CI installs FFmpeg and sets REQUIRE_FFMPEG=1; if the binaries are missing there, a silently
    skipped media test would hide a broken pipeline, so the test fails instead.
    """
    if _HAVE_FFMPEG:
        return
    if os.environ.get("REQUIRE_FFMPEG") == "1":
        pytest.fail("REQUIRE_FFMPEG=1 but ffmpeg/ffprobe are not installed; CI must install them.")
    pytest.skip("ffmpeg and ffprobe are not installed")


@pytest.fixture
def make_test_video(real_ffmpeg: None) -> Callable[..., Path]:
    """Create a small real H.264 test video (the testsrc pattern changes on every frame)."""

    def _make(path: Path, size: str = "320x240", rate: int = 24, seconds: int = 2) -> Path:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={rate}",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path),
            ],
            check=True,
        )
        return path

    return _make

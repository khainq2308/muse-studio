from __future__ import annotations

import os
import shutil

import pytest


def _require(available: bool, what: str) -> None:
    """Skip on a dev machine without `what`, but never skip in CI.

    CI installs FFmpeg and the muse-media package and sets REQUIRE_FFMPEG=1. If they are missing
    there, a silently skipped real-media test would hide a broken pipeline, so the test fails.
    """
    if available:
        return
    if os.environ.get("REQUIRE_FFMPEG") == "1":
        pytest.fail(f"REQUIRE_FFMPEG=1 but {what} is not available; CI must install it.")
    pytest.skip(f"{what} is not available")


@pytest.fixture
def real_ffmpeg() -> None:
    _require(bool(shutil.which("ffmpeg") and shutil.which("ffprobe")), "ffmpeg and ffprobe")


@pytest.fixture
def real_media_engine(real_ffmpeg: None) -> None:
    try:
        import muse_media  # noqa: F401
        available = True
    except ImportError:
        available = False
    _require(available, "the muse-media package (pip install -e apps/media-engine)")

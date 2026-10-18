from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from muse_orchestrator.services.asset_storage import sniff_media_mime
from muse_orchestrator.services.boundary_frames import (
    FFmpegBoundaryFrameExtractor,
    FrameExtractionError,
    MockBoundaryFrameExtractor,
    build_frame_extractor,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _probe_png(path: str) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height", "-of", "json", path,
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["streams"][0]


def _listing(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def test_extractor_choice_follows_the_provider_type() -> None:
    assert isinstance(build_frame_extractor("mock"), MockBoundaryFrameExtractor)
    assert isinstance(build_frame_extractor("muse2api"), FFmpegBoundaryFrameExtractor)


def test_real_extractor_returns_genuine_decoded_frames(tmp_path: Path, make_test_video) -> None:
    video = make_test_video(tmp_path / "shot.mp4", size="320x240", rate=24, seconds=2)
    dest = tmp_path / "generated"

    frames = FFmpegBoundaryFrameExtractor().extract(str(video), str(dest), "sht_a1", 3)

    assert frames.source == "ffmpeg"
    for frame, position in ((frames.first, "first"), (frames.last, "last")):
        data = Path(frame.path).read_bytes()
        assert data.startswith(PNG_SIGNATURE)
        assert sniff_media_mime(data) == "image/png"
        assert frame.size_bytes == len(data)
        assert frame.sha256 == hashlib.sha256(data).hexdigest()
        assert Path(frame.path).name == f"sht_a1_take3_{position}_{frame.sha256[:10]}.png"
        assert Path(frame.path).parent == dest
        # A real decoded frame: a PNG of the video's size, not a few placeholder bytes.
        assert _probe_png(frame.path) == {"codec_name": "png", "width": 320, "height": 240}
        assert frame.size_bytes > 1000

    # The first and the last frame are different pictures (testsrc draws a running counter).
    assert frames.first.sha256 != frames.last.sha256
    # Only the two published frames remain; the working directory is gone.
    assert _listing(dest) == sorted(Path(f.path).name for f in (frames.first, frames.last))


def test_real_extractor_rejects_a_file_that_is_not_a_video(tmp_path: Path, real_ffmpeg) -> None:
    # Has an MP4 signature (so it passes the download checks) but cannot be decoded.
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"this is not a video")
    dest = tmp_path / "generated"

    with pytest.raises(FrameExtractionError):
        FFmpegBoundaryFrameExtractor().extract(str(fake), str(dest), "sht_a1", 1)

    assert _listing(dest) == []  # nothing left behind, not even the working directory


def test_real_extractor_rejects_a_missing_file(tmp_path: Path, real_ffmpeg) -> None:
    dest = tmp_path / "generated"

    with pytest.raises(FrameExtractionError):
        FFmpegBoundaryFrameExtractor().extract(str(tmp_path / "missing.mp4"), str(dest), "sht_a1", 1)

    assert _listing(dest) == []


def test_real_extractor_reports_a_missing_media_engine_instead_of_faking_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "shot.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 32)
    # A None entry in sys.modules makes `import muse_media...` raise ImportError.
    monkeypatch.setitem(sys.modules, "muse_media", None)
    monkeypatch.setitem(sys.modules, "muse_media.boundary_frames", None)
    monkeypatch.setitem(sys.modules, "muse_media.probe", None)
    dest = tmp_path / "generated"

    with pytest.raises(FrameExtractionError, match="media-engine"):
        FFmpegBoundaryFrameExtractor().extract(str(video), str(dest), "sht_a1", 1)

    assert not dest.exists() or _listing(dest) == []


def test_mock_extractor_makes_clearly_labelled_placeholders(tmp_path: Path) -> None:
    video = tmp_path / "mock.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"mock-video")
    dest = tmp_path / "generated"

    frames = MockBoundaryFrameExtractor().extract(str(video), str(dest), "sht_a1", 2)

    assert frames.source == "mock_placeholder"
    for frame in (frames.first, frames.last):
        data = Path(frame.path).read_bytes()
        assert sniff_media_mime(data) == "image/png"  # passes the asset signature check
        assert b"MOCK_PLACEHOLDER_FRAME" in data
        assert Path(frame.path).name.startswith("mock_")
        assert frame.sha256 == hashlib.sha256(data).hexdigest()
    assert frames.first.sha256 != frames.last.sha256

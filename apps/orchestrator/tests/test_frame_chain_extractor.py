from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from muse_media.boundary_frames import BoundaryFrames
from muse_orchestrator.providers.base import ProviderFailure
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


def _assert_valid_tiny_png(path: Path) -> None:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    cursor = 8
    idat = bytearray()
    seen_ihdr = False
    seen_iend = False
    while cursor < len(data):
        length = struct.unpack(">I", data[cursor:cursor + 4])[0]
        kind = data[cursor + 4:cursor + 8]
        payload = data[cursor + 8:cursor + 8 + length]
        stored_crc = struct.unpack(">I", data[cursor + 8 + length:cursor + 12 + length])[0]
        assert zlib.crc32(kind + payload) & 0xFFFFFFFF == stored_crc
        if kind == b"IHDR":
            width, height = struct.unpack(">II", payload[:8])
            assert (width, height) == (1, 1)
            seen_ihdr = True
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            seen_iend = True
            cursor += 12 + length
            break
        cursor += 12 + length
    assert seen_ihdr and seen_iend and cursor == len(data)
    assert len(zlib.decompress(idat)) == 4  # filter byte + one RGB pixel


@pytest.mark.asyncio
async def test_mock_path_uses_valid_placeholder_pngs_without_calling_ffmpeg(tmp_path: Path):
    artifacts = await _extract_video_boundary_frames(
        "synthetic://mock-output",
        str(tmp_path / "generated"),
        "shot_mock",
        1,
        "mock",
        extractor=lambda *_: (_ for _ in ()).throw(AssertionError("mock must not call FFmpeg")),
    )
    assert [artifact.position for artifact in artifacts] == ["first", "last"]
    for artifact in artifacts:
        _assert_valid_tiny_png(Path(artifact.path))
        assert hashlib.sha256(Path(artifact.path).read_bytes()).hexdigest() == artifact.sha256


@pytest.mark.asyncio
async def test_bad_extractor_output_fails_closed_and_cleans_partial_frames(tmp_path: Path):
    def broken_extractor(_source, output_dir):
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        first = out / "first_frame.png"
        last = out / "last_frame.png"
        first.write_bytes(b"not-a-png")
        last.write_bytes(b"not-a-png")
        return BoundaryFrames(
            first_frame_path=str(first),
            last_frame_path=str(last),
            first_frame_sha256=hashlib.sha256(first.read_bytes()).hexdigest(),
            last_frame_sha256=hashlib.sha256(last.read_bytes()).hexdigest(),
            source_frame_count=2,
            source_duration_seconds=1.0,
        )

    with pytest.raises(ProviderFailure, match="Boundary-frame extraction failed"):
        await _extract_video_boundary_frames(
            "unused.mp4",
            str(tmp_path / "generated"),
            "shot_broken",
            1,
            "muse2api",
            extractor=broken_extractor,
        )
    assert not list((tmp_path / "generated").rglob("first_frame.png"))

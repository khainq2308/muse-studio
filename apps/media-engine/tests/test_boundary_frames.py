from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from muse_media.boundary_frames import extract_boundary_frames
from muse_media.probe import MediaProcessingError, probe_video


FFPROBE_RESPONSE = {
    "streams": [{
        "codec_type": "video",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "24/1",
        "nb_read_frames": "72",
        "duration": "3.0",
    }],
    "format": {"duration": "3.0"},
}
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-png-payload"


def test_probe_video_returns_validated_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"placeholder")

    def fake_run(command, **kwargs):
        assert command[0] == "ffprobe"
        assert "-count_frames" in command
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0, json.dumps(FFPROBE_RESPONSE), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = probe_video(source)
    assert result.width == 1920
    assert result.height == 1080
    assert result.fps == 24
    assert result.frame_count == 72
    assert result.duration_seconds == 3.0


def test_probe_video_rejects_missing_video_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '{"streams":[]}', ""),
    )
    with pytest.raises(MediaProcessingError, match="no decodable video stream"):
        probe_video(source)


def test_extract_boundary_frames_uses_last_decoded_index_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"placeholder")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, json.dumps(FFPROBE_RESPONSE), "")
        Path(command[-1]).write_bytes(PNG_BYTES)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = extract_boundary_frames(source, tmp_path / "frames")
    assert len(calls) == 3
    assert "select=eq(n\\,0)" in calls[1]
    assert "select=eq(n\\,71)" in calls[2]
    assert Path(result.first_frame_path).read_bytes() == PNG_BYTES
    assert result.first_frame_sha256 == result.last_frame_sha256
    assert result.source_frame_count == 72

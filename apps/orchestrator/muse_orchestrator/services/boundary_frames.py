"""First/last frame extraction for frame chaining.

Real providers go through the media-engine package (FFprobe + FFmpeg): the frames are genuine
decoded PNGs. The mock provider has no decodable video, so it gets a clearly separate placeholder
extractor; a placeholder is never produced for a real provider.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Protocol


class FrameExtractionError(Exception):
    """The boundary frames could not be extracted; the message is safe to show to an operator."""


@dataclass(frozen=True)
class ExtractedFrame:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ExtractedBoundaryFrames:
    first: ExtractedFrame
    last: ExtractedFrame
    source: str  # "ffmpeg" for real frames, "mock_placeholder" for the mock provider


class BoundaryFrameExtractor(Protocol):
    def extract(self, video_path: str, dest_dir: str, shot_id: str, take_number: int) -> ExtractedBoundaryFrames: ...


def _frame_filename(prefix: str, shot_id: str, take_number: int, position: str, sha256_hex: str) -> str:
    return f"{prefix}{shot_id}_take{take_number}_{position}_{sha256_hex[:10]}.png"


class FFmpegBoundaryFrameExtractor:
    """Extracts the first and last decoded frame of a real video with media-engine."""

    def extract(self, video_path: str, dest_dir: str, shot_id: str, take_number: int) -> ExtractedBoundaryFrames:
        try:
            from muse_media.boundary_frames import extract_boundary_frames
            from muse_media.probe import MediaProcessingError
        except ImportError as exc:
            raise FrameExtractionError(
                "The media-engine package (muse_media) is not installed, so boundary frames cannot be extracted."
            ) from exc

        os.makedirs(dest_dir, exist_ok=True)
        work_dir = tempfile.mkdtemp(prefix=".frames_", dir=dest_dir)
        published: list[str] = []
        try:
            try:
                result = extract_boundary_frames(video_path, work_dir)
            except MediaProcessingError as exc:
                raise FrameExtractionError(str(exc)) from exc

            frames: dict[str, ExtractedFrame] = {}
            for position, source_path, sha256_hex in (
                ("first", result.first_frame_path, result.first_frame_sha256),
                ("last", result.last_frame_path, result.last_frame_sha256),
            ):
                final_path = os.path.join(
                    dest_dir, _frame_filename("", shot_id, take_number, position, sha256_hex)
                )
                os.replace(source_path, final_path)
                published.append(final_path)
                frames[position] = ExtractedFrame(
                    path=final_path, size_bytes=os.path.getsize(final_path), sha256=sha256_hex
                )
            return ExtractedBoundaryFrames(first=frames["first"], last=frames["last"], source="ffmpeg")
        except BaseException:
            # Only what this call published is removed; a failed extraction must not leave frames behind.
            for path in published:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


class MockBoundaryFrameExtractor:
    """Placeholder frames for the mock provider, whose "videos" are not decodable.

    The files carry a PNG signature so that they pass the asset checks, and a ``mock_`` file name
    prefix so that they can never be mistaken for real frames.
    """

    def extract(self, video_path: str, dest_dir: str, shot_id: str, take_number: int) -> ExtractedBoundaryFrames:
        os.makedirs(dest_dir, exist_ok=True)
        with open(video_path, "rb") as source:
            seed = hashlib.sha256(source.read()).hexdigest()
        frames: dict[str, ExtractedFrame] = {}
        for position in ("first", "last"):
            payload = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                + f"MOCK_PLACEHOLDER_FRAME:{shot_id}:take_{take_number}:{position}:{seed}".encode("utf-8")
            )
            digest = hashlib.sha256(payload).hexdigest()
            final_path = os.path.join(
                dest_dir, _frame_filename("mock_", shot_id, take_number, position, digest)
            )
            with open(final_path, "wb") as target:
                target.write(payload)
            frames[position] = ExtractedFrame(path=final_path, size_bytes=len(payload), sha256=digest)
        return ExtractedBoundaryFrames(first=frames["first"], last=frames["last"], source="mock_placeholder")


def build_frame_extractor(provider_type: str) -> BoundaryFrameExtractor:
    if provider_type == "mock":
        return MockBoundaryFrameExtractor()
    return FFmpegBoundaryFrameExtractor()

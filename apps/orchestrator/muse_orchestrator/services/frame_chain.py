"""Media helpers for frame chaining: real boundary frames out, first-frame image in.

* extract_frames        first/last decoded frame of a finished video, through the muse-media package
                        (FFprobe + FFmpeg). Never fabricates a frame.
* write_placeholder_frames
                        valid 1x1 PNGs for the mock provider only, whose "videos" are not decodable.
* load_frame_data_uri   a stored frame as a data: URI, which is what a remote provider can actually
                        use (it cannot reach our asset ids or our disk).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from muse_orchestrator.core.media_types import PNG_SIGNATURE, SIGNATURE_BYTES, detect_media_type
from muse_orchestrator.services.asset_storage import AssetStorageError, LocalAssetStorage

# A first frame travels inline (base64) in the generation request, so it is size-limited.
MAX_INLINE_FRAME_BYTES = 16 * 1024 * 1024
_INLINE_FRAME_TYPES = {"image/png", "image/jpeg", "image/webp"}


class FrameExtractionError(Exception):
    """Boundary frames could not be produced."""


class InvalidVideoError(FrameExtractionError):
    """The file is not a decodable video, so the provider's output is unusable."""


class FrameToolUnavailableError(FrameExtractionError):
    """FFmpeg/FFprobe (or the muse-media package) could not run. Says nothing about the video."""


class FrameInputError(Exception):
    """A stored frame cannot be used as a generation input."""


@dataclass(frozen=True)
class FramePair:
    first_path: str
    last_path: str
    first_sha256: str
    last_sha256: str
    synthetic: bool = False


def extract_frames_sync(video_path: str, output_dir: str) -> FramePair:
    try:
        from muse_media.boundary_frames import extract_boundary_frames
        from muse_media.probe import MediaProcessingError, MediaToolError
    except ImportError as exc:
        raise FrameToolUnavailableError(
            "The muse-media package is not installed (pip install -e apps/media-engine)."
        ) from exc

    try:
        result = extract_boundary_frames(video_path, output_dir)
    except MediaToolError as exc:  # must precede its base class
        raise FrameToolUnavailableError(str(exc)) from exc
    except MediaProcessingError as exc:
        raise InvalidVideoError(str(exc)) from exc
    return FramePair(
        first_path=result.first_frame_path,
        last_path=result.last_frame_path,
        first_sha256=result.first_frame_sha256,
        last_sha256=result.last_frame_sha256,
    )


async def extract_frames(video_path: str, output_dir: str) -> FramePair:
    """FFmpeg is a blocking subprocess; keep it off the event loop."""
    return await asyncio.to_thread(extract_frames_sync, video_path, output_dir)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def placeholder_png(seed: str) -> bytes:
    """A real, decodable 1x1 PNG whose colour is derived from the seed."""
    rgb = hashlib.sha256(seed.encode("utf-8")).digest()[:3]
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"\x00" + rgb))
        + _png_chunk(b"IEND", b"")
    )


def write_placeholder_frames(output_dir: str, seed: str) -> FramePair:
    """Placeholder frames for the mock provider. Real providers must use extract_frames."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[tuple[str, str]] = []
    for name in ("first_frame", "last_frame"):
        data = placeholder_png(f"{seed}:{name}")
        path = directory / f"{name}.png"
        path.write_bytes(data)
        paths.append((str(path), hashlib.sha256(data).hexdigest()))
    (first_path, first_sha), (last_path, last_sha) = paths
    return FramePair(first_path, last_path, first_sha, last_sha, synthetic=True)


def load_frame_data_uri(storage: LocalAssetStorage, storage_rel_path: str) -> str:
    """Read a stored frame (inside the media root) and return it as a base64 data: URI."""
    try:
        path = storage.resolve_safe_path(storage_rel_path)
    except AssetStorageError as exc:
        raise FrameInputError(f"First frame file is not available: {exc}") from exc
    size = path.stat().st_size
    if size > MAX_INLINE_FRAME_BYTES:
        raise FrameInputError(
            f"First frame is {size} bytes, over the {MAX_INLINE_FRAME_BYTES // (1024 * 1024)} MB inline limit."
        )
    data = path.read_bytes()
    media_type = detect_media_type(data[:SIGNATURE_BYTES])
    if media_type not in _INLINE_FRAME_TYPES:
        raise FrameInputError("First frame is not a PNG, JPEG or WebP image.")
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"

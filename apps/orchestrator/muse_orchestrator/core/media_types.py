"""Media type detection from file signatures ("magic bytes").

Declared types (an HTTP Content-Type, a multipart field) are never trusted on their own: the
type of a stored or downloaded file is whatever its leading bytes say it is.
"""

from __future__ import annotations

# Enough leading bytes for every signature below.
SIGNATURE_BYTES = 12

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

ALLOWED_MEDIA_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/webp", "video/mp4"})

MEDIA_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "video/mp4": "mp4",
}


def detect_media_type(head: bytes) -> str | None:
    """Return the media type implied by the leading bytes, or None when it is not recognised."""
    if len(head) < SIGNATURE_BYTES:
        return None
    if head.startswith(PNG_SIGNATURE):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    return None

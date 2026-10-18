from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from muse_orchestrator.providers.base import ErrorCategory, ProviderFailure
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter

BASE = "https://muse.example.com"
API_KEY = "sk-secret-key"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"png-body"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"jpeg-body"
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"VP8 webp-body"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"mp4-body"


class ScriptedStream(httpx.AsyncByteStream):
    """A response body that records how many bytes the adapter actually pulled from it."""

    def __init__(self, chunks: list[bytes], then_raise: Exception | None = None) -> None:
        self.chunks = chunks
        self.then_raise = then_raise
        self.bytes_read = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.bytes_read += len(chunk)
            yield chunk
        if self.then_raise is not None:
            raise self.then_raise


def _adapter(handler, **kwargs) -> Muse2ApiAdapter:
    return Muse2ApiAdapter(BASE, API_KEY, transport=httpx.MockTransport(handler), **kwargs)


def _serving(body: bytes, headers: dict[str, str] | None = None, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, content=body)

    return handler


def _serving_stream(stream: ScriptedStream, headers: dict[str, str] | None = None, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, stream=stream)

    return handler


def _files(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "mime", "extension"),
    [(PNG, "image/png", "png"), (JPEG, "image/jpeg", "jpg"), (WEBP, "image/webp", "webp"), (MP4, "video/mp4", "mp4")],
)
async def test_supported_media_is_stored_under_its_hash_with_the_detected_type(
    body: bytes, mime: str, extension: str, tmp_path: Path
) -> None:
    # A wrong Content-Type must not matter: the file signature decides.
    adapter = _adapter(_serving(body, {"content-type": "text/plain"}))

    stored = await adapter.download_result("task_1", str(tmp_path))

    digest = hashlib.sha256(body).hexdigest()
    assert stored.mime_type == mime
    assert stored.sha256 == digest
    assert stored.size_bytes == len(body)
    assert Path(stored.local_path).name == f"{digest[:16]}.{extension}"
    assert Path(stored.local_path).read_bytes() == body
    assert _files(tmp_path) == [f"{digest[:16]}.{extension}"]  # no temp file left behind


@pytest.mark.asyncio
async def test_declared_content_type_is_never_trusted(tmp_path: Path) -> None:
    adapter = _adapter(_serving(PNG, {"content-type": "video/mp4"}))

    stored = await adapter.download_result("task_1", str(tmp_path))

    assert stored.mime_type == "image/png"
    assert stored.local_path.endswith(".png")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [b"<html><body>Please sign in to continue</body></html>", b'{"error": "quota exceeded"}', b"short", b""],
)
async def test_a_200_response_that_is_not_media_is_rejected_and_leaves_nothing(body: bytes, tmp_path: Path) -> None:
    adapter = _adapter(_serving(body, {"content-type": "video/mp4"}))

    with pytest.raises(ProviderFailure) as excinfo:
        await adapter.download_result("task_1", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_MEDIA
    assert excinfo.value.retryable is False
    assert _files(tmp_path) == []


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_before_any_byte_is_read(tmp_path: Path) -> None:
    stream = ScriptedStream([MP4 + b"x" * 200])
    handler = _serving_stream(stream, {"content-length": str(len(MP4) + 200)})

    with pytest.raises(ProviderFailure) as excinfo:
        await _adapter(handler, max_download_bytes=100).download_result("task_1", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_MEDIA
    assert "download limit" in excinfo.value.message
    assert stream.bytes_read == 0
    assert _files(tmp_path) == []


@pytest.mark.asyncio
async def test_undeclared_oversize_stops_reading_and_removes_the_partial_file(tmp_path: Path) -> None:
    # No Content-Length (chunked), so the limit has to be enforced while streaming.
    stream = ScriptedStream([MP4 + b"a" * 40, b"b" * 60, b"c" * 60, b"d" * 60])

    with pytest.raises(ProviderFailure) as excinfo:
        await _adapter(_serving_stream(stream), max_download_bytes=100).download_result("task_1", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_MEDIA
    assert stream.bytes_read < sum(len(c) for c in stream.chunks)  # it did not drain the whole body
    assert _files(tmp_path) == []


@pytest.mark.asyncio
async def test_a_file_exactly_at_the_limit_is_accepted(tmp_path: Path) -> None:
    body = MP4 + b"z" * (100 - len(MP4))
    assert len(body) == 100

    stored = await _adapter(_serving(body), max_download_bytes=100).download_result("task_1", str(tmp_path))

    assert stored.size_bytes == 100


@pytest.mark.asyncio
async def test_connection_failure_is_a_retryable_network_error(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderFailure) as excinfo:
        await _adapter(handler).download_result("task_1", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.TRANSIENT_NETWORK
    assert excinfo.value.retryable is True
    assert _files(tmp_path) == []


@pytest.mark.asyncio
async def test_connection_dropped_mid_download_leaves_no_partial_file(tmp_path: Path) -> None:
    stream = ScriptedStream([MP4, b"more-bytes"], then_raise=httpx.ReadError("connection reset"))

    with pytest.raises(ProviderFailure) as excinfo:
        await _adapter(_serving_stream(stream)).download_result("task_1", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.TRANSIENT_NETWORK
    assert excinfo.value.retryable is True
    assert _files(tmp_path) == []


@pytest.mark.asyncio
async def test_error_response_bodies_are_read_with_a_cap(tmp_path: Path) -> None:
    stream = ScriptedStream([b"e" * 1024 for _ in range(1000)])  # a 1 MB error page

    with pytest.raises(ProviderFailure) as excinfo:
        await _adapter(_serving_stream(stream, status=500)).download_result("task_1", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.UPSTREAM_5XX
    assert stream.bytes_read <= 64 * 1024 + 1024
    assert _files(tmp_path) == []


@pytest.mark.asyncio
async def test_auth_failure_is_classified_and_not_retryable(tmp_path: Path) -> None:
    with pytest.raises(ProviderFailure) as excinfo:
        await _adapter(_serving(b"nope", status=401)).download_result("task_1", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.AUTH_FAILED
    assert excinfo.value.retryable is False

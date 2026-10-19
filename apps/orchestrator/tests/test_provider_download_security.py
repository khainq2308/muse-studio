from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from muse_orchestrator.providers import muse2api_adapter as adapter_module
from muse_orchestrator.providers.base import ErrorCategory, ProviderFailure
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter, _origin

BASE = "https://muse.example.com"
API_KEY = "sk-secret-key"
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"VIDEO-BYTES"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"IMAGE-BYTES"


def _response(content: bytes, content_type: str = "video/mp4", **kwargs) -> httpx.Response:
    """A fresh response per use: a streamed Response must not be shared between requests."""
    return httpx.Response(200, headers={"content-type": content_type}, content=content, **kwargs)


def _video() -> httpx.Response:
    return _response(MP4_BYTES)


def _redirect(location: str | None) -> httpx.Response:
    return httpx.Response(302, headers={"location": location} if location else {})


class Upstream:
    """Scripted upstream that records every request and the Authorization header it carried."""

    def __init__(self, routes: dict[str, httpx.Response]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((url, request.headers.get("authorization")))
        return self.routes[url]

    def adapter(self) -> Muse2ApiAdapter:
        return Muse2ApiAdapter(BASE, API_KEY, transport=httpx.MockTransport(self))


BEARER = f"Bearer {API_KEY}"


@pytest.mark.asyncio
async def test_media_id_on_the_provider_gets_the_api_key(tmp_path: Path) -> None:
    upstream = Upstream({f"{BASE}/v1/media/task_1.mp4": _video()})

    stored = await upstream.adapter().download_result("task_1.mp4", str(tmp_path))

    assert stored.size_bytes == len(MP4_BYTES)
    assert stored.mime_type == "video/mp4"
    assert upstream.requests == [(f"{BASE}/v1/media/task_1.mp4", BEARER)]


@pytest.mark.asyncio
async def test_absolute_url_on_the_provider_origin_gets_the_api_key(tmp_path: Path) -> None:
    upstream = Upstream({f"{BASE}/files/a.mp4": _video()})

    await upstream.adapter().download_result(f"{BASE}/files/a.mp4", str(tmp_path))

    assert upstream.requests == [(f"{BASE}/files/a.mp4", BEARER)]


def test_origin_ignores_case_and_default_ports_but_not_scheme_or_port() -> None:
    same = _origin("https://muse.example.com/x")
    assert _origin("https://Muse.Example.com:443/y") == same
    assert _origin("http://muse.example.com/x") != same
    assert _origin("https://muse.example.com:8443/x") != same
    assert _origin("https://muse.example.com.evil.com/x") != same
    assert _origin("file:///etc/passwd") is None
    assert _origin("/v1/media/x") is None


@pytest.mark.asyncio
async def test_url_on_another_host_never_receives_the_api_key(tmp_path: Path) -> None:
    upstream = Upstream({"https://cdn.other.com/a.mp4": _video()})

    await upstream.adapter().download_result("https://cdn.other.com/a.mp4", str(tmp_path))

    assert upstream.requests == [("https://cdn.other.com/a.mp4", None)]


@pytest.mark.asyncio
async def test_redirect_to_another_host_drops_the_api_key(tmp_path: Path) -> None:
    upstream = Upstream({
        f"{BASE}/v1/media/r": _redirect("https://cdn.other.com/a.mp4"),
        "https://cdn.other.com/a.mp4": _video(),
    })

    await upstream.adapter().download_result("r", str(tmp_path))

    assert upstream.requests == [
        (f"{BASE}/v1/media/r", BEARER),
        ("https://cdn.other.com/a.mp4", None),
    ]


@pytest.mark.asyncio
async def test_plain_http_to_another_host_is_refused_before_any_request(tmp_path: Path) -> None:
    upstream = Upstream({})

    with pytest.raises(ProviderFailure) as excinfo:
        await upstream.adapter().download_result("http://evil.example.com/a.mp4", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_REQUEST
    assert upstream.requests == []


@pytest.mark.asyncio
async def test_redirect_to_plain_http_on_another_host_is_refused(tmp_path: Path) -> None:
    upstream = Upstream({f"{BASE}/v1/media/r": _redirect("http://evil.example.com/a.mp4")})

    with pytest.raises(ProviderFailure) as excinfo:
        await upstream.adapter().download_result("r", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_REQUEST
    assert len(upstream.requests) == 1  # the evil host was never contacted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    ["file:///etc/passwd", "ftp://muse.example.com/a.mp4", "//evil.example.com/a.mp4", "../../admin", "a/../../b", ""],
)
async def test_unsafe_references_are_refused_without_any_request(reference: str, tmp_path: Path) -> None:
    upstream = Upstream({})

    with pytest.raises(ProviderFailure) as excinfo:
        await upstream.adapter().download_result(reference, str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_REQUEST
    assert upstream.requests == []


@pytest.mark.asyncio
async def test_redirect_loops_and_missing_locations_stop(tmp_path: Path) -> None:
    loop = Upstream({f"{BASE}/v1/media/loop": _redirect(f"{BASE}/v1/media/loop")})
    with pytest.raises(ProviderFailure):
        await loop.adapter().download_result("loop", str(tmp_path))
    assert len(loop.requests) == 4  # initial request + 3 redirects, then give up

    no_location = Upstream({f"{BASE}/v1/media/x": _redirect(None)})
    with pytest.raises(ProviderFailure):
        await no_location.adapter().download_result("x", str(tmp_path))


@pytest.mark.asyncio
async def test_upstream_errors_are_still_classified(tmp_path: Path) -> None:
    upstream = Upstream({f"{BASE}/v1/media/bad": httpx.Response(500, text="boom")})

    with pytest.raises(ProviderFailure) as excinfo:
        await upstream.adapter().download_result("bad", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.UPSTREAM_5XX
    assert excinfo.value.retryable is True


# --- what is accepted: type from the bytes, size cap, no partial files --------------------


class _Chunks(httpx.AsyncByteStream):
    """A streamed body with no Content-Length that can fail part-way, and records being read."""

    def __init__(self, chunks: list[bytes], then_raise: Exception | None = None) -> None:
        self.chunks = chunks
        self.then_raise = then_raise
        self.started = False

    async def __aiter__(self):
        self.started = True
        for chunk in self.chunks:
            yield chunk
        if self.then_raise is not None:
            raise self.then_raise


@pytest.mark.asyncio
async def test_media_type_comes_from_the_bytes_not_the_content_type_header(tmp_path: Path) -> None:
    upstream = Upstream({
        f"{BASE}/v1/media/a": _response(PNG_BYTES, content_type="video/mp4"),
        f"{BASE}/v1/media/b": _response(MP4_BYTES, content_type="image/png"),
    })
    adapter = upstream.adapter()

    png = await adapter.download_result("a", str(tmp_path))
    mp4 = await adapter.download_result("b", str(tmp_path))

    assert (png.mime_type, Path(png.local_path).suffix) == ("image/png", ".png")
    assert (mp4.mime_type, Path(mp4.local_path).suffix) == ("video/mp4", ".mp4")
    assert Path(png.local_path).read_bytes() == PNG_BYTES
    assert png.sha256 == hashlib.sha256(PNG_BYTES).hexdigest()
    assert png.size_bytes == len(PNG_BYTES)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>502 Bad Gateway from the proxy</body></html>",
        b'{"error": "task expired"}',
        b"",
        b"short",
        b"\x00" * 64,
    ],
    ids=["html", "json", "empty", "truncated", "zeros"],
)
async def test_bodies_that_are_not_media_are_rejected_and_leave_no_file(body: bytes, tmp_path: Path) -> None:
    upstream = Upstream({f"{BASE}/v1/media/x": _response(body, content_type="video/mp4")})

    with pytest.raises(ProviderFailure) as excinfo:
        await upstream.adapter().download_result("x", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_UPSTREAM_MEDIA
    assert excinfo.value.retryable is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_a_non_media_body_is_rejected_after_its_first_bytes(tmp_path: Path) -> None:
    stream = _Chunks([b"<html><body>error page</body></html>"], then_raise=AssertionError("body was read past its header"))
    upstream = Upstream({f"{BASE}/v1/media/x": httpx.Response(200, stream=stream)})

    with pytest.raises(ProviderFailure) as excinfo:
        await upstream.adapter().download_result("x", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_UPSTREAM_MEDIA
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_body_over_the_size_limit_is_aborted_while_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter_module, "MAX_MEDIA_DOWNLOAD_BYTES", 1024)
    stream = _Chunks([MP4_BYTES, b"x" * 600, b"x" * 600])  # no Content-Length: only the stream can tell
    upstream = Upstream({f"{BASE}/v1/media/big": httpx.Response(200, stream=stream)})

    with pytest.raises(ProviderFailure) as excinfo:
        await upstream.adapter().download_result("big", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_UPSTREAM_MEDIA
    assert "download limit" in excinfo.value.message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_declared_content_length_over_the_limit_is_refused_before_reading(tmp_path: Path) -> None:
    stream = _Chunks([MP4_BYTES])
    response = httpx.Response(200, headers={"content-length": str(10**12)}, stream=stream)
    upstream = Upstream({f"{BASE}/v1/media/huge": response})

    with pytest.raises(ProviderFailure) as excinfo:
        await upstream.adapter().download_result("huge", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.INVALID_UPSTREAM_MEDIA
    assert stream.started is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_connection_lost_mid_download_is_retryable_and_leaves_no_partial_file(tmp_path: Path) -> None:
    stream = _Chunks([MP4_BYTES, b"y" * 100], then_raise=httpx.ReadError("connection reset by peer"))
    upstream = Upstream({f"{BASE}/v1/media/x": httpx.Response(200, stream=stream)})

    with pytest.raises(ProviderFailure) as excinfo:
        await upstream.adapter().download_result("x", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.TRANSIENT_NETWORK
    assert excinfo.value.retryable is True
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_connection_failure_before_any_response_is_a_provider_failure_not_a_raw_error(tmp_path: Path) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = Muse2ApiAdapter(BASE, API_KEY, transport=httpx.MockTransport(refuse))

    with pytest.raises(ProviderFailure) as excinfo:
        await adapter.download_result("x", str(tmp_path))

    assert excinfo.value.category == ErrorCategory.TRANSIENT_NETWORK
    assert excinfo.value.retryable is True


@pytest.mark.asyncio
async def test_identical_downloads_share_one_content_addressed_file(tmp_path: Path) -> None:
    upstream = Upstream({f"{BASE}/v1/media/a": _video(), f"{BASE}/v1/media/b": _video()})
    adapter = upstream.adapter()

    first = await adapter.download_result("a", str(tmp_path))
    second = await adapter.download_result("b", str(tmp_path))

    assert first.local_path == second.local_path
    assert [p.name for p in tmp_path.iterdir()] == [Path(first.local_path).name]

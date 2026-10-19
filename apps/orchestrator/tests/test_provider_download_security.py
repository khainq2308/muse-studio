from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from muse_orchestrator.providers.base import ErrorCategory, ProviderFailure
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter, _origin, _read_limited_error_body

BASE = "https://muse.example.com"
API_KEY = "sk-secret-key"
# Minimal MP4 ftyp box signature for adapter-level download tests; FFprobe is tested by the queue verifier.
VIDEO = httpx.Response(
    200,
    headers={"content-type": "video/mp4"},
    content=b"\x00\x00\x00\x18ftypmp42" + b"VIDEO-BYTES",
)


def _redirect(location: str | None) -> httpx.Response:
    return httpx.Response(302, headers={"location": location} if location else {})


class _CountingErrorStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.chunks_yielded = 0

    async def __aiter__(self):
        for _ in range(100):
            self.chunks_yielded += 1
            yield b"x" * 1024

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_error_body_reader_stops_at_configured_limit() -> None:
    stream = _CountingErrorStream()
    response = httpx.Response(500, stream=stream)

    body = await _read_limited_error_body(response, limit=4096)

    assert body == "x" * 4096
    assert stream.chunks_yielded == 4  # Does not drain the remaining 96 KiB.


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
async def test_download_rejects_html_masquerading_as_mp4_and_removes_partial_file(tmp_path: Path) -> None:
    upstream = Upstream({
        f"{BASE}/v1/media/html": httpx.Response(
            200, headers={"content-type": "video/mp4"}, content=b"<html>upstream error</html>"
        )
    })

    with pytest.raises(ProviderFailure, match="signature"):
        await upstream.adapter().download_result("html", str(tmp_path))

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_identical_downloads_get_distinct_paths(tmp_path: Path) -> None:
    upstream = Upstream({
        f"{BASE}/v1/media/a.mp4": VIDEO,
        f"{BASE}/v1/media/b.mp4": httpx.Response(
            200, headers={"content-type": "video/mp4"}, content=b"\x00\x00\x00\x18ftypmp42VIDEO-BYTES"
        ),
    })
    adapter = upstream.adapter()

    first = await adapter.download_result("a.mp4", str(tmp_path))
    second = await adapter.download_result("b.mp4", str(tmp_path))

    assert first.sha256 == second.sha256
    assert first.local_path != second.local_path
    assert Path(first.local_path).is_file()
    assert Path(second.local_path).is_file()

@pytest.mark.asyncio
async def test_media_id_on_the_provider_gets_the_api_key(tmp_path: Path) -> None:
    upstream = Upstream({f"{BASE}/v1/media/task_1.mp4": VIDEO})

    stored = await upstream.adapter().download_result("task_1.mp4", str(tmp_path))

    assert stored.size_bytes == len(VIDEO.content)
    assert upstream.requests == [(f"{BASE}/v1/media/task_1.mp4", BEARER)]


@pytest.mark.asyncio
async def test_absolute_url_on_the_provider_origin_gets_the_api_key(tmp_path: Path) -> None:
    upstream = Upstream({f"{BASE}/files/a.mp4": VIDEO})

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
    upstream = Upstream({"https://cdn.other.com/a.mp4": VIDEO})

    await upstream.adapter().download_result("https://cdn.other.com/a.mp4", str(tmp_path))

    assert upstream.requests == [("https://cdn.other.com/a.mp4", None)]


@pytest.mark.asyncio
async def test_redirect_to_another_host_drops_the_api_key(tmp_path: Path) -> None:
    upstream = Upstream({
        f"{BASE}/v1/media/r": _redirect("https://cdn.other.com/a.mp4"),
        "https://cdn.other.com/a.mp4": VIDEO,
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

@pytest.mark.asyncio
async def test_p2_ssrf_blocks_private_ips_loopback_and_internal_dns_redirects(monkeypatch, tmp_path):
    """P2 Regression: Chặn URL IP nội bộ/loopback/link-local và chặn redirect phân giải ra IP nội bộ."""
    import socket
    import httpx
    from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter
    from muse_orchestrator.providers.base import ProviderFailure

    adapter = Muse2ApiAdapter(base_url="https://api.muse.example.com", api_key="sk-live-secret-token")

    for forbidden_url in [
        "https://127.0.0.1/media/video.mp4",
        "https://10.0.0.15/media/video.mp4",
        "https://192.168.1.20/media/video.mp4",
        "https://169.254.169.254/latest/meta-data/",
        "https://localhost/media/video.mp4",
        "https://internal-service.local/media/video.mp4",
    ]:
        with pytest.raises(ProviderFailure, match="SSRF"):
            await adapter.download_result(forbidden_url, str(tmp_path))

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "rebind.attacker.example.org":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.10.1.5", port or 443))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://api.muse.example.com/v1/media/clip.mp4":
            return httpx.Response(302, headers={"Location": "https://rebind.attacker.example.org/evil.mp4"})
        return httpx.Response(200, content=b"should_not_reach", headers={"content-type": "video/mp4"})

    orig_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_async_client(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}),
    )

    with pytest.raises(ProviderFailure, match="SSRF"):
        await adapter.download_result("https://api.muse.example.com/v1/media/clip.mp4", str(tmp_path))

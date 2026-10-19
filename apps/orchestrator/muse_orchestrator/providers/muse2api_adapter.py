from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import socket
import time
import uuid
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urljoin, urlsplit

import httpx
from muse_orchestrator.core.config import SecretRedactor, settings
from muse_orchestrator.providers.base import (
    VideoProvider, ProviderHealth, ModelCapability,
    ImageGenerationRequest, VideoGenerationRequest,
    ProviderTask, ProviderTaskStatus, StoredMedia, ProviderFailure,
    ErrorCategory, classify_http_error
)

_DEFAULT_PORTS = {"http": 80, "https": 443}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_DOWNLOAD_REDIRECTS = 3
_FORBIDDEN_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
}
_FORBIDDEN_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")

Origin = tuple[str, str, int]


def _origin(url: str) -> Origin | None:
    """Return a canonical (scheme, ASCII host, port) for an HTTP(S) origin."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        port = parts.port or _DEFAULT_PORTS.get(scheme)
        if hostname and ":" not in hostname:
            # Normalize IDNs and trailing root dots consistently before comparing
            # provider origins or constructing Host/SNI values.
            hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        elif hostname:
            hostname = hostname.lower()
    except (UnicodeError, ValueError):
        # urlsplit can reject malformed bracketed IPv6 literals and ports.
        return None
    if scheme not in _DEFAULT_PORTS or not hostname or port is None:
        return None
    return scheme, hostname, port


def _is_forbidden_ip(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True unless an address is globally routable."""
    try:
        if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            address = value
        else:
            address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
_EXT_BY_CONTENT_TYPE = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4",
}
_MIME_BY_EXTENSION = {extension: mime for mime, extension in _EXT_BY_CONTENT_TYPE.items()}
_GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream"}


def _detect_media_format(content_type: str, header: bytes) -> tuple[str, str]:
    """Infer a supported media format from its signature and reject MIME/body mismatches."""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = "png"
    elif header.startswith(b"\xff\xd8\xff"):
        extension = "jpg"
    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        extension = "webp"
    elif len(header) >= 12 and header[4:8] == b"ftyp":
        extension = "mp4"
    else:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST,
            "Provider result does not have a supported image/video signature.",
            retryable=False,
        )

    normalized_type = content_type.split(";", 1)[0].strip().lower()
    declared_extension = _EXT_BY_CONTENT_TYPE.get(normalized_type)
    if normalized_type not in _GENERIC_CONTENT_TYPES and declared_extension is None:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "Provider returned an unsupported media content type.", retryable=False
        )
    if declared_extension is not None and declared_extension != extension:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "Provider media content type does not match its file signature.", retryable=False
        )
    return extension, _MIME_BY_EXTENSION[extension]


async def _read_limited_error_body(resp: httpx.Response, limit: int = 4096) -> str:
    """Read only a small prefix of an upstream error response, then let the stream close.

    Response.aread() buffers the entire body before callers can slice it, so a
    large error page could exhaust memory even though only a short diagnostic is used.
    Chunking and stopping once the byte budget is reached bounds application buffering.
    """
    if limit <= 0:
        return ""
    body = bytearray()
    async for chunk in resp.aiter_bytes(chunk_size=1024):
        remaining = limit - len(body)
        if remaining <= 0:
            break
        body.extend(chunk[:remaining])
        if len(body) >= limit:
            break
    return body.decode("utf-8", "replace")


def _json_or_fail(resp: httpx.Response) -> dict:
    """Parse a provider JSON object; a malformed body becomes a ProviderFailure, not a 500."""
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderFailure(
            ErrorCategory.UPSTREAM_5XX, "Provider returned a non-JSON response.", resp.status_code, True
        ) from exc
    if not isinstance(data, dict):
        raise ProviderFailure(
            ErrorCategory.UPSTREAM_5XX, "Provider returned an unexpected JSON shape.", resp.status_code, True
        )
    return data


def _task_from_response(data: dict) -> ProviderTask:
    task_id = str(data.get("id") or data.get("task_id") or "").strip()
    if not task_id:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "Provider response did not include a task id.", None, False
        )
    return ProviderTask(external_task_id=task_id, status=str(data.get("status", "queued")), raw_response=data)


class Muse2ApiAdapter(VideoProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        proxy: str | None = None,
        dns_resolver: Callable[[str, int], Awaitable[list[tuple[Any, ...]]]] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._transport = transport
        self.proxy = proxy
        # Injectable for deterministic tests; production uses loop.getaddrinfo so DNS
        # resolution does not block the event loop.
        self._dns_resolver = dns_resolver

    def _headers(self, idempotency_key: str = "") -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def health(self) -> ProviderHealth:
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=10.0, proxy=self.proxy) as client:
            try:
                h_resp = await client.get(f"{self.base_url}/healthz")
                r_resp = await client.get(f"{self.base_url}/readyz")
                latency = (time.perf_counter() - start) * 1000.0
                healthy = h_resp.status_code == 200
                ready = r_resp.status_code == 200
                state = "healthy" if (healthy and ready) else ("degraded" if healthy else "unavailable")
                return ProviderHealth(healthy=healthy, ready=ready, status_state=state, latency_ms=round(latency, 2))
            except httpx.RequestError as exc:
                latency = (time.perf_counter() - start) * 1000.0
                safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                return ProviderHealth(healthy=False, ready=False, status_state="unavailable", latency_ms=round(latency, 2), details={"error": safe_msg})

    async def list_models(self) -> list[ModelCapability]:
        async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy) as client:
            try:
                resp = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
            except httpx.RequestError as exc:
                safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc

            if resp.status_code >= 400:
                safe_body = SecretRedactor.redact_text(resp.text, [self.api_key or ""])
                raise classify_http_error(resp.status_code, safe_body)

            payload = _json_or_fail(resp)
            items = payload.get("data", [])
            if not isinstance(items, list):
                items = []
            return [
                ModelCapability(
                    model_id=str(item.get("id", "unknown")),
                    modality="video" if "video" in str(item.get("id", "")).lower() else ("image" if "image" in str(item.get("id", "")).lower() else "chat"),
                    verification_status="unverified",
                    raw_metadata=item,
                )
                for item in items
            ]

    async def generate_image(self, request: ImageGenerationRequest) -> ProviderTask:
        body: dict[str, object] = {
            "model": request.model_id,
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "async": True,
        }
        if request.negative_prompt:
            body["negative_prompt"] = request.negative_prompt
        if request.reference_image_urls:
            body["reference_images"] = request.reference_image_urls
        async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy) as client:
            try:
                resp = await client.post(f"{self.base_url}/v1/images/generations", headers=self._headers(request.idempotency_key), json=body)
            except httpx.RequestError as exc:
                safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, SecretRedactor.redact_text(resp.text, [self.api_key or ""]))
            return _task_from_response(_json_or_fail(resp))

    def _build_video_payload(self, request: VideoGenerationRequest) -> dict[str, object]:
        """Build the request body for the selected muse2api schema profile.

        The compat profile preserves the existing aliases for unknown deployments.
        Provider-specific profiles send one frame field only. The www222fff profile
        requires an explicit size value because its accepted format must be confirmed
        against the deployed gateway rather than inferred from the field name.
        """
        profile = settings.video_payload_profile
        body: dict[str, object] = {
            "model": request.model_id,
            "prompt": request.prompt,
            "duration": request.duration_seconds,
        }

        if profile == "www222fff":
            size_value = (settings.video_payload_size or "").strip()
            if not size_value:
                raise ValueError(
                    "MUSE2API_VIDEO_SIZE is required for the www222fff video payload profile."
                )
            body["size"] = size_value
        elif profile == "czg86389-hub":
            body["aspect_ratio"] = request.aspect_ratio
        else:  # compat: keep the historical request contract until the deployment is identified.
            body["size"] = request.aspect_ratio
            body["aspect_ratio"] = request.aspect_ratio

        if request.negative_prompt:
            body["negative_prompt"] = request.negative_prompt
        if request.first_frame_url:
            if profile in ("compat", "www222fff"):
                body["image"] = request.first_frame_url
            if profile in ("compat", "czg86389-hub"):
                body["first_frame_image"] = request.first_frame_url
        if request.reference_image_urls:
            body["reference_images"] = request.reference_image_urls
        return body

    async def generate_video(self, request: VideoGenerationRequest) -> ProviderTask:
        body = self._build_video_payload(request)

        async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy) as client:
            try:
                resp = await client.post(f"{self.base_url}/v1/videos", headers=self._headers(request.idempotency_key), json=body)
            except httpx.RequestError as exc:
                safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, SecretRedactor.redact_text(resp.text, [self.api_key or ""]))
            return _task_from_response(_json_or_fail(resp))

    async def get_task(self, task_id: str) -> ProviderTaskStatus:
        async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy) as client:
            try:
                resp = await client.get(
                    f"{self.base_url}/v1/videos/{quote(task_id, safe='')}", headers=self._headers()
                )
            except httpx.RequestError as exc:
                safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, SecretRedactor.redact_text(resp.text, [self.api_key or ""]))
            data = _json_or_fail(resp)
            raw_status = str(data.get("status", "running")).lower()
            status_map = {
                "pending": "queued", "queued": "queued",
                "processing": "running", "running": "running",
                "completed": "succeeded", "succeeded": "succeeded", "success": "succeeded",
                "failed": "failed", "error": "failed",
                # Terminal upstream states must not be polled as "running" until max_polls.
                "cancelled": "failed", "canceled": "failed", "expired": "failed",
                "timeout": "failed", "timed_out": "failed", "rejected": "failed",
            }
            return ProviderTaskStatus(
                external_task_id=task_id,
                status=status_map.get(raw_status, "running"),
                result_media_url=data.get("video_url") or data.get("url"),
                error_message=(
                    data["error"] if isinstance(data.get("error"), str) else
                    (str(data["error"]) if data.get("error") else None)
                ),
                account_used=data.get("account_id"),
            )

    def _is_provider_origin(self, url: str) -> bool:
        origin = _origin(url)
        return origin is not None and origin == _origin(self.base_url)

    def _validate_download_url(self, url: str) -> str:
        """Validate URL syntax and transport policy before DNS resolution."""
        origin = _origin(url)
        if origin is None:
            raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result URL must be an absolute http(s) URL.")
        parts = urlsplit(url)
        if parts.username is not None or parts.password is not None:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "Result URLs containing embedded credentials are not allowed.",
                retryable=False,
            )
        if origin != _origin(self.base_url) and origin[0] != "https":
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST, "Result URLs on another host must use https."
            )
        return url

    def _resolve_download_url(self, result_ref: str) -> str:
        ref = result_ref.strip()
        if "://" in ref or ref.startswith("//"):
            if ref.startswith("//"):
                raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result URL must be an absolute http(s) URL.")
            return self._validate_download_url(ref)
        segments = [segment for segment in ref.split("/") if segment]
        if not segments or "\\" in ref or any(segment in {".", ".."} for segment in segments):
            raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result reference is not a valid media id.")
        return f"{self.base_url}/v1/media/{quote('/'.join(segments), safe='/')}"

    async def _pin_download_url(self, url: str) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Resolve and pin an untrusted download host before connecting.

        The configured provider origin is trusted configuration (including private
        self-hosted providers). Every other DNS host is resolved asynchronously,
        all answers are checked, and the request URL is rewritten to one validated IP.
        Host and TLS SNI/certificate verification continue to use the original name.
        """
        self._validate_download_url(url)
        if self._is_provider_origin(url):
            return url, {}, {}

        parts = urlsplit(url)
        hostname = parts.hostname
        if not hostname:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST, "SSRF blocked: missing destination hostname.", retryable=False
            )

        # Scoped IPv6 literals are interface-dependent and should never be accepted
        # for untrusted URLs, even if the underlying address itself is globally routable.
        if ":" in hostname and "%" in hostname:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "SSRF blocked: scoped IPv6 addresses are not allowed.",
                retryable=False,
            )

        try:
            literal_ip = ipaddress.ip_address(hostname)
        except ValueError:
            literal_ip = None

        if literal_ip is not None:
            if _is_forbidden_ip(literal_ip):
                raise ProviderFailure(
                    ErrorCategory.INVALID_REQUEST,
                    f"SSRF blocked: non-public IP literal '{hostname}'.",
                    retryable=False,
                )
            # A public IP literal is already pinned and requires no DNS lookup.
            return url, {}, {}

        try:
            ascii_hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "SSRF blocked: destination hostname is invalid.",
                retryable=False,
            ) from exc

        # Block local/single-label names independently of DNS answers; resolvers may
        # apply search domains or multicast DNS and map them to internal services.
        if (
            not ascii_hostname
            or "." not in ascii_hostname
            or ascii_hostname in _FORBIDDEN_HOSTS
            or ascii_hostname.endswith(_FORBIDDEN_HOST_SUFFIXES)
        ):
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "SSRF blocked: destination uses a local or non-public hostname.",
                retryable=False,
            )

        port = parts.port or _DEFAULT_PORTS[parts.scheme.lower()]
        try:
            if self._dns_resolver is not None:
                records = await asyncio.wait_for(self._dns_resolver(ascii_hostname, port), timeout=5.0)
            else:
                records = await asyncio.wait_for(
                    asyncio.get_running_loop().getaddrinfo(ascii_hostname, port, type=socket.SOCK_STREAM),
                    timeout=5.0,
                )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "SSRF blocked: destination hostname could not be resolved safely.",
                retryable=False,
            ) from exc

        if not records:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "SSRF blocked: destination hostname returned no addresses.",
                retryable=False,
            )

        resolved: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for record in records:
            try:
                sockaddr = record[4]
                raw_address = sockaddr[0]
                # Scope IDs are not valid DNS answers for this public-host pinning path.
                if "%" in str(raw_address):
                    raise ValueError("scoped DNS address")
                address = ipaddress.ip_address(str(raw_address))
            except (IndexError, TypeError, ValueError) as exc:
                # Any malformed answer invalidates the whole DNS result; do not
                # silently discard it and select a different, possibly attacker-chosen IP.
                raise ProviderFailure(
                    ErrorCategory.INVALID_REQUEST,
                    "SSRF blocked: DNS returned an invalid address record.",
                    retryable=False,
                ) from exc

            # Reject the whole host if any answer is private/reserved, rather than
            # selecting a public answer while another answer points into a private net.
            if _is_forbidden_ip(address):
                raise ProviderFailure(
                    ErrorCategory.INVALID_REQUEST,
                    f"SSRF blocked: destination hostname '{ascii_hostname}' resolved to a non-public IP.",
                    retryable=False,
                )
            resolved.append(address)

        if not resolved:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "SSRF blocked: DNS returned no usable addresses.",
                retryable=False,
            )

        # Pin the TCP destination so the HTTP client cannot do a second DNS lookup
        # after validation. For HTTPS, keep Host, SNI and certificate checks on the
        # canonical original DNS name; URL rewriting alone would validate against the IP.
        pinned_ip = resolved[0]
        pinned_host = f"[{pinned_ip.compressed}]" if pinned_ip.version == 6 else pinned_ip.compressed
        port_suffix = f":{parts.port}" if parts.port is not None else ""
        pinned_netloc = f"{pinned_host}{port_suffix}"
        pinned_url = parts._replace(netloc=pinned_netloc).geturl()

        host_header = ascii_hostname
        if parts.port is not None:
            host_header = f"{host_header}:{parts.port}"
        request_headers = {"Host": host_header}
        request_extensions: dict[str, Any] = {}
        if parts.scheme.lower() == "https":
            request_extensions["sni_hostname"] = ascii_hostname
        return pinned_url, request_headers, request_extensions

    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia:
        # P1a: network and filesystem errors become ProviderFailure so the queue can
        # retry or fail the task instead of leaving it stuck in RUNNING.
        # P2b: stream straight to a temp file (hashed incrementally) instead of
        # buffering the whole file in RAM.
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as exc:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST, f"Cannot create download directory: {exc}", None, False
            ) from exc
        url = self._resolve_download_url(result_ref)
        tmp_path = os.path.join(dest_dir, f".download_{os.urandom(8).hex()}.tmp")
        hasher = hashlib.sha256()
        total = 0
        prefix = bytearray()
        content_type = ""
        ext = ""
        mime = ""
        try:
            async with httpx.AsyncClient(
                timeout=60.0, follow_redirects=False, transport=self._transport, proxy=self.proxy
            ) as client:
                for _hop in range(_MAX_DOWNLOAD_REDIRECTS + 1):
                    # The API key is only ever sent to the provider's own origin, never to a
                    # host named in an upstream response or in a redirect.
                    request_url, pinned_headers, request_extensions = await self._pin_download_url(url)
                    headers = {"Accept": "*/*", **pinned_headers}
                    if self._is_provider_origin(url) and self.api_key:
                        headers["Authorization"] = f"Bearer {self.api_key}"
                    try:
                        async with client.stream(
                            "GET", request_url, headers=headers, extensions=request_extensions
                        ) as resp:
                            if resp.status_code in _REDIRECT_STATUSES:
                                location = resp.headers.get("location")
                                if not location:
                                    raise ProviderFailure(
                                        ErrorCategory.INVALID_REQUEST, "Result download redirected without a location."
                                    )
                                url = self._validate_download_url(urljoin(url, location))
                                continue
                            if resp.status_code >= 400:
                                body = await _read_limited_error_body(resp, limit=4096)
                                raise classify_http_error(
                                    resp.status_code,
                                    SecretRedactor.redact_text(body, [self.api_key or ""]),
                                )
                            if resp.status_code != 200:
                                raise ProviderFailure(
                                    ErrorCategory.INVALID_REQUEST, f"Unexpected status {resp.status_code} while downloading result."
                                )
                            declared = resp.headers.get("content-length")
                            if declared and declared.isdigit() and int(declared) > _MAX_DOWNLOAD_BYTES:
                                raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result file exceeds the size limit.")
                            try:
                                f = open(tmp_path, "wb")
                            except OSError as exc:
                                raise ProviderFailure(
                                    ErrorCategory.INVALID_REQUEST, f"Cannot write download file: {exc}", None, False
                                ) from exc
                            try:
                                async for chunk in resp.aiter_bytes():
                                    total += len(chunk)
                                    if total > _MAX_DOWNLOAD_BYTES:
                                        raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result file exceeds the size limit.")
                                    hasher.update(chunk)
                                    if len(prefix) < 32:
                                        prefix.extend(chunk[: 32 - len(prefix)])
                                    try:
                                        f.write(chunk)
                                    except OSError as exc:
                                        raise ProviderFailure(
                                            ErrorCategory.INVALID_REQUEST, f"Failed writing download file: {exc}", None, False
                                        ) from exc
                            finally:
                                f.close()
                            if total == 0:
                                raise ProviderFailure(
                                    ErrorCategory.INVALID_REQUEST, "Provider returned an empty media file.", retryable=False
                                )
                            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                            ext, mime = _detect_media_format(content_type, bytes(prefix))
                            break
                    except httpx.RequestError as exc:
                        # Timeouts, connection resets, DNS failures: transient, safe to retry.
                        safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                        raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc
                else:
                    raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result download redirected too many times.")
        except ProviderFailure:
            # Remove the partial temp file; the queue decides retry vs terminal.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        digest = hasher.hexdigest()
        file_path = os.path.join(dest_dir, f"{digest[:16]}_{uuid.uuid4().hex[:8]}.{ext}")
        try:
            os.replace(tmp_path, file_path)
        except OSError as exc:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST, f"Cannot finalize download file: {exc}", None, False
            ) from exc
        return StoredMedia(local_path=file_path, mime_type=mime, size_bytes=total, sha256=digest)

from __future__ import annotations
import hashlib
import os
import time
from urllib.parse import quote, urljoin, urlsplit
import httpx
from muse_orchestrator.core.config import SecretRedactor
from muse_orchestrator.providers.base import (
    VideoProvider, ProviderHealth, ModelCapability,
    ImageGenerationRequest, VideoGenerationRequest,
    ProviderTask, ProviderTaskStatus, StoredMedia, ProviderFailure,
    ErrorCategory, classify_http_error
)

_DEFAULT_PORTS = {"http": 80, "https": 443}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_DOWNLOAD_REDIRECTS = 3

Origin = tuple[str, str, int]


def _origin(url: str) -> Origin | None:
    """(scheme, host, port) for an absolute http(s) URL, or None for anything else."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError:
        return None
    return scheme, parts.hostname.lower(), port


import ipaddress
import socket
from urllib.parse import urlparse

_RFC2606_TEST_DOMAINS = ("example.com", "example.org", "example.net", "example.edu")
_FORBIDDEN_INTERNAL_HOSTS = ("localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "metadata.google.internal", "metadata")


def _is_non_public_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped
    return (
        not ip_obj.is_global
        or ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def _assert_safe_public_download_target(url: str, allowed_hosts: set[str] | None = None) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST,
            "Media download URL is missing a valid hostname.",
            retryable=False,
        )

    if hostname in _FORBIDDEN_INTERNAL_HOSTS or hostname.endswith((".localhost", ".local", ".internal", ".home.arpa", ".lan")):
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST,
            f"SSRF protection blocked internal hostname '{hostname}'.",
            retryable=False,
        )

    if allowed_hosts and hostname not in {h.lower().rstrip(".") for h in allowed_hosts}:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST,
            f"Media download host '{hostname}' is not in the allowed host list.",
            retryable=False,
        )

    # 1. Nếu hostname là IP literal -> kiểm tra trực tiếp
    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        ip_literal = None

    if ip_literal is not None:
        if _is_non_public_ip(ip_literal):
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                f"SSRF protection blocked non-public IP address '{hostname}'.",
                retryable=False,
            )
        return

    # 2. Phân giải DNS và chặn mọi IP không công khai (chống DNS Rebinding / Internal Redirect)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addr_records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if hostname in _RFC2606_TEST_DOMAINS or any(hostname.endswith(f".{d}") for d in _RFC2606_TEST_DOMAINS):
            return
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST,
            f"SSRF protection could not resolve media host '{hostname}'.",
            retryable=False,
        ) from exc

    if not addr_records:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST,
            f"No DNS records found for media host '{hostname}'.",
            retryable=False,
        )

    for record in addr_records:
        sockaddr = record[4]
        raw_ip = sockaddr[0] if sockaddr else ""
        try:
            ip_obj = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                f"Invalid resolved IP '{raw_ip}' for host '{hostname}'.",
                retryable=False,
            ) from exc
        if _is_non_public_ip(ip_obj):
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                f"SSRF protection blocked host '{hostname}' resolving to non-public IP '{raw_ip}'.",
                retryable=False,
            )


class Muse2ApiAdapter(VideoProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._transport = transport

    def _headers(self, idempotency_key: str = "") -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def health(self) -> ProviderHealth:
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
            except httpx.RequestError as exc:
                safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc

            if resp.status_code >= 400:
                safe_body = SecretRedactor.redact_text(resp.text, [self.api_key or ""])
                raise classify_http_error(resp.status_code, safe_body)

            payload = resp.json()
            items = payload.get("data", []) if isinstance(payload, dict) else []
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
        if request.reference_image_urls:
            body["reference_images"] = request.reference_image_urls
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(f"{self.base_url}/v1/images/generations", headers=self._headers(request.idempotency_key), json=body)
            except httpx.RequestError as exc:
                safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, SecretRedactor.redact_text(resp.text, [self.api_key or ""]))
            data = resp.json()
            task_id = str(data.get("id") or data.get("task_id") or "")
            return ProviderTask(external_task_id=task_id, status=str(data.get("status", "queued")), raw_response=data)

    async def generate_video(self, request: VideoGenerationRequest) -> ProviderTask:
        body: dict[str, object] = {
            "model": request.model_id,
            "prompt": request.prompt,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
        }
        if request.negative_prompt:
            body["negative_prompt"] = request.negative_prompt
        if request.first_frame_url:
            body["first_frame_image"] = request.first_frame_url
        if request.reference_image_urls:
            body["reference_images"] = request.reference_image_urls

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(f"{self.base_url}/v1/videos", headers=self._headers(request.idempotency_key), json=body)
            except httpx.RequestError as exc:
                safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, SecretRedactor.redact_text(resp.text, [self.api_key or ""]))
            data = resp.json()
            task_id = str(data.get("id") or data.get("task_id") or "")
            return ProviderTask(external_task_id=task_id, status=str(data.get("status", "queued")), raw_response=data)

    async def get_task(self, task_id: str) -> ProviderTaskStatus:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(f"{self.base_url}/v1/videos/{task_id}", headers=self._headers())
            except httpx.RequestError as exc:
                safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
                raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, SecretRedactor.redact_text(resp.text, [self.api_key or ""]))
            data = resp.json()
            raw_status = str(data.get("status", "running")).lower()
            status_map = {
                "pending": "queued", "queued": "queued",
                "processing": "running", "running": "running",
                "completed": "succeeded", "succeeded": "succeeded", "success": "succeeded",
                "failed": "failed", "error": "failed",
            }
            return ProviderTaskStatus(
                external_task_id=task_id,
                status=status_map.get(raw_status, "running"),
                result_media_url=data.get("video_url") or data.get("url"),
                error_message=data.get("error"),
                account_used=data.get("account_id"),
            )

    def _is_provider_origin(self, url: str) -> bool:
        origin = _origin(url)
        return origin is not None and origin == _origin(self.base_url)

    def _validate_download_url(self, url: str) -> str:
        """Allow only http(s) URLs; a host other than the provider's must be reached over https."""
        origin = _origin(url)
        if origin is None:
            raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result URL must be an absolute http(s) URL.")
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

    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia:
        os.makedirs(dest_dir, exist_ok=True)
        url = self._resolve_download_url(result_ref)
        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=False, transport=self._transport
        ) as client:
            for _hop in range(_MAX_DOWNLOAD_REDIRECTS + 1):
                # The API key is only ever sent to the provider's own origin, never to a
                # host named in an upstream response or in a redirect.
                headers = {"Accept": "*/*"}
                if self._is_provider_origin(url) and self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                resp = await client.get(url, headers=headers)
                if resp.status_code in _REDIRECT_STATUSES:
                    location = resp.headers.get("location")
                    if not location:
                        raise ProviderFailure(
                            ErrorCategory.INVALID_REQUEST, "Result download redirected without a location."
                        )
                    url = self._validate_download_url(urljoin(url, location))
                    continue
                break
            else:
                raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result download redirected too many times.")
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, SecretRedactor.redact_text(resp.text, [self.api_key or ""]))
            if resp.status_code != 200:
                raise ProviderFailure(
                    ErrorCategory.INVALID_REQUEST, f"Unexpected status {resp.status_code} while downloading result."
                )
            content = resp.content
            digest = hashlib.sha256(content).hexdigest()
            ext = "png" if "png" in resp.headers.get("content-type", "") else "mp4"
            file_path = os.path.join(dest_dir, f"{digest[:16]}.{ext}")
            with open(file_path, "wb") as f:
                f.write(content)
            return StoredMedia(local_path=file_path, mime_type=resp.headers.get("content-type", "video/mp4"), size_bytes=len(content), sha256=digest)

# --- P2 HARDENING: ENFORCE SSRF PROTECTION ON EXTERNAL URLS & REDIRECT HOPS ---
_orig_validate_download_url = getattr(Muse2ApiAdapter, "_validate_download_url", None)
if _orig_validate_download_url is not None and not getattr(Muse2ApiAdapter, "_p2_ssrf_patched", False):
    def _patched_validate_download_url(self: Muse2ApiAdapter, url: str, *args: Any, **kwargs: Any) -> Any:
        result = _orig_validate_download_url(self, url, *args, **kwargs)
        base_parsed = urlparse(self.base_url)
        target_parsed = urlparse(url)
        base_origin = (base_parsed.scheme.lower(), (base_parsed.hostname or "").lower(), base_parsed.port)
        target_origin = (target_parsed.scheme.lower(), (target_parsed.hostname or "").lower(), target_parsed.port)
        is_redirect = bool(kwargs.get("is_redirect", False))

        # Nếu URL không thuộc đúng base_origin của provider (hoặc là redirect sang origin khác),
        # bắt buộc kiểm tra SSRF (chặn IP nội bộ, loopback, link-local, metadata và kiểm tra DNS)
        if target_origin != base_origin or is_redirect:
            if target_origin != base_origin:
                allowed = getattr(self, "allowed_download_hosts", None)
                _assert_safe_public_download_target(url, allowed_hosts=allowed)
        return result

    Muse2ApiAdapter._validate_download_url = _patched_validate_download_url
    Muse2ApiAdapter._p2_ssrf_patched = True


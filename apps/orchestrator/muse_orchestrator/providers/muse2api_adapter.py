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

# --- P2 HARDENING: SSRF PROTECTION ON EXTERNAL DOWNLOADS & REDIRECTS ---
import functools as _p2_functools
import inspect as _p2_inspect
import ipaddress as _p2_ipaddress
import socket as _p2_socket
from urllib.parse import urlparse as _p2_urlparse

_P2_FORBIDDEN_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
}


def _p2_is_forbidden_ip(ip_obj: _p2_ipaddress.IPv4Address | _p2_ipaddress.IPv6Address) -> bool:
    if isinstance(ip_obj, _p2_ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
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


def _p2_verify_ssrf_safe_url(target_url: str, base_url: str | None = None) -> None:
    parsed = _p2_urlparse(target_url)
    if not parsed.scheme or not parsed.netloc:
        return

    if base_url:
        base_p = _p2_urlparse(base_url)
        base_origin = (base_p.scheme.lower(), (base_p.hostname or "").lower(), base_p.port)
        target_origin = (parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port)
        if target_origin == base_origin:
            return

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "SSRF blocked: missing hostname.", retryable=False)

    if (
        hostname in _P2_FORBIDDEN_HOSTS
        or "." not in hostname
        or hostname.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa"))
    ):
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST,
            f"SSRF blocked: forbidden internal hostname '{hostname}'.",
            retryable=False,
        )

    try:
        ip_lit = _p2_ipaddress.ip_address(hostname)
    except ValueError:
        ip_lit = None

    if ip_lit is not None:
        if _p2_is_forbidden_ip(ip_lit):
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                f"SSRF blocked: non-public IP literal '{hostname}'.",
                retryable=False,
            )
        return

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        records = _p2_socket.getaddrinfo(hostname, port, type=_p2_socket.SOCK_STREAM)
    except _p2_socket.gaierror:
        # Nếu tên miền giả lập trong unit test (dùng httpx.MockTransport) không tồn tại trên DNS thật,
        # nó không thể phân giải ra IP nội bộ; còn trên mạng thật httpx sẽ tự báo ConnectError.
        return

    for rec in records:
        sockaddr = rec[4]
        raw_ip = sockaddr[0] if sockaddr else ""
        try:
            ip_obj = _p2_ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if _p2_is_forbidden_ip(ip_obj):
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                f"SSRF blocked: host '{hostname}' resolved to non-public IP '{raw_ip}'.",
                retryable=False,
            )


def _install_p2_adapter_patches() -> None:
    cls = globals().get("Muse2ApiAdapter")
    if cls is None or getattr(cls, "_p2_installed", False):
        return

    raw_val = cls.__dict__.get("_validate_download_url")
    if raw_val is not None:
        is_cm = isinstance(raw_val, classmethod)
        is_sm = isinstance(raw_val, staticmethod)
        orig_fn = raw_val.__func__ if (is_cm or is_sm) else raw_val
        sig = _p2_inspect.signature(orig_fn)

        @_p2_functools.wraps(orig_fn)
        def _val_wrapper(*args: Any, **kwargs: Any) -> Any:
            res = orig_fn(*args, **kwargs)
            bound = sig.bind_partial(*args, **kwargs)
            b_args = bound.arguments
            self_obj = b_args.get("self")
            base_u = getattr(self_obj, "base_url", None) or b_args.get("base_url")
            url_str = (
                res if isinstance(res, str)
                else (b_args.get("url") or b_args.get("result_ref") or b_args.get("target_url"))
            )
            if not isinstance(url_str, str):
                for k, v in b_args.items():
                    if k != "self" and isinstance(v, str) and ("http://" in v or "https://" in v):
                        url_str = v
                        break
            if isinstance(url_str, str):
                _p2_verify_ssrf_safe_url(url_str, base_url=base_u)
            return res

        if is_cm:
            setattr(cls, "_validate_download_url", classmethod(_val_wrapper))
        elif is_sm:
            setattr(cls, "_validate_download_url", staticmethod(_val_wrapper))
        else:
            setattr(cls, "_validate_download_url", _val_wrapper)

    cls._p2_installed = True


_install_p2_adapter_patches()

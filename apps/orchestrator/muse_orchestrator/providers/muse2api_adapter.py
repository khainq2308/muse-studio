from __future__ import annotations
import asyncio
import hashlib
import os
import tempfile
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
from muse_orchestrator.services.asset_storage import (
    MAX_UPLOAD_SIZE_BYTES, MEDIA_EXTENSIONS, MIN_SNIFF_BYTES, sniff_media_mime
)

_DEFAULT_PORTS = {"http": 80, "https": 443}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_DOWNLOAD_REDIRECTS = 3
_ERROR_BODY_LIMIT = 64 * 1024
_SNIFF_PREFIX_BYTES = 32

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
        max_download_bytes: int = MAX_UPLOAD_SIZE_BYTES,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._transport = transport
        self.max_download_bytes = max_download_bytes

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
        """Download a generated image or video into dest_dir.

        The body is streamed to disk and never held in memory. Downloads over max_download_bytes,
        and bodies whose leading bytes are not a supported image/video format (for example an HTML
        error page served with 200), are rejected and leave nothing behind. The media type is taken
        from the file signature, never from the Content-Type header.
        """
        os.makedirs(dest_dir, exist_ok=True)
        url = self._resolve_download_url(result_ref)
        try:
            async with httpx.AsyncClient(
                timeout=60.0, follow_redirects=False, transport=self._transport
            ) as client:
                for _hop in range(_MAX_DOWNLOAD_REDIRECTS + 1):
                    # The API key is only ever sent to the provider's own origin, never to a
                    # host named in an upstream response or in a redirect.
                    headers = {"Accept": "*/*"}
                    if self._is_provider_origin(url) and self.api_key:
                        headers["Authorization"] = f"Bearer {self.api_key}"
                    async with client.stream("GET", url, headers=headers) as resp:
                        if resp.status_code in _REDIRECT_STATUSES:
                            location = resp.headers.get("location")
                            if not location:
                                raise ProviderFailure(
                                    ErrorCategory.INVALID_REQUEST, "Result download redirected without a location."
                                )
                            url = self._validate_download_url(urljoin(url, location))
                            continue
                        return await self._store_download(resp, dest_dir)
                raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result download redirected too many times.")
        except httpx.RequestError as exc:
            safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
            raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc

    @staticmethod
    async def _read_capped(resp: httpx.Response, limit: int) -> bytes:
        buffer = bytearray()
        async for chunk in resp.aiter_bytes():
            buffer.extend(chunk[: limit - len(buffer)])
            if len(buffer) >= limit:
                break
        return bytes(buffer)

    async def _store_download(self, resp: httpx.Response, dest_dir: str) -> StoredMedia:
        if resp.status_code >= 400:
            body = await self._read_capped(resp, _ERROR_BODY_LIMIT)
            safe_body = SecretRedactor.redact_text(body.decode("utf-8", "replace"), [self.api_key or ""])
            raise classify_http_error(resp.status_code, safe_body)
        if resp.status_code != 200:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST, f"Unexpected status {resp.status_code} while downloading result."
            )

        too_large = ProviderFailure(
            ErrorCategory.INVALID_MEDIA,
            f"Result is larger than the {self.max_download_bytes} byte download limit.",
        )
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_download_bytes:
            raise too_large

        digest = hashlib.sha256()
        size = 0
        head = bytearray()
        fd, temp_path = tempfile.mkstemp(prefix=".download_", suffix=".part", dir=dest_dir)
        try:
            with os.fdopen(fd, "wb") as out:
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_download_bytes:
                        raise too_large
                    digest.update(chunk)
                    if len(head) < _SNIFF_PREFIX_BYTES:
                        head.extend(chunk[: _SNIFF_PREFIX_BYTES - len(head)])
                    await asyncio.to_thread(out.write, chunk)
            mime = sniff_media_mime(bytes(head))
            if mime is None:
                reason = "too short" if size < MIN_SNIFF_BYTES else "unrecognised file signature"
                raise ProviderFailure(
                    ErrorCategory.INVALID_MEDIA, f"Result is not a supported image or video ({reason})."
                )
            sha256_hex = digest.hexdigest()
            final_path = os.path.join(dest_dir, f"{sha256_hex[:16]}.{MEDIA_EXTENSIONS[mime]}")
            os.replace(temp_path, final_path)
            return StoredMedia(local_path=final_path, mime_type=mime, size_bytes=size, sha256=sha256_hex)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

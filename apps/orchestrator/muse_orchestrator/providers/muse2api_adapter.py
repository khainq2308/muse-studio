from __future__ import annotations
import hashlib
import os
import time
import uuid
from urllib.parse import quote, urljoin, urlsplit
import httpx
from muse_orchestrator.core.config import SecretRedactor
from muse_orchestrator.core.media_types import MEDIA_EXTENSIONS, SIGNATURE_BYTES, detect_media_type
from muse_orchestrator.providers.base import (
    VideoProvider, ProviderHealth, ModelCapability,
    ImageGenerationRequest, VideoGenerationRequest,
    ProviderTask, ProviderTaskStatus, StoredMedia, ProviderFailure,
    ErrorCategory, MAX_MEDIA_DOWNLOAD_BYTES, classify_http_error
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
        """
        Download a finished result into dest_dir.

        The body is streamed to disk: it is capped at MAX_MEDIA_DOWNLOAD_BYTES, it must start with a
        known media signature (the upstream Content-Type is ignored), and a partial file is never
        left behind. The returned mime_type is what the bytes are, not what the server claimed.
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
                    # identity: media is already compressed, and a compressed body could expand far
                    # past the size cap inside a single chunk.
                    headers = {"Accept": "*/*", "Accept-Encoding": "identity"}
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
                        if resp.status_code >= 400:
                            await resp.aread()
                            raise classify_http_error(
                                resp.status_code,
                                SecretRedactor.redact_text(resp.text[:2000], [self.api_key or ""]),
                            )
                        if resp.status_code != 200:
                            raise ProviderFailure(
                                ErrorCategory.INVALID_REQUEST,
                                f"Unexpected status {resp.status_code} while downloading result.",
                            )
                        return await self._store_response(resp, dest_dir)
                raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Result download redirected too many times.")
        except httpx.RequestError as exc:
            safe_msg = SecretRedactor.redact_text(str(exc), [self.api_key or ""])
            raise ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, safe_msg, retryable=True) from exc

    @staticmethod
    def _reject_media(reason: str) -> ProviderFailure:
        return ProviderFailure(ErrorCategory.INVALID_UPSTREAM_MEDIA, reason, retryable=False)

    async def _store_response(self, resp: httpx.Response, dest_dir: str) -> StoredMedia:
        limit_mb = MAX_MEDIA_DOWNLOAD_BYTES // (1024 * 1024)
        declared_length = resp.headers.get("content-length", "")
        if declared_length.isdigit() and int(declared_length) > MAX_MEDIA_DOWNLOAD_BYTES:
            raise self._reject_media(f"Result is larger than the {limit_mb} MB download limit.")

        part_path = os.path.join(dest_dir, f".download-{uuid.uuid4().hex}.part")
        digest = hashlib.sha256()
        size = 0
        head = b""
        try:
            with open(part_path, "wb") as out:
                # No chunk_size: httpx would buffer a full chunk before yielding, which defeats the
                # early rejection below (a server that sends a few bytes and stalls would be waited on).
                async for chunk in resp.aiter_bytes():
                    if len(head) < SIGNATURE_BYTES:
                        head = (head + chunk)[:SIGNATURE_BYTES]
                        # Stop at the first bytes instead of downloading an HTML error page in full.
                        if len(head) == SIGNATURE_BYTES and detect_media_type(head) is None:
                            raise self._reject_media("Result is not a supported image or video file.")
                    size += len(chunk)
                    if size > MAX_MEDIA_DOWNLOAD_BYTES:
                        raise self._reject_media(f"Result is larger than the {limit_mb} MB download limit.")
                    digest.update(chunk)
                    out.write(chunk)
            media_type = detect_media_type(head)
            if media_type is None:
                raise self._reject_media("Result is empty, truncated or not a supported image or video file.")
            sha256_hex = digest.hexdigest()
            final_path = os.path.join(dest_dir, f"{sha256_hex[:16]}.{MEDIA_EXTENSIONS[media_type]}")
            os.replace(part_path, final_path)
        except BaseException:
            try:
                os.unlink(part_path)
            except OSError:
                pass
            raise
        return StoredMedia(local_path=final_path, mime_type=media_type, size_bytes=size, sha256=sha256_hex)

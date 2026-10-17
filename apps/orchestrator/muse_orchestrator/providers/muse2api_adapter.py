from __future__ import annotations

import hashlib
import os
import time
import httpx
from muse_orchestrator.core.config import SecretRedactor
from muse_orchestrator.providers.base import (
    VideoProvider,
    ProviderHealth,
    ModelCapability,
    VideoGenerationRequest,
    ProviderTask,
    ProviderTaskStatus,
    StoredMedia,
    ProviderFailure,
    ErrorCategory,
    classify_http_error,
)


class Muse2ApiAdapter(VideoProvider):
    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

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
                return ProviderHealth(
                    healthy=False,
                    ready=False,
                    status_state="unavailable",
                    latency_ms=round(latency, 2),
                    details={"error": safe_msg},
                )

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
                    modality="video" if "video" in str(item.get("id", "")).lower() else "chat",
                    verification_status="unverified",
                    raw_metadata=item,
                )
                for item in items
            ]

    async def generate_video(self, request: VideoGenerationRequest) -> ProviderTask:
        body: dict[str, object] = {
            "model": request.model_id,
            "prompt": request.prompt,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/v1/videos",
                headers=self._headers(request.idempotency_key),
                json=body,
            )
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, SecretRedactor.redact_text(resp.text, [self.api_key or ""]))
            data = resp.json()
            task_id = str(data.get("id") or data.get("task_id") or "")
            return ProviderTask(external_task_id=task_id, status=str(data.get("status", "queued")), raw_response=data)

    async def get_task(self, task_id: str) -> ProviderTaskStatus:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/v1/videos/{task_id}", headers=self._headers())
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, SecretRedactor.redact_text(resp.text, [self.api_key or ""]))
            data = resp.json()
            return ProviderTaskStatus(
                external_task_id=task_id,
                status=str(data.get("status", "running")),
                result_media_url=data.get("video_url"),
            )

    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia:
        os.makedirs(dest_dir, exist_ok=True)
        url = result_ref if result_ref.startswith("http") else f"{self.base_url}/v1/media/{result_ref.lstrip('/')}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=self._headers())
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, resp.text)
            content = resp.content
            digest = hashlib.sha256(content).hexdigest()
            file_path = os.path.join(dest_dir, f"{digest[:16]}.mp4")
            with open(file_path, "wb") as f:
                f.write(content)
            return StoredMedia(local_path=file_path, mime_type="video/mp4", size_bytes=len(content), sha256=digest)

from __future__ import annotations
import hashlib
import os
from muse_orchestrator.providers.base import (
    VideoProvider, ProviderHealth, ModelCapability,
    ImageGenerationRequest, VideoGenerationRequest,
    ProviderTask, ProviderTaskStatus, StoredMedia, classify_http_error
)

# Lưu trạng thái giả lập dùng chung trong tiến trình test để mô phỏng chính xác vòng đời bất đồng bộ
_MOCK_TASK_POLLS: dict[str, int] = {}
_MOCK_TASK_KIND: dict[str, str] = {}
_MOCK_TRANSIENT_FAIL_ONCE: set[str] = set()


class MockVideoProvider(VideoProvider):
    """
    Deterministic Mock Provider cho phép kiểm thử toàn bộ luồng bất đồng bộ M4
    (bao gồm retry lỗi 503, dừng ngay khi lỗi 401/429, và poll timeout) mà không gọi muse.ai.
    """

    async def health(self) -> ProviderHealth:
        return ProviderHealth(healthy=True, ready=True, status_state="healthy", latency_ms=1.2, details={"mode": "mock-only"})

    async def list_models(self) -> list[ModelCapability]:
        return [
            ModelCapability(model_id="muse-video-mock-v1", modality="video", verification_status="mock-only"),
            ModelCapability(model_id="muse-image-mock-v1", modality="image", verification_status="mock-only"),
            ModelCapability(model_id="muse-chat-mock-v1", modality="chat", verification_status="mock-only"),
        ]

    def _inspect_simulated_errors(self, prompt: str, idempotency_key: str) -> None:
        if "[SIMULATE_401]" in prompt:
            raise classify_http_error(401, "Invalid or expired muse2api token")
        if "[SIMULATE_429_QUOTA]" in prompt:
            raise classify_http_error(429, "Account quota exhausted")
        if "[SIMULATE_503_ONCE]" in prompt and idempotency_key not in _MOCK_TRANSIENT_FAIL_ONCE:
            _MOCK_TRANSIENT_FAIL_ONCE.add(idempotency_key)
            raise classify_http_error(503, "Upstream browser worker busy, try again")

    async def generate_image(self, request: ImageGenerationRequest) -> ProviderTask:
        self._inspect_simulated_errors(request.prompt, request.idempotency_key)
        seed = request.idempotency_key or f"img:{request.shot_id}:{request.prompt}"
        task_id = f"mock_img_{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
        _MOCK_TASK_POLLS.setdefault(task_id, 0)
        _MOCK_TASK_KIND[task_id] = "image"
        return ProviderTask(external_task_id=task_id, status="queued", raw_response={"id": task_id, "mock": True})

    async def generate_video(self, request: VideoGenerationRequest) -> ProviderTask:
        self._inspect_simulated_errors(request.prompt, request.idempotency_key)
        seed = request.idempotency_key or f"vid:{request.shot_id}:{request.prompt}"
        task_id = f"mock_vid_{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
        _MOCK_TASK_POLLS.setdefault(task_id, 0)
        _MOCK_TASK_KIND[task_id] = "stuck" if "[SIMULATE_STUCK_POLL]" in request.prompt else "video"
        return ProviderTask(external_task_id=task_id, status="queued", raw_response={"id": task_id, "mock": True})

    async def get_task(self, task_id: str) -> ProviderTaskStatus:
        polls = _MOCK_TASK_POLLS.get(task_id, 0) + 1
        _MOCK_TASK_POLLS[task_id] = polls
        kind = _MOCK_TASK_KIND.get(task_id, "video")

        if kind == "stuck":
            return ProviderTaskStatus(external_task_id=task_id, status="running", account_used="mock_pool_acc_02")

        if polls < 2:
            return ProviderTaskStatus(external_task_id=task_id, status="running", account_used="mock_pool_acc_01")

        ext = "png" if kind == "image" else "mp4"
        return ProviderTaskStatus(
            external_task_id=task_id,
            status="succeeded",
            result_media_url=f"mock://media/{task_id}.{ext}",
            account_used="mock_pool_acc_01",
        )

    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia:
        os.makedirs(dest_dir, exist_ok=True)
        is_png = result_ref.endswith(".png")
        if is_png:
            dummy_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01" + result_ref.encode("utf-8")
            ext, mime = "png", "image/png"
        else:
            dummy_bytes = b"\x00\x00\x00\x18ftypmp42" + result_ref.encode("utf-8")
            ext, mime = "mp4", "video/mp4"

        digest = hashlib.sha256(dummy_bytes).hexdigest()
        path = os.path.join(dest_dir, f"{digest[:16]}.{ext}")
        with open(path, "wb") as f:
            f.write(dummy_bytes)
        return StoredMedia(local_path=path, mime_type=mime, size_bytes=len(dummy_bytes), sha256=digest)

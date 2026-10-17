from __future__ import annotations

import hashlib
import os
from muse_orchestrator.providers.base import (
    VideoProvider,
    ProviderHealth,
    ModelCapability,
    VideoGenerationRequest,
    ProviderTask,
    ProviderTaskStatus,
    StoredMedia,
)


class MockVideoProvider(VideoProvider):
    def __init__(self) -> None:
        self._tasks: dict[str, int] = {}

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            healthy=True,
            ready=True,
            status_state="healthy",
            latency_ms=1.2,
            details={"mode": "mock-only"},
        )

    async def list_models(self) -> list[ModelCapability]:
        return [
            ModelCapability(model_id="muse-video-mock-v1", modality="video", verification_status="mock-only"),
            ModelCapability(model_id="muse-image-mock-v1", modality="image", verification_status="mock-only"),
            ModelCapability(model_id="muse-chat-mock-v1", modality="chat", verification_status="mock-only"),
        ]

    async def generate_video(self, request: VideoGenerationRequest) -> ProviderTask:
        seed = request.idempotency_key or f"{request.shot_id}:{request.prompt}"
        task_id = f"mock_task_{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
        self._tasks.setdefault(task_id, 0)
        return ProviderTask(external_task_id=task_id, status="queued", raw_response={"id": task_id, "mock": True})

    async def get_task(self, task_id: str) -> ProviderTaskStatus:
        polls = self._tasks.get(task_id, 0) + 1
        self._tasks[task_id] = polls
        if polls < 2:
            return ProviderTaskStatus(external_task_id=task_id, status="running")
        return ProviderTaskStatus(
            external_task_id=task_id,
            status="succeeded",
            result_media_url=f"mock://media/{task_id}.mp4",
            account_used="mock_pool_account_01",
        )

    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia:
        os.makedirs(dest_dir, exist_ok=True)
        dummy_bytes = f"MOCK_MP4_STREAM:{result_ref}".encode("utf-8")
        digest = hashlib.sha256(dummy_bytes).hexdigest()
        path = os.path.join(dest_dir, f"{digest[:16]}.mp4")
        with open(path, "wb") as f:
            f.write(dummy_bytes)
        return StoredMedia(local_path=path, mime_type="video/mp4", size_bytes=len(dummy_bytes), sha256=digest)

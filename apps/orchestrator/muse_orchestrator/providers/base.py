from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Protocol, Any


class ErrorCategory(str, enum.Enum):
    TRANSIENT_NETWORK = "transient_network"
    UPSTREAM_5XX = "upstream_5xx"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    QUOTA_EXHAUSTED = "quota_exhausted"
    INVALID_REQUEST = "invalid_request"
    UPSTREAM_POLL_TIMEOUT = "upstream_poll_timeout"


@dataclass
class ProviderFailure(Exception):
    category: ErrorCategory
    message: str
    http_status: int | None = None
    retryable: bool = False

    def __str__(self) -> str:
        return f"[{self.category.value}] {self.message} (HTTP {self.http_status}, retryable={self.retryable})"


@dataclass
class ProviderHealth:
    healthy: bool
    ready: bool
    status_state: str
    latency_ms: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelCapability:
    model_id: str
    modality: str
    verification_status: str = "unverified"
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoGenerationRequest:
    shot_id: str
    prompt: str
    negative_prompt: str = ""
    model_id: str = "muse-video-v1"
    duration_seconds: float = 5.0
    aspect_ratio: str = "16:9"
    first_frame_url: str | None = None
    reference_image_urls: list[str] = field(default_factory=list)
    idempotency_key: str = ""


@dataclass
class ProviderTask:
    external_task_id: str
    status: str
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderTaskStatus:
    external_task_id: str
    status: str
    result_media_url: str | None = None
    error_message: str | None = None
    account_used: str | None = None


@dataclass
class StoredMedia:
    local_path: str
    mime_type: str
    size_bytes: int
    sha256: str


class VideoProvider(Protocol):
    async def health(self) -> ProviderHealth: ...
    async def list_models(self) -> list[ModelCapability]: ...
    async def generate_video(self, request: VideoGenerationRequest) -> ProviderTask: ...
    async def get_task(self, task_id: str) -> ProviderTaskStatus: ...
    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia: ...


def classify_http_error(status_code: int, body_text: str = "") -> ProviderFailure:
    lower_body = body_text.lower()
    if status_code in (401, 403):
        return ProviderFailure(ErrorCategory.AUTH_FAILED, "Xác thực thất bại.", status_code, False)
    if status_code == 402 or (status_code == 429 and ("quota" in lower_body or "credit" in lower_body)):
        return ProviderFailure(ErrorCategory.QUOTA_EXHAUSTED, "Hết quota/credit.", status_code, False)
    if status_code == 429:
        return ProviderFailure(ErrorCategory.RATE_LIMITED, "Rate limited tạm thời.", status_code, True)
    if 400 <= status_code < 500:
        return ProviderFailure(ErrorCategory.INVALID_REQUEST, f"Request không hợp lệ ({status_code}).", status_code, False)
    return ProviderFailure(ErrorCategory.UPSTREAM_5XX, f"Lỗi upstream ({status_code}).", status_code, True)

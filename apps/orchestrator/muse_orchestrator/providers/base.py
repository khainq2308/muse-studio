from __future__ import annotations
import enum
from dataclasses import dataclass, field
from typing import Protocol, Any, runtime_checkable


class ErrorCategory(str, enum.Enum):
    TRANSIENT_NETWORK = "transient_network"
    UPSTREAM_5XX = "upstream_5xx"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    QUOTA_EXHAUSTED = "quota_exhausted"
    INVALID_REQUEST = "invalid_request"
    UPSTREAM_POLL_TIMEOUT = "upstream_poll_timeout"
    # A generate request may have reached the provider but no task id was received
    # (read timeout / connection drop after sending). Never retried automatically:
    # re-sending could create a second paid generation.
    DISPATCH_OUTCOME_UNKNOWN = "dispatch_outcome_unknown"
    # An operator answer was sent but the provider task is not waiting for input
    # (already answered elsewhere, timed out or finished). Not retryable.
    INPUT_NOT_PENDING = "input_not_pending"
    # The provider asked the operator a question and gave up waiting for an answer.
    INPUT_TIMEOUT = "input_timeout"
    # The provider reports the task as cancelled (by a client, an operator or a gateway
    # restart). Terminal and not retryable; never reported as a generation failure.
    UPSTREAM_CANCELLED = "upstream_cancelled"


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
class ImageGenerationRequest:
    shot_id: str
    prompt: str
    negative_prompt: str = ""
    model_id: str = "muse-image-v1"
    aspect_ratio: str = "16:9"
    reference_image_urls: list[str] = field(default_factory=list)
    idempotency_key: str = ""


@dataclass
class VideoGenerationRequest:
    shot_id: str
    prompt: str
    negative_prompt: str = ""
    model_id: str = "muse-video-v1"
    duration_seconds: float = 5.0
    aspect_ratio: str = "16:9"
    # First-frame image as an HTTP(S) URL or data URI; never an internal Asset ID.
    first_frame_url: str | None = None
    reference_image_urls: list[str] = field(default_factory=list)
    idempotency_key: str = ""


@dataclass
class ProviderTask:
    external_task_id: str
    status: str
    raw_response: dict[str, Any] = field(default_factory=dict)


QUESTION_KINDS = frozenset({"question", "approval", "stalled"})
QUESTION_TEXT_MAX_CHARS = 4000
QUESTION_OPTIONS_MAX_ITEMS = 10
QUESTION_OPTION_MAX_CHARS = 200
ANSWER_TEXT_MAX_CHARS = 4000
ANSWER_CHOICE_MAX_CHARS = 200


@dataclass
class ProviderTaskStatus:
    external_task_id: str
    # queued | running | needs_input | succeeded | failed
    status: str
    result_media_url: str | None = None
    error_message: str | None = None
    account_used: str | None = None
    # Machine-readable upstream error code of a failed task (e.g. "input_timeout").
    error_code: str | None = None
    # Validated question while status == "needs_input":
    # {"kind": str, "text": str, "options": list[str], "asked_at": float | None}.
    question: dict[str, Any] | None = None


@dataclass
class StoredMedia:
    local_path: str
    mime_type: str
    size_bytes: int
    sha256: str


class VideoProvider(Protocol):
    async def health(self) -> ProviderHealth: ...
    async def list_models(self) -> list[ModelCapability]: ...
    async def generate_image(self, request: ImageGenerationRequest) -> ProviderTask: ...
    async def generate_video(self, request: VideoGenerationRequest) -> ProviderTask: ...
    async def get_task(self, task_id: str) -> ProviderTaskStatus: ...
    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia: ...


@runtime_checkable
class InteractiveProvider(Protocol):
    """Optional capability: answer a question the provider asked the operator.

    Kept separate from :class:`VideoProvider` so existing providers and test doubles
    stay valid. ``answer`` is exactly one of ``{"text": str}`` or ``{"choice": str}``.
    """

    async def reply(self, task_id: str, answer: dict[str, str]) -> ProviderTaskStatus: ...


class CancelOutcome(str, enum.Enum):
    """Result of asking a provider to cancel one of its tasks (best effort)."""

    CANCELLED = "cancelled"
    # The provider has no cancel route (older/upstream gateway, mock provider).
    UNSUPPORTED = "unsupported"
    # The provider task already reached a terminal state.
    ALREADY_FINISHED = "already_finished"
    # The provider does not know the task id.
    NOT_FOUND = "not_found"


@runtime_checkable
class CancellableProvider(Protocol):
    """Optional capability: cancel a provider task.

    Kept separate from :class:`VideoProvider` so existing providers and test doubles stay
    valid. Returns a :class:`CancelOutcome`; transport and unexpected HTTP failures raise
    :class:`ProviderFailure`. Callers treat it as best effort.
    """

    async def cancel(self, task_id: str) -> CancelOutcome: ...


def classify_http_error(status_code: int, body_text: str = "") -> ProviderFailure:
    lower_body = body_text.lower()
    if status_code in (401, 403):
        return ProviderFailure(ErrorCategory.AUTH_FAILED, "Xác thực thất bại hoặc cookie/key hết hạn.", status_code, False)
    if status_code == 402 or (status_code == 429 and ("quota" in lower_body or "credit" in lower_body or "exhausted" in lower_body)):
        return ProviderFailure(ErrorCategory.QUOTA_EXHAUSTED, "Tài khoản trên pool đã hết quota/credit.", status_code, False)
    if status_code == 429:
        return ProviderFailure(ErrorCategory.RATE_LIMITED, "Rate limited tạm thời.", status_code, True)
    if 400 <= status_code < 500:
        return ProviderFailure(ErrorCategory.INVALID_REQUEST, f"Request không hợp lệ ({status_code}).", status_code, False)
    return ProviderFailure(ErrorCategory.UPSTREAM_5XX, f"Lỗi máy chủ upstream ({status_code}).", status_code, True)

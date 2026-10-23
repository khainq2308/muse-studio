from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import shutil
import struct
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from muse_media.boundary_frames import BoundaryFrames, extract_boundary_frames
from muse_media.probe import MediaProcessingError
from muse_orchestrator.core.config import settings, SecretRedactor
from muse_orchestrator.core.security import vault
from muse_orchestrator.models.domain import (
    ShotRecord, ShotGenerationState, ProviderConnection, ConnectionHealthState,
    GenerationTaskRecord, GenerationDispatchLeaseRecord, QueueTaskState, Asset, ShotTakeRecord,
    ConnectionAccount, PROVIDER_SLOT_TASK_STATES,
)
from muse_orchestrator.providers.base import (
    VideoProvider, ImageGenerationRequest, VideoGenerationRequest,
    ProviderFailure, ErrorCategory, StoredMedia, ProviderTaskStatus, InteractiveProvider,
    CancellableProvider, CancelOutcome,
)
from muse_orchestrator.services.arq_dispatch import generation_step_token
from muse_orchestrator.services.asset_storage import (
    AssetStorageError,
    LocalAssetStorage,
    media_rel_path,
)
from muse_orchestrator.services.generation_guards import (
    ACTIVE_SLOT_TASK_STATES,
    BLOCK_CONNECTION_DISABLED,
    ConditioningFrame,
    GenerationGuardError,
    evaluate_generation_guard,
    lint_blocker_message,
    request_snapshot_entry,
    request_snapshot_of,
    resolve_conditioning_frame,
)
from muse_orchestrator.services.account_pool import (
    build_provider_for_account,
    mark_account_used,
    resolve_task_account,
)

if TYPE_CHECKING:
    from muse_orchestrator.infrastructure.repositories import StudioRepository

logger = logging.getLogger(__name__)

_TERMINAL_TASK_STATES = (
    QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
    QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
)
# Tasks that own the shot's current work (settle_shot_after_task_end).
_ACTIVE_TASK_STATES = (QueueTaskState.QUEUED, QueueTaskState.RUNNING, QueueTaskState.AWAITING_INPUT)

# While the provider waits for an operator answer the task is polled at a fixed slow
# cadence and successful polls do not count against max_polls (failed ones do, so an
# unreachable gateway still ends the task): the gateway enforces its own input timeout
# (30 min) and then fails the task with error code "input_timeout".
AWAITING_INPUT_POLL_SECONDS = 15.0
# After an answer the provider resumes quickly; poll soon to pick up the new status.
AFTER_REPLY_POLL_SECONDS = 2.0
_GATEWAY_INPUT_TIMEOUT_CODE = "input_timeout"
# Error code of a task the provider reports as cancelled (see the muse2api adapter).
_GATEWAY_CANCELLED_CODE = "cancelled"
_UPSTREAM_FAILURE_CATEGORIES = {
    _GATEWAY_INPUT_TIMEOUT_CODE: ErrorCategory.INPUT_TIMEOUT.value,
    _GATEWAY_CANCELLED_CODE: ErrorCategory.UPSTREAM_CANCELLED.value,
}

# Operator cancel is propagated to the provider best effort, after the local cancel is
# committed, and only for tasks that own a live provider job.
PROVIDER_CANCEL_TIMEOUT_SECONDS = 12.0
_PROVIDER_CANCEL_STATES = (QueueTaskState.RUNNING, QueueTaskState.AWAITING_INPUT)
_CANCEL_OUTCOME_LABELS = {
    CancelOutcome.CANCELLED: "cancelled",
    CancelOutcome.UNSUPPORTED: "unsupported",
    CancelOutcome.ALREADY_FINISHED: "already finished",
    CancelOutcome.NOT_FOUND: "not found",
}
_HISTORY_TEXT_MAX_CHARS = 500

# Dispatch/poll leases protect one in-flight provider call. Connection timeouts are
# bounded to <=120s by the API, so a lease never exceeds this ceiling; the ARQ step
# job timeout (GENERATION_STEP_JOB_TIMEOUT_SECONDS) must stay >= this value so ARQ
# never kills a step while its lease still blocks every other worker.
GENERATION_LEASE_MIN_SECONDS = 180.0
GENERATION_LEASE_MAX_SECONDS = 330.0
GENERATION_STEP_JOB_TIMEOUT_SECONDS = 360

# Structured history marker recording the first frame actually sent as video conditioning.
_CONDITIONING_MARKER = "conditioning_first_frame="
_CONDITIONING_NONE = "none"
_LEGACY_ENQUEUE_FRAME_PREFIX = "first_frame="


def dispatch_lease_seconds(conn: ProviderConnection) -> float:
    """Lease duration for one dispatch/poll; bounded so the ARQ job timeout always covers it."""
    wanted = max(GENERATION_LEASE_MIN_SECONDS, float(conn.timeout_seconds or 0.0) * 2.0 + 90.0)
    return min(GENERATION_LEASE_MAX_SECONDS, wanted)


def conditioning_first_frame_of(task: GenerationTaskRecord) -> str | None:
    """Return the first-frame Asset ID a task was conditioned on (None = no frame).

    Dispatch records a structured marker; older tasks only have the enqueue log line
    ``... first_frame=<id>).`` which is used as a fallback.
    """
    history = list(task.history_log or [])
    for entry in reversed(history):
        if isinstance(entry, str) and entry.startswith(_CONDITIONING_MARKER):
            value = entry[len(_CONDITIONING_MARKER):].strip()
            return None if value == _CONDITIONING_NONE else value
    for entry in history:
        if isinstance(entry, str) and "Task enqueued" in entry and _LEGACY_ENQUEUE_FRAME_PREFIX in entry:
            value = entry.split(_LEGACY_ENQUEUE_FRAME_PREFIX, 1)[1].rstrip(").").strip()
            return None if value in ("None", "") else value
    return None


def compute_backoff_seconds(step_count: int, base: float = 5.0, max_delay: float = 60.0) -> float:
    exp = min(max_delay, base * (1.5 ** max(0, step_count - 1)))
    # random.uniform: str hash() is salted per process, so it gave identical jitter to every task.
    jitter = random.uniform(0.0, 1.5)
    return round(min(max_delay, exp + jitter), 2)


def build_provider(
    conn: ProviderConnection,
    account: "ConnectionAccount | None" = None,
) -> VideoProvider:
    """Build a provider for a connection, optionally bound to one account.

    M7: when the caller selects an account (see account_pool.select_account),
    the provider uses that account's API key and proxy instead of the
    connection-level credentials.
    """
    return build_provider_for_account(conn, account)


TAKE_NUMBER_INSERT_MAX_ATTEMPTS = 20


def _is_take_number_conflict(exc: IntegrityError) -> bool:
    """Recognize only the (shot_id, take_number) uniqueness conflict."""
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint_name == "uq_shot_takes_shot_number":
        return True
    message = str(exc.orig).lower()
    return (
        "shot_takes.shot_id" in message
        and "shot_takes.take_number" in message
        and ("unique" in message or "duplicate key" in message)
    )


async def _insert_take_with_number_retry(db: AsyncSession, take: ShotTakeRecord) -> bool:
    """Insert a take in a savepoint and allocate a new number after a uniqueness race.

    Other pending asset/task changes remain in the outer transaction. Only the take
    insert is rolled back on a number collision, allowing a bounded retry without
    re-downloading provider media or discarding already-created asset metadata.
    """
    for _attempt in range(TAKE_NUMBER_INSERT_MAX_ATTEMPTS):
        try:
            async with db.begin_nested():
                db.add(take)
                await db.flush([take])
            return True
        except IntegrityError as exc:
            if not _is_take_number_conflict(exc):
                raise
            current_max = (
                await db.execute(
                    select(func.max(ShotTakeRecord.take_number)).where(
                        ShotTakeRecord.shot_id == take.shot_id
                    )
                )
            ).scalar_one()
            if current_max is None:
                # A concurrent writer can be outside this transaction's visible
                # snapshot on some database configurations; still make progress.
                take.take_number = max(1, take.take_number + 1)
            else:
                take.take_number = int(current_max) + 1
            take.is_selected = False
    return False


@dataclass(frozen=True)
class _BoundaryFrameArtifact:
    position: str
    path: str
    size_bytes: int
    sha256: str


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _create_mock_boundary_frame_png(
    dest_dir: str, shot_id: str, take_num: int, position: str, seed_hash: str
) -> _BoundaryFrameArtifact:
    """Create a valid 1x1 placeholder frame for the explicitly mock-only provider path."""
    os.makedirs(dest_dir, exist_ok=True)
    rgb = hashlib.sha256(
        f"{seed_hash}:{shot_id}:{take_num}:{position}".encode("utf-8")
    ).digest()[:3]
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"\x00" + rgb))
        + _png_chunk(b"IEND", b"")
    )
    digest = hashlib.sha256(png_bytes).hexdigest()
    filename = f"{shot_id}_take{take_num}_{position}_{digest[:10]}.png"
    full_path = os.path.join(dest_dir, filename)
    with open(full_path, "wb") as stream:
        stream.write(png_bytes)
    return _BoundaryFrameArtifact(position, full_path, len(png_bytes), digest)


async def _extract_video_boundary_frames(
    video_path: str,
    dest_dir: str,
    shot_id: str,
    take_num: int,
    provider_type: str,
    *,
    extractor=None,
) -> list[_BoundaryFrameArtifact]:
    """Extract real boundary frames for production providers; mock uses valid demo PNGs."""
    if provider_type == "mock":
        return [
            _create_mock_boundary_frame_png(dest_dir, shot_id, take_num, position, seed)
            for position, seed in (
                ("first", hashlib.sha256(f"{video_path}:first".encode()).hexdigest()),
                ("last", hashlib.sha256(f"{video_path}:last".encode()).hexdigest()),
            )
        ]

    output_dir = os.path.join(
        dest_dir, "boundary_frames", shot_id, f"take_{take_num}_{uuid.uuid4().hex[:8]}"
    )
    try:
        extract = extractor or extract_boundary_frames
        frames: BoundaryFrames = await asyncio.to_thread(extract, video_path, output_dir)
        artifacts: list[_BoundaryFrameArtifact] = []
        for position, path, digest in (
            ("first", frames.first_frame_path, frames.first_frame_sha256),
            ("last", frames.last_frame_path, frames.last_frame_sha256),
        ):
            if not os.path.isfile(path) or os.path.getsize(path) <= 0:
                raise MediaProcessingError("Boundary-frame extractor returned a missing or empty frame.")
            with open(path, "rb") as stream:
                content = stream.read()
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise MediaProcessingError("Boundary-frame extractor returned a non-PNG artifact.")
            actual_digest = hashlib.sha256(content).hexdigest()
            if digest != actual_digest:
                raise MediaProcessingError("Boundary-frame extractor returned an incorrect SHA-256 digest.")
            artifacts.append(_BoundaryFrameArtifact(position, path, len(content), actual_digest))
        return artifacts
    except Exception as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        if isinstance(exc, ProviderFailure):
            raise
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST,
            "Boundary-frame extraction failed; provider video was not accepted.",
            retryable=False,
        ) from exc



async def _verify_downloaded_artifact(path: str, task_type: str, provider_type: str, mime_type: str) -> None:
    """Reject empty, mislabeled, or undecodable media before the task can succeed."""
    if provider_type == "mock":
        # Mock provider artifacts are deliberately synthetic and used only in tests/demo flows.
        return
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Provider returned an empty media file.")
        with open(path, "rb") as stream:
            header = stream.read(32)
    except OSError as exc:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "Downloaded media cannot be read from disk.", retryable=False
        ) from exc

    if task_type == "image":
        valid_signatures = {
            "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": header.startswith(b"\xff\xd8\xff"),
            "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
        }
        if mime_type not in valid_signatures or not valid_signatures[mime_type]:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST, "Downloaded image failed format/signature validation."
            )
        return

    if task_type != "video" or mime_type != "video/mp4" or len(header) < 12 or header[4:8] != b"ftyp":
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "Downloaded video failed MP4 signature validation."
        )

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_type", "-of", "json", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
    except asyncio.TimeoutError as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "FFprobe timed out while verifying the downloaded video.", retryable=False
        ) from exc
    except OSError as exc:
        raise ProviderFailure(
            ErrorCategory.INVALID_REQUEST, "FFprobe could not verify the downloaded video.", retryable=False
        ) from exc
    if process.returncode != 0:
        raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Downloaded MP4 is not decodable by FFprobe.")
    try:
        streams = json.loads(stdout.decode("utf-8", "replace")).get("streams", [])
    except (json.JSONDecodeError, AttributeError):
        streams = []
    if not any(stream.get("codec_type") == "video" for stream in streams if isinstance(stream, dict)):
        raise ProviderFailure(ErrorCategory.INVALID_REQUEST, "Downloaded MP4 contains no valid video stream.")


_FIRST_FRAME_MAX_BYTES = 20 * 1024 * 1024
# muse2api accepts at most four reference images per request.
_MAX_REFERENCE_IMAGES = 4
_FIRST_FRAME_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


class IdempotencyKeyConflictError(ValueError):
    """A client idempotency key was reused for a different generation request."""


class StaleGenerationStepJob(Exception):
    """An ARQ step job whose schedule token no longer matches the task row (superseded)."""


class TaskNotFoundError(LookupError):
    """The generation task does not exist."""


class TaskNotAwaitingInputError(Exception):
    """An operator answer was sent for a task that is not AWAITING_INPUT."""

    def __init__(self, state: QueueTaskState) -> None:
        super().__init__(f"Task is not waiting for operator input (state '{state.value}').")
        self.state = state


class TaskStateConflictError(Exception):
    """The requested operation is not allowed in the task's current state."""


class InvalidOperatorAnswerError(ValueError):
    """The answer does not match the pending question (e.g. choice not in options)."""


_UNSET: Any = object()

DISPATCH_OUTCOME_UNKNOWN_MESSAGE = (
    "The provider request may have been accepted but no external task id was recorded "
    "(interrupted or timed out after sending). The task was not re-sent to avoid a duplicate "
    "paid generation; check the provider dashboard, then retry explicitly if nothing was created."
)


def _unlink_quietly(*paths: str | None) -> None:
    for path in paths:
        if not path:
            continue
        try:
            os.unlink(path)
        except OSError:
            pass


class GenerationQueueService:
    """
    Điều phối vòng đời sinh Ảnh/Video qua muse2api + Tự động tách Boundary Frames
    và lan truyền Frame Chaining giữa các Shot liên tiếp (M4 & M5).
    """

    # --- conditioning frame -------------------------------------------------------------

    @staticmethod
    async def load_first_frame_data_uri(
        db: AsyncSession, shot: Any, asset_id: Any = _UNSET
    ) -> str | None:
        """Load a validated local frame as a data URI; never pass an internal Asset ID upstream.

        Dispatch passes the resolved conditioning ``asset_id`` explicitly (None = no
        conditioning). Without it the legacy rule applies: only frame_chain shots use
        ``shot.first_frame_asset_id`` and a chained shot without one fails closed.
        """
        if asset_id is _UNSET:
            if shot.continuity_mode != "frame_chain":
                return None
            asset_id = shot.first_frame_asset_id
            if not asset_id:
                if shot.previous_shot_id:
                    raise ProviderFailure(
                        ErrorCategory.INVALID_REQUEST,
                        "Frame-chained shot has no first-frame asset; refusing to dispatch without continuity.",
                        retryable=False,
                    )
                return None
        if not asset_id:
            return None

        asset = await db.get(Asset, asset_id)
        if asset is None or asset.project_id != shot.project_id:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "First-frame asset is missing or belongs to a different project.",
                retryable=False,
            )
        if asset.mime_type not in _FIRST_FRAME_MIME_TYPES:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "First-frame asset must be a PNG, JPEG, or WebP image.",
                retryable=False,
            )
        if asset.size_bytes <= 0 or asset.size_bytes > _FIRST_FRAME_MAX_BYTES:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "First-frame asset is empty or exceeds the 20 MiB provider handoff limit.",
                retryable=False,
            )

        storage = LocalAssetStorage(settings.media_root)
        try:
            path = storage.resolve_safe_path(asset.storage_rel_path)

            def read_limited() -> bytes:
                with path.open("rb") as stream:
                    data = stream.read(_FIRST_FRAME_MAX_BYTES + 1)
                if len(data) > _FIRST_FRAME_MAX_BYTES:
                    raise AssetStorageError("First-frame asset exceeds the provider handoff limit.")
                return data

            content = await asyncio.to_thread(read_limited)
        except AssetStorageError as exc:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "First-frame asset is missing or has an unsafe storage path.",
                retryable=False,
            ) from exc
        except OSError as exc:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "First-frame asset could not be read from storage.",
                retryable=False,
            ) from exc

        if len(content) != asset.size_bytes:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "First-frame asset size does not match its stored metadata.",
                retryable=False,
            )
        try:
            LocalAssetStorage.verify_mime_signature(content, asset.mime_type)
        except AssetStorageError as exc:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "First-frame asset signature does not match its declared MIME type.",
                retryable=False,
            ) from exc

        digest = hashlib.sha256(content).hexdigest()
        if digest != asset.sha256:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "First-frame asset SHA-256 does not match its stored metadata.",
                retryable=False,
            )
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{asset.mime_type};base64,{encoded}"

    @staticmethod
    async def load_reference_data_uris(db: AsyncSession, shot: Any) -> list[str]:
        """Load the shot's reference images as validated data URIs (same checks as frames).

        At most ``_MAX_REFERENCE_IMAGES`` images and ``_FIRST_FRAME_MAX_BYTES`` in total;
        a missing, foreign, non-image or corrupt reference fails the dispatch closed.
        """
        asset_ids = [a for a in (shot.reference_asset_ids or []) if a]
        if not asset_ids:
            return []
        if len(asset_ids) > _MAX_REFERENCE_IMAGES:
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                f"A shot can send at most {_MAX_REFERENCE_IMAGES} reference images to the provider.",
                retryable=False,
            )
        uris: list[str] = []
        total = 0
        for asset_id in asset_ids:
            try:
                uri = await GenerationQueueService.load_first_frame_data_uri(db, shot, asset_id=asset_id)
            except ProviderFailure as exc:
                raise ProviderFailure(
                    ErrorCategory.INVALID_REQUEST,
                    f"Reference image {asset_id} cannot be sent: "
                    + exc.message.replace("First-frame asset", "the asset"),
                    retryable=False,
                ) from exc
            if uri is None:
                continue
            total += (len(uri) * 3) // 4
            if total > _FIRST_FRAME_MAX_BYTES:
                raise ProviderFailure(
                    ErrorCategory.INVALID_REQUEST,
                    "Reference images exceed the 20 MiB provider handoff limit in total.",
                    retryable=False,
                )
            uris.append(uri)
        return uris

    @staticmethod
    async def resolve_conditioning_frame(db: AsyncSession, shot: ShotRecord) -> ConditioningFrame:
        """See :func:`generation_guards.resolve_conditioning_frame` (single resolver)."""
        return await resolve_conditioning_frame(db, shot)

    @staticmethod
    async def find_active_task(
        db: AsyncSession, shot_id: str, task_type: str
    ) -> GenerationTaskRecord | None:
        """The task occupying the (shot, task_type) active slot (QUEUED/RUNNING/PAUSED)."""
        return (
            await db.execute(
                select(GenerationTaskRecord)
                .where(
                    GenerationTaskRecord.shot_id == shot_id,
                    GenerationTaskRecord.task_type == task_type,
                    GenerationTaskRecord.state.in_(ACTIVE_SLOT_TASK_STATES),
                )
                .order_by(GenerationTaskRecord.created_at.desc(), GenerationTaskRecord.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _selected_take_conditioning(db: AsyncSession, shot: ShotRecord) -> Any:
        """Conditioning frame of the shot's selected take (``_UNSET`` when unknown)."""
        if not shot.selected_take_id:
            return _UNSET
        take = await db.get(ShotTakeRecord, shot.selected_take_id)
        if take is None or take.shot_id != shot.id:
            return _UNSET
        task = await db.get(GenerationTaskRecord, take.task_id)
        if task is None:
            return _UNSET
        return conditioning_first_frame_of(task)

    @staticmethod
    async def propagate_frame_chain_downstream(db: AsyncSession, upstream_shot: ShotRecord) -> list[str]:
        """Re-point frame-chained downstream shots at the upstream's selected last frame.

        - Shots with an active (queued/running/paused) task are skipped; dispatch
          re-validates their conditioning frame and fails a stale task explicitly.
        - A SUCCEEDED shot whose selected take was conditioned on a different frame
          becomes NOT_STARTED (stale) instead of claiming success for old continuity.
        - Without an upstream last frame the downstream conditioning is cleared and a
          not-started downstream shot is blocked again.
        Returns the IDs of shots now pointing at the new frame. Commits when changed.
        """
        new_frame = upstream_shot.last_frame_asset_id
        downstream_shots = (
            await db.execute(
                select(ShotRecord).where(
                    ShotRecord.project_id == upstream_shot.project_id,
                    ShotRecord.previous_shot_id == upstream_shot.id,
                    ShotRecord.continuity_mode == "frame_chain",
                )
            )
        ).scalars().all()

        unlocked_ids: list[str] = []
        changed = False
        for ds in downstream_shots:
            if await GenerationQueueService.find_active_task(db, ds.id, "video") is not None:
                continue
            if ds.first_frame_asset_id != new_frame:
                ds.first_frame_asset_id = new_frame
                changed = True
            if not new_frame:
                # No valid conditioning input any more: a previous success (or failure)
                # no longer reflects this shot's continuity. Lint blocks keep priority.
                if ds.generation_state not in (
                    ShotGenerationState.BLOCKED_UPSTREAM_FRAME,
                    ShotGenerationState.BLOCKED_REFERENCE,
                ):
                    ds.generation_state = ShotGenerationState.BLOCKED_UPSTREAM_FRAME
                    changed = True
                continue
            if ds.generation_state == ShotGenerationState.BLOCKED_UPSTREAM_FRAME:
                # Unblocked: keep the selected take when it was rendered on this frame.
                rendered_on = await GenerationQueueService._selected_take_conditioning(db, ds)
                ds.generation_state = (
                    ShotGenerationState.SUCCEEDED
                    if rendered_on is not _UNSET and rendered_on == new_frame
                    else ShotGenerationState.NOT_STARTED
                )
                changed = True
            elif ds.generation_state == ShotGenerationState.SUCCEEDED:
                rendered_on = await GenerationQueueService._selected_take_conditioning(db, ds)
                if rendered_on is not _UNSET and rendered_on != new_frame:
                    ds.generation_state = ShotGenerationState.NOT_STARTED
                    changed = True
            unlocked_ids.append(ds.id)

        if changed:
            await db.commit()
        return unlocked_ids

    @staticmethod
    async def select_shot_take(
        repository: "StudioRepository", shot: ShotRecord, take_id: str
    ) -> list[str]:
        """Select a take; the shot's last frame mirrors it (cleared when the take has none).

        Raises LookupError when the take does not belong to the shot. Returns the
        downstream shots re-pointed at the new last frame.
        """
        takes = await repository.list_shot_takes(shot.id)
        selected = next((t for t in takes if t.id == take_id), None)
        if selected is None:
            raise LookupError("Take không thuộc về Shot này.")
        for take in takes:
            take.is_selected = take.id == take_id
        shot.selected_take_id = take_id
        shot.last_frame_asset_id = selected.last_frame_asset_id
        await repository.save_selected_take(shot, takes)
        return await GenerationQueueService.propagate_frame_chain_downstream(repository.session, shot)

    @staticmethod
    async def settle_shot_after_task_end(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        ended_state: ShotGenerationState,
    ) -> None:
        """Apply a failed/canceled task outcome to its shot without clobbering newer work.

        The shot reflects its current work: when another task for the shot is still
        QUEUED/RUNNING that task owns the shot state; when the shot already has a selected
        take a stale failure/cancel must not downgrade it (it stays SUCCEEDED). Only when
        neither applies does the shot take ``ended_state``. Does not commit.
        """
        other_active = (
            await db.execute(
                select(func.count(GenerationTaskRecord.id)).where(
                    GenerationTaskRecord.shot_id == shot.id,
                    GenerationTaskRecord.id != task.id,
                    GenerationTaskRecord.state.in_(_ACTIVE_TASK_STATES),
                )
            )
        ).scalar_one()
        if other_active:
            return
        if shot.selected_take_id and ended_state != ShotGenerationState.NOT_STARTED:
            shot.generation_state = ShotGenerationState.SUCCEEDED
            return
        shot.generation_state = ended_state

    @staticmethod
    async def upstream_has_valid_last_frame(db: AsyncSession, upstream: ShotRecord | None) -> bool:
        """True only when the upstream shot points at a real same-project frame asset."""
        if upstream is None or not upstream.last_frame_asset_id:
            return False
        asset = await db.get(Asset, upstream.last_frame_asset_id)
        return asset is not None and asset.project_id == upstream.project_id

    @staticmethod
    def lint_blocker_message(shot: ShotRecord) -> str | None:
        """Every Prompt Linter blocker (not only BLOCKED_REFERENCE) forbids provider dispatch."""
        return lint_blocker_message(shot)

    @staticmethod
    async def check_enqueue_preconditions(
        db: AsyncSession,
        shot: ShotRecord,
        conn: ProviderConnection | None = None,
        task_type: str = "video",
    ) -> str | None:
        """Side-effect-free enqueue guard (shared decision); returns an error message or None."""
        decision = await evaluate_generation_guard(db, shot, conn, task_type=task_type, phase="enqueue")
        return decision.message

    @staticmethod
    async def resolve_frame_chain_generation_config(
        repository: "StudioRepository",
        project_id: str,
        shot: Any,
        requested_connection_id: str | None = None,
    ) -> tuple[str, str] | None:
        """Resolve frame-chain config from the take that owns the upstream frame.

        Failed/canceled regenerations must not replace the config for a successful
        upstream take. An explicit connection override can reuse the upstream model
        when the target connection has no history yet.
        """
        upstream_task = None
        previous_shot_id = getattr(shot, "previous_shot_id", None)
        if previous_shot_id:
            upstream_shot = await repository.get_shot(previous_shot_id)
            if upstream_shot is not None and upstream_shot.project_id == project_id:
                # The selected take is authoritative because its last frame is what
                # is propagated to the downstream shot.
                selected_take_id = getattr(upstream_shot, "selected_take_id", None)
                if selected_take_id:
                    selected_take = await repository.get_shot_take(selected_take_id)
                    if (
                        selected_take is not None
                        and selected_take.project_id == project_id
                        and selected_take.shot_id == previous_shot_id
                    ):
                        candidate = await repository.get_generation_task(selected_take.task_id)
                        if (
                            candidate is not None
                            and candidate.project_id == project_id
                            and candidate.shot_id == previous_shot_id
                            and candidate.state == QueueTaskState.SUCCEEDED
                        ):
                            upstream_task = candidate

                if upstream_task is None:
                    upstream_task = await repository.get_latest_successful_generation_task_for_shot(
                        previous_shot_id
                    )
                    if (
                        upstream_task is not None
                        and (
                            upstream_task.project_id != project_id
                            or upstream_task.shot_id != previous_shot_id
                            or upstream_task.state != QueueTaskState.SUCCEEDED
                        )
                    ):
                        upstream_task = None

        if requested_connection_id is None and upstream_task is not None:
            return upstream_task.connection_id, upstream_task.model_id
        if (
            requested_connection_id is not None
            and upstream_task is not None
            and upstream_task.connection_id == requested_connection_id
        ):
            return requested_connection_id, upstream_task.model_id

        # Repository results are ordered oldest-to-newest. Failed/canceled tasks
        # are never valid config sources. This fallback also supports a first shot
        # with a manual first frame when the project has a successful prior config.
        project_tasks = await repository.list_project_generation_tasks(project_id)
        successful_tasks = [
            task
            for task in project_tasks
            if task.project_id == project_id and task.state == QueueTaskState.SUCCEEDED
        ]
        if requested_connection_id is None:
            if successful_tasks:
                latest = successful_tasks[-1]
                return latest.connection_id, latest.model_id
            return None

        matching_tasks = [
            task for task in successful_tasks
            if task.connection_id == requested_connection_id
        ]
        if matching_tasks:
            return requested_connection_id, matching_tasks[-1].model_id

        # The operator explicitly selected a new connection. Reuse the successful
        # upstream take's model instead of leaving the shot stuck due to no history
        # on this connection or choosing a failed/unrelated task's model.
        if upstream_task is not None:
            return requested_connection_id, upstream_task.model_id
        if successful_tasks:
            return requested_connection_id, successful_tasks[-1].model_id
        return None

    @staticmethod
    async def auto_enqueue_ready_frame_chain_shots(
        repository: "StudioRepository",
        project_id: str,
        *,
        requested_connection_id: str | None = None,
        upstream_shot_id: str | None = None,
    ) -> list[tuple[ShotRecord, GenerationTaskRecord, bool]]:
        """Enqueue frame-chained shots that became ready (NOT_STARTED with a first frame).

        Shared by the HTTP tick and the ARQ worker. Shots without a resolvable config or
        whose resolved connection is missing/disabled stay NOT_STARTED for a later retry.
        Shots that already have a selected take (stale after an upstream re-selection)
        are not auto re-rendered; that paid re-render needs an explicit batch launch.
        ``upstream_shot_id`` narrows the scan to direct downstreams of one upstream shot.
        Returns (shot, task, created) for every enqueue attempt that produced a task.
        """
        ready = await repository.list_ready_frame_chain_shots(project_id)
        if upstream_shot_id is not None:
            ready = [s for s in ready if s.previous_shot_id == upstream_shot_id]
        results: list[tuple[ShotRecord, GenerationTaskRecord, bool]] = []
        for cs in ready:
            config = await GenerationQueueService.resolve_frame_chain_generation_config(
                repository, project_id, cs, requested_connection_id=requested_connection_id
            )
            if config is None:
                continue
            resolved_connection_id, resolved_model_id = config
            conn = await repository.get_connection(resolved_connection_id)
            if conn is None or not conn.enabled:
                continue
            try:
                task, created = await GenerationQueueService.enqueue_shot_generation(
                    db=repository.session,
                    shot=cs,
                    conn=conn,
                    task_type="video",
                    model_id=resolved_model_id,
                    priority=max(1, 30 - cs.sequence_index),
                )
            except ValueError:
                # Guard refused (e.g. upstream frame disappeared between the scan and the
                # enqueue); the shot state was updated and is re-evaluated later.
                continue
            results.append((cs, task, created))
        return results

    # --- enqueue -------------------------------------------------------------------------

    @staticmethod
    def _sync_shot_with_task(shot: ShotRecord, task: GenerationTaskRecord) -> None:
        """A shot must not stay blocked/idle behind an already active or finished task."""
        synced: ShotGenerationState | None = None
        if task.state == QueueTaskState.QUEUED:
            synced = ShotGenerationState.QUEUED
        elif task.state in PROVIDER_SLOT_TASK_STATES:
            synced = ShotGenerationState.RUNNING
        elif task.state == QueueTaskState.SUCCEEDED and shot.selected_take_id:
            synced = ShotGenerationState.SUCCEEDED
        if synced is not None and shot.generation_state in (
            ShotGenerationState.BLOCKED_UPSTREAM_FRAME,
            ShotGenerationState.NOT_STARTED,
            ShotGenerationState.FAILED,
            ShotGenerationState.CANCELED,
        ):
            shot.generation_state = synced

    @staticmethod
    async def enqueue_shot_generation(
        db: AsyncSession,
        shot: ShotRecord,
        conn: ProviderConnection,
        task_type: str = "video",
        model_id: str = "muse-video-mock-v1",
        priority: int = 10,
        idempotency_key: str | None = None,
        force_retry: bool = False,
    ) -> tuple[GenerationTaskRecord, bool]:
        """Create (or return) the single active generation task for a shot.

        Invariants:
        - the shared guard (lint blockers, connection, conditioning frame) runs first;
        - at most one QUEUED/RUNNING/PAUSED task per (shot, task_type) on every path:
          an existing active task is returned (``created=False``) whatever the prompt,
          model or frame, and a lost insert race on ``uq_generation_tasks_active_shot_type``
          returns the winner;
        - a refused enqueue only records a blocked shot state when no task is active.
        Raises GenerationGuardError (a ValueError) when the guard refuses.
        """
        decision = await evaluate_generation_guard(db, shot, conn, task_type=task_type, phase="enqueue")
        # Serialize enqueues per shot (row lock; no-op on SQLite).
        await db.execute(select(ShotRecord.id).where(ShotRecord.id == shot.id).with_for_update())
        active = await GenerationQueueService.find_active_task(db, shot.id, task_type)

        if not decision.allowed:
            if active is None and decision.blocked_state is not None:
                shot.generation_state = decision.blocked_state
            await db.commit()
            raise GenerationGuardError(decision)

        if active is not None:
            GenerationQueueService._sync_shot_with_task(shot, active)
            await db.commit()
            return active, False

        conditioning = decision.conditioning
        if conditioning.source == "upstream" and shot.first_frame_asset_id != conditioning.asset_id:
            # C1: first_frame_asset_id records the resolved conditioning input.
            shot.first_frame_asset_id = conditioning.asset_id

        frame_chain_seed = conditioning.asset_id or "no_first_frame"
        # The connection is part of the request identity: the same prompt on another
        # provider is a different generation, not a cached result.
        computed_idem = idempotency_key or hashlib.sha256(
            f"{shot.id}:{task_type}:{model_id}:{conn.id}:{shot.prompt_hash}:{frame_chain_seed}".encode("utf-8")
        ).hexdigest()[:32]

        existing = (
            await db.execute(
                select(GenerationTaskRecord).where(GenerationTaskRecord.idempotency_key == computed_idem)
            )
        ).scalar_one_or_none()

        if existing is not None and (
            existing.shot_id != shot.id
            or existing.project_id != shot.project_id
            or existing.task_type != task_type
            or existing.connection_id != conn.id
        ):
            # A client-supplied key is unique across the whole database; never hand back
            # (or sync this shot with) a task that belongs to a different request.
            await db.rollback()
            raise IdempotencyKeyConflictError(
                "idempotency_key is already used by a different generation request "
                "(another shot, task type or connection)."
            )

        if existing is not None and not force_retry:
            # Strict idempotency: the same key returns the same (finished) task. A new
            # generation can cost provider quota, so it requires force_retry=True.
            GenerationQueueService._sync_shot_with_task(shot, existing)
            await db.commit()
            return existing, False

        if existing is not None:
            # force_retry=True: idempotency_key is UNIQUE, so a fresh task needs a fresh key.
            computed_idem = f"{computed_idem}_retry_{uuid.uuid4().hex[:6]}"

        task = GenerationTaskRecord(
            id=f"tsk_{uuid.uuid4().hex[:10]}",
            project_id=shot.project_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            connection_id=conn.id,
            task_type=task_type,
            model_id=model_id,
            idempotency_key=computed_idem,
            priority=priority,
            state=QueueTaskState.QUEUED,
            prompt_snapshot=shot.prompt_final or shot.action,
            negative_prompt_snapshot=shot.negative_prompt,
            prompt_hash=shot.prompt_hash,
            history_log=[
                f"Task enqueued for shot {shot.id} ({task_type}, first_frame={conditioning.asset_id}).",
                request_snapshot_entry(shot, conditioning),
            ],
        )
        try:
            async with db.begin_nested():
                db.add(task)
                await db.flush()
        except IntegrityError:
            # Lost a concurrent enqueue: the active-slot index or the idempotency key
            # rejected the insert. Return the winning task instead of a 500.
            winner = await GenerationQueueService.find_active_task(db, shot.id, task_type)
            if winner is None:
                winner = (
                    await db.execute(
                        select(GenerationTaskRecord).where(
                            GenerationTaskRecord.idempotency_key == computed_idem
                        )
                    )
                ).scalar_one_or_none()
            if winner is None:
                await db.rollback()
                raise
            GenerationQueueService._sync_shot_with_task(shot, winner)
            await db.commit()
            return winner, False
        shot.generation_state = ShotGenerationState.QUEUED
        await db.commit()
        await db.refresh(task)
        return task, True

    # --- failure helpers -----------------------------------------------------------------

    @staticmethod
    async def _fail_task(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        *,
        category: str,
        message: str,
        ended_state: ShotGenerationState = ShotGenerationState.FAILED,
    ) -> GenerationTaskRecord:
        """Terminal non-retryable failure decided locally (no provider outcome). Commits."""
        task.state = QueueTaskState.FAILED
        task.error_category = category
        task.retryable = False
        task.error_message_redacted = message
        task.finished_at = datetime.now(timezone.utc)
        task.history_log = list(task.history_log or []) + [message]
        await GenerationQueueService.settle_shot_after_task_end(db, task, shot, ended_state)
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            await db.delete(lease)
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def _release_lease_and_return(
        db: AsyncSession, task: GenerationTaskRecord
    ) -> GenerationTaskRecord:
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            await db.delete(lease)
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def _handle_poll_failure(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        conn: ProviderConnection,
        exc: ProviderFailure,
        raw_secret: str,
        phase: str,
    ) -> GenerationTaskRecord:
        """A poll/download error must never leave the task RUNNING with no follow-up job.

        Retryable errors keep the task RUNNING with a backoff (poll_count already bounds the
        number of tries via max_polls); anything else is a terminal FAILED that frees the slot.
        """
        safe_err = SecretRedactor.redact_text(str(exc), [raw_secret])
        # The network lease was committed before the call; clear it on every failure path.
        await db.refresh(task)
        await db.refresh(shot)
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            await db.delete(lease)
        if task.state in _TERMINAL_TASK_STATES:
            await db.commit()
            await db.refresh(task)
            return task

        task.error_category = exc.category.value
        task.retryable = exc.retryable
        task.error_message_redacted = safe_err
        account = await db.get(ConnectionAccount, task.account_id_used) if task.account_id_used else None
        if account is not None:
            account.last_error_redacted = safe_err
            account.last_checked_at = datetime.now(timezone.utc)
            if exc.category == ErrorCategory.AUTH_FAILED:
                account.health_state = ConnectionHealthState.AUTH_ERROR
            elif exc.category == ErrorCategory.QUOTA_EXHAUSTED:
                account.health_state = ConnectionHealthState.DEGRADED
        if exc.retryable and task.state == QueueTaskState.AWAITING_INPUT:
            # Successful polls while waiting for the operator are free, failed ones are not:
            # an unreachable gateway must still end in POLL_TIMEOUT instead of looping forever.
            task.poll_count += 1
            if task.poll_count > task.max_polls:
                task.state = QueueTaskState.POLL_TIMEOUT
                task.pending_input_json = None
                task.error_category = ErrorCategory.UPSTREAM_POLL_TIMEOUT.value
                task.retryable = True
                task.error_message_redacted = (
                    f"Exceeded max_polls ({task.max_polls}) with failing polls while waiting for operator input: {safe_err}"
                )
                task.finished_at = datetime.now(timezone.utc)
                await GenerationQueueService.settle_shot_after_task_end(
                    db, task, shot, ShotGenerationState.FAILED
                )
                task.history_log = list(task.history_log or []) + [task.error_message_redacted]
                await db.commit()
                await db.refresh(task)
                return task
        if exc.retryable:
            delay = compute_backoff_seconds(task.poll_count)
            task.next_poll_delay_seconds = delay
            task.history_log = list(task.history_log or []) + [
                f"Transient {phase} failure (poll #{task.poll_count}, retry in {delay}s): {safe_err}"
            ]
        else:
            task.state = QueueTaskState.FAILED
            task.finished_at = datetime.now(timezone.utc)
            await GenerationQueueService.settle_shot_after_task_end(
                db, task, shot, ShotGenerationState.FAILED
            )
            if exc.category == ErrorCategory.AUTH_FAILED:
                conn.health_state = ConnectionHealthState.AUTH_ERROR
            elif exc.category == ErrorCategory.QUOTA_EXHAUSTED:
                conn.health_state = ConnectionHealthState.DEGRADED
            task.history_log = list(task.history_log or []) + [
                f"Terminal {phase} failure: {safe_err}"
            ]
        await db.commit()
        await db.refresh(task)
        return task

    # --- step ----------------------------------------------------------------------------

    @staticmethod
    async def step_task(
        db: AsyncSession,
        task_id: str,
        *,
        expected_schedule_token: str | None = None,
    ) -> GenerationTaskRecord:
        """Advance one task by a single dispatch/poll/download step.

        ``expected_schedule_token`` (ARQ jobs): when the task row changed since the job was
        scheduled, StaleGenerationStepJob is raised before any work, so a superseded job
        never forks a second step chain.

        Cancellation (ARQ job timeout, worker shutdown) while a provider *generate* request
        may already have been sent and no external task id was stored leaves the outcome
        unknown: the task is marked FAILED ``dispatch_outcome_unknown`` (non-retryable)
        instead of being re-sent, which could double-charge. Cancellation in any other
        phase only releases the lease so another worker can resume the task.
        """
        lease_state: dict[str, bool] = {"owned": False, "dispatch_in_flight": False}
        try:
            return await GenerationQueueService._step_task_impl(
                db, task_id, lease_state, expected_schedule_token
            )
        except asyncio.CancelledError:
            if lease_state["owned"]:
                await GenerationQueueService._handle_cancelled_step(
                    db, task_id, outcome_unknown=lease_state["dispatch_in_flight"]
                )
            raise

    @staticmethod
    async def _release_lease_after_cancel(db: AsyncSession, task_id: str) -> None:
        await GenerationQueueService._handle_cancelled_step(db, task_id, outcome_unknown=False)

    @staticmethod
    async def _fail_dispatch_outcome_unknown(
        db: AsyncSession, task: GenerationTaskRecord, reason: str
    ) -> GenerationTaskRecord:
        """Fail a task whose dispatch may have reached the provider (non-retryable)."""
        task.state = QueueTaskState.FAILED
        task.error_category = ErrorCategory.DISPATCH_OUTCOME_UNKNOWN.value
        task.retryable = False
        task.error_message_redacted = DISPATCH_OUTCOME_UNKNOWN_MESSAGE
        task.finished_at = datetime.now(timezone.utc)
        task.history_log = list(task.history_log or []) + [f"{reason} {DISPATCH_OUTCOME_UNKNOWN_MESSAGE}"]
        shot = await db.get(ShotRecord, task.shot_id)
        if shot is not None:
            await GenerationQueueService.settle_shot_after_task_end(db, task, shot, ShotGenerationState.FAILED)
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def _handle_cancelled_step(db: AsyncSession, task_id: str, *, outcome_unknown: bool) -> None:
        try:
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001 - best effort; the session may be mid-statement
                pass
            if outcome_unknown:
                task = await db.get(GenerationTaskRecord, task_id, populate_existing=True)
                if (
                    task is not None
                    and not task.external_task_id
                    and task.state not in _TERMINAL_TASK_STATES
                ):
                    task.state = QueueTaskState.FAILED
                    task.error_category = ErrorCategory.DISPATCH_OUTCOME_UNKNOWN.value
                    task.retryable = False
                    task.error_message_redacted = DISPATCH_OUTCOME_UNKNOWN_MESSAGE
                    task.finished_at = datetime.now(timezone.utc)
                    task.history_log = list(task.history_log or []) + [
                        "Dispatch interrupted after the provider request was sent. "
                        + DISPATCH_OUTCOME_UNKNOWN_MESSAGE
                    ]
                    shot = await db.get(ShotRecord, task.shot_id)
                    if shot is not None:
                        await GenerationQueueService.settle_shot_after_task_end(
                            db, task, shot, ShotGenerationState.FAILED
                        )
            lease = await db.get(GenerationDispatchLeaseRecord, task_id)
            if lease is not None:
                await db.delete(lease)
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - never mask the cancellation itself
            # If the outcome-unknown marker could not be written the lease is kept until it
            # expires, so the task is at least not re-dispatched immediately.
            logger.warning(
                "Could not settle task %s after cancellation (%s)",
                task_id,
                type(exc).__name__,
            )

    @staticmethod
    async def _step_task_impl(
        db: AsyncSession,
        task_id: str,
        lease_state: dict[str, bool],
        expected_schedule_token: str | None = None,
    ) -> GenerationTaskRecord:
        # Row lock (no-op on SQLite) so two workers cannot step/dispatch the same task at once.
        task = (
            await db.execute(
                select(GenerationTaskRecord)
                .where(GenerationTaskRecord.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not task:
            raise ValueError("Task không tồn tại.")

        if task.state in _TERMINAL_TASK_STATES:
            return task

        if (
            expected_schedule_token is not None
            and generation_step_token(task) != expected_schedule_token
        ):
            await db.rollback()
            raise StaleGenerationStepJob(task_id)

        # Claim any in-flight dispatch/poll/download before releasing the task row lock.
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        now = datetime.now(timezone.utc)
        if lease is not None:
            expiry = lease.expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > now:
                task.next_poll_delay_seconds = max(1.0, min(30.0, (expiry - now).total_seconds()))
                # Always change the row so the follow-up ARQ job ID differs from the job
                # that is currently running (an identical ID would be deduplicated).
                task.updated_at = now
                await db.commit()
                await db.refresh(task)
                return task
            await db.delete(lease)
            await db.flush()
            if not task.external_task_id:
                # Only dispatch takes a lease before a task has an external id. An expired
                # one means the process died mid-dispatch (hard kill, OOM, host loss) after
                # the provider request may have been sent: never re-send it automatically.
                return await GenerationQueueService._fail_dispatch_outcome_unknown(
                    db, task, "Dispatch lease expired without a provider task id (worker died mid-dispatch)."
                )

        # Polling an existing task does not consume a new slot. Lock only for dispatch reservation.
        if task.external_task_id:
            conn = await db.get(ProviderConnection, task.connection_id)
        else:
            conn = (
                await db.execute(
                    select(ProviderConnection)
                    .where(ProviderConnection.id == task.connection_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
        shot = await db.get(ShotRecord, task.shot_id)
        if not conn or not shot:
            raise ValueError("Connection hoặc Shot liên kết không tồn tại.")

        planned_frame: str | None = None
        if not task.external_task_id:
            if not conn.enabled:
                # Policy: a disabled connection never receives a new paid dispatch. The task
                # fails non-retryably (instead of waiting forever) so the operator can retry
                # explicitly on an enabled connection. Already-dispatched tasks keep polling
                # so a result that was already paid for is still collected.
                return await GenerationQueueService._fail_task(
                    db, task, shot,
                    category=BLOCK_CONNECTION_DISABLED,
                    message="Connection is disabled; the task was not dispatched. Retry on an enabled connection.",
                )
            guard_result = await GenerationQueueService._dispatch_guard(db, task, shot, conn)
            if isinstance(guard_result, GenerationTaskRecord):
                return guard_result
            planned_frame = guard_result

        # M7: select a connection account (least-recently-used) if any exist.
        accounts = list((
            await db.execute(
                select(ConnectionAccount).where(ConnectionAccount.connection_id == conn.id)
            )
        ).scalars().all())
        has_external_task = bool(task.external_task_id)
        try:
            account = resolve_task_account(accounts, task.account_id_used, has_external_task)
        except LookupError as exc:
            return await GenerationQueueService._fail_task(
                db, task, shot, category="pinned_account_missing", message=str(exc)
            )

        if not has_external_task and accounts and account is None:
            # Never bypass a configured pool by falling back to connection-level keys
            # when all pool accounts are disabled or unhealthy.
            task.state = QueueTaskState.QUEUED
            shot.generation_state = ShotGenerationState.QUEUED
            task.account_id_used = None
            task.next_poll_delay_seconds = compute_backoff_seconds(1)
            task.history_log = list(task.history_log or []) + [
                "Waiting for an enabled account with usable health; no account was selected."
            ]
            await db.commit()
            await db.refresh(task)
            return task

        if account is not None and not has_external_task:
            mark_account_used(account)
            task.account_id_used = account.id
            task.account_used = account.label
        provider = build_provider(conn, account)
        raw_secret = (
            vault.decrypt(account.encrypted_api_key)
            if account is not None
            else vault.decrypt(conn.encrypted_api_key)
        ) or ""

        if not task.external_task_id:
            return await GenerationQueueService._dispatch_new_task(
                db, task, shot, conn, account, provider, raw_secret, planned_frame, lease_state
            )
        return await GenerationQueueService._poll_and_collect(
            db, task, shot, conn, provider, raw_secret, lease_state
        )

    @staticmethod
    async def _dispatch_guard(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        conn: ProviderConnection,
    ) -> GenerationTaskRecord | str | None:
        """Re-run the shared guard before every paid dispatch (resume/recovery included).

        Returns the FAILED task when dispatch is refused, otherwise the conditioning frame
        to send, validated against the enqueue-time snapshot.
        """
        decision = await evaluate_generation_guard(
            db, shot, conn, task_type=task.task_type, phase="dispatch"
        )
        if not decision.allowed:
            return await GenerationQueueService._fail_task(
                db, task, shot,
                category=decision.code or "dispatch_refused",
                message=f"Dispatch refused by the generation guard: {decision.message}",
                ended_state=decision.blocked_state or ShotGenerationState.FAILED,
            )
        if task.task_type != "video":
            return None
        snapshot = request_snapshot_of(task)
        if snapshot is None:
            # Legacy task enqueued before request snapshots existed: use the live resolution.
            return decision.conditioning.asset_id
        planned = snapshot.get("conditioning_first_frame")
        if planned != decision.conditioning.asset_id:
            return await GenerationQueueService._fail_task(
                db, task, shot,
                category="stale_conditioning_frame",
                message=(
                    "The conditioning first frame changed after enqueue (upstream take "
                    "re-selected or continuity edited); re-enqueue to render on the current frame."
                ),
                ended_state=ShotGenerationState.NOT_STARTED,
            )
        return planned

    @staticmethod
    async def _dispatch_new_task(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        conn: ProviderConnection,
        account: ConnectionAccount | None,
        provider: VideoProvider,
        raw_secret: str,
        planned_frame: str | None,
        lease_state: dict[str, bool],
    ) -> GenerationTaskRecord:
        # Reserve the slot in a short transaction, then release DB locks before network I/O.
        now = datetime.now(timezone.utc)
        running_count = (
            await db.execute(
                select(func.count(GenerationTaskRecord.id)).where(
                    GenerationTaskRecord.connection_id == conn.id,
                    # A task waiting for operator input still holds its provider job
                    # (a muse.ai tab), so it occupies a concurrency slot like RUNNING.
                    GenerationTaskRecord.state.in_(PROVIDER_SLOT_TASK_STATES),
                    GenerationTaskRecord.id != task.id,
                )
            )
        ).scalar_one()

        if running_count >= conn.max_concurrency:
            task.state = QueueTaskState.QUEUED
            shot.generation_state = ShotGenerationState.QUEUED
            task.next_poll_delay_seconds = compute_backoff_seconds(1)
            task.history_log = list(task.history_log or []) + [
                f"Waiting for connection concurrency slot ({running_count}/{conn.max_concurrency} busy)."
            ]
            await db.commit()
            await db.refresh(task)
            return task

        # Dispatch from the enqueue-time snapshot, not the live (possibly edited) shot.
        snapshot = request_snapshot_of(task) or {}
        duration_seconds = float(snapshot.get("duration_seconds", shot.duration_seconds))
        aspect_ratio = str(snapshot.get("aspect_ratio", shot.aspect_ratio))

        task.attempt_count += 1
        task.state = QueueTaskState.RUNNING
        shot.generation_state = ShotGenerationState.RUNNING
        task.next_poll_delay_seconds = compute_backoff_seconds(1)
        db.add(GenerationDispatchLeaseRecord(
            task_id=task.id,
            connection_id=conn.id,
            expires_at=now + timedelta(seconds=dispatch_lease_seconds(conn)),
        ))
        # expire_on_commit=False keeps the claimed task snapshot available without opening a
        # new DB transaction while the provider request is in flight.
        await db.commit()
        lease_state["owned"] = True

        dispatch_markers: list[str] = []
        try:
            # Reference images are sent only where the provider has a field for them;
            # otherwise the history says so instead of implying they were applied.
            references: list[str] = []
            if shot.reference_asset_ids:
                if task.task_type == "video" and not getattr(provider, "supports_video_references", True):
                    dispatch_markers.append(
                        "Reference images not sent: this provider payload has no video reference field."
                    )
                else:
                    references = await GenerationQueueService.load_reference_data_uris(db, shot)
                    dispatch_markers.append(f"Reference images sent: {len(references)}.")
            if task.task_type == "image":
                lease_state["dispatch_in_flight"] = True
                p_task = await provider.generate_image(
                    ImageGenerationRequest(
                        shot_id=shot.id,
                        prompt=task.prompt_snapshot,
                        negative_prompt=task.negative_prompt_snapshot,
                        model_id=task.model_id,
                        aspect_ratio=aspect_ratio,
                        reference_image_urls=references,
                        idempotency_key=task.idempotency_key,
                    )
                )
            else:
                first_frame_data_uri = await GenerationQueueService.load_first_frame_data_uri(
                    db, shot, asset_id=planned_frame
                )
                dispatch_markers.append(
                    f"{_CONDITIONING_MARKER}{planned_frame or _CONDITIONING_NONE}"
                )
                lease_state["dispatch_in_flight"] = True
                p_task = await provider.generate_video(
                    VideoGenerationRequest(
                        shot_id=shot.id,
                        prompt=task.prompt_snapshot,
                        negative_prompt=task.negative_prompt_snapshot,
                        model_id=task.model_id,
                        duration_seconds=duration_seconds,
                        aspect_ratio=aspect_ratio,
                        first_frame_url=first_frame_data_uri,
                        reference_image_urls=references,
                        idempotency_key=task.idempotency_key,
                    )
                )
        except ProviderFailure as exc:
            # The provider answered or the adapter classified the outcome explicitly
            # (dispatch_outcome_unknown is a non-retryable category).
            lease_state["dispatch_in_flight"] = False
            return await GenerationQueueService._handle_dispatch_failure(
                db, task, shot, conn, account, exc, raw_secret
            )
        except Exception as exc:
            lease_state["dispatch_in_flight"] = False
            # Unexpected adapter failures are terminal rather than leaving RUNNING with no upstream ID.
            await db.refresh(task)
            await db.refresh(shot)
            if task.state in _TERMINAL_TASK_STATES:
                return await GenerationQueueService._release_lease_and_return(db, task)
            return await GenerationQueueService._fail_task(
                db, task, shot,
                category="provider_internal_error",
                message=f"Unexpected provider adapter error ({type(exc).__name__}).",
            )

        # Another operator may have paused/canceled the task while the request was in flight.
        await db.refresh(task)
        await db.refresh(shot)
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            await db.delete(lease)
        task.external_task_id = p_task.external_task_id
        task.history_log = list(task.history_log or []) + dispatch_markers + [
            f"Dispatched to {conn.name} -> external_task_id={p_task.external_task_id}."
        ]
        if task.state not in _TERMINAL_TASK_STATES:
            task.state = QueueTaskState.RUNNING
            shot.generation_state = ShotGenerationState.RUNNING
        else:
            task.history_log = list(task.history_log or []) + [
                "Provider accepted the request after the local task state changed; local state was preserved."
            ]
        await db.commit()
        lease_state["dispatch_in_flight"] = False
        await db.refresh(task)
        return task

    @staticmethod
    async def _handle_dispatch_failure(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        conn: ProviderConnection,
        account: ConnectionAccount | None,
        exc: ProviderFailure,
        raw_secret: str,
    ) -> GenerationTaskRecord:
        await db.refresh(task)
        await db.refresh(shot)
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            await db.delete(lease)
        if task.state in _TERMINAL_TASK_STATES:
            await db.commit()
            await db.refresh(task)
            return task

        safe_err = SecretRedactor.redact_text(str(exc), [raw_secret])
        task.error_category = exc.category.value
        task.retryable = exc.retryable
        task.error_message_redacted = safe_err
        if account is not None:
            account.last_error_redacted = safe_err
            account.last_checked_at = datetime.now(timezone.utc)
            if exc.category == ErrorCategory.AUTH_FAILED:
                account.health_state = ConnectionHealthState.AUTH_ERROR
            elif exc.category == ErrorCategory.QUOTA_EXHAUSTED:
                account.health_state = ConnectionHealthState.DEGRADED
        if not exc.retryable or task.attempt_count >= task.max_attempts:
            task.state = QueueTaskState.FAILED
            task.finished_at = datetime.now(timezone.utc)
            await GenerationQueueService.settle_shot_after_task_end(
                db, task, shot, ShotGenerationState.FAILED
            )
            if exc.category == ErrorCategory.AUTH_FAILED:
                conn.health_state = ConnectionHealthState.AUTH_ERROR
            elif exc.category == ErrorCategory.QUOTA_EXHAUSTED:
                conn.health_state = ConnectionHealthState.DEGRADED
            task.history_log = list(task.history_log or []) + [
                f"Terminal failure on attempt {task.attempt_count}: {safe_err}"
            ]
        else:
            delay = compute_backoff_seconds(task.attempt_count)
            task.state = QueueTaskState.QUEUED
            shot.generation_state = ShotGenerationState.QUEUED
            task.next_poll_delay_seconds = delay
            if account is not None and exc.category in (
                ErrorCategory.AUTH_FAILED, ErrorCategory.QUOTA_EXHAUSTED
            ):
                # This account is no longer the candidate for the retry.
                task.account_id_used = None
            task.history_log = list(task.history_log or []) + [
                f"Transient failure on attempt {task.attempt_count} (will retry in {delay}s): {safe_err}"
            ]
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def _poll_and_collect(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        conn: ProviderConnection,
        provider: VideoProvider,
        raw_secret: str,
        lease_state: dict[str, bool],
    ) -> GenerationTaskRecord:
        # Successful polls while waiting for the operator do not consume max_polls
        # (failed ones do, in _handle_poll_failure).
        counted_poll = task.state != QueueTaskState.AWAITING_INPUT
        if counted_poll:
            task.poll_count += 1
        if counted_poll and task.poll_count > task.max_polls:
            task.state = QueueTaskState.POLL_TIMEOUT
            task.error_category = ErrorCategory.UPSTREAM_POLL_TIMEOUT.value
            task.retryable = True
            task.error_message_redacted = f"Exceeded max_polls ({task.max_polls}) waiting for upstream task {task.external_task_id}."
            task.finished_at = datetime.now(timezone.utc)
            await GenerationQueueService.settle_shot_after_task_end(
                db, task, shot, ShotGenerationState.FAILED
            )
            task.history_log = list(task.history_log or []) + [task.error_message_redacted]
            await db.commit()
            await db.refresh(task)
            return task

        # Poll/download can take seconds; reserve with a short-lived lease and commit first.
        db.add(GenerationDispatchLeaseRecord(
            task_id=task.id,
            connection_id=conn.id,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=dispatch_lease_seconds(conn)),
        ))
        await db.commit()
        lease_state["owned"] = True
        try:
            p_status = await provider.get_task(task.external_task_id)
        except ProviderFailure as exc:
            return await GenerationQueueService._handle_poll_failure(db, task, shot, conn, exc, raw_secret, "poll")
        except Exception as exc:
            unexpected = ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                f"Unexpected provider poll error ({type(exc).__name__}).",
                retryable=False,
            )
            return await GenerationQueueService._handle_poll_failure(db, task, shot, conn, unexpected, raw_secret, "poll")
        await db.refresh(task)
        await db.refresh(shot)
        if task.state in _TERMINAL_TASK_STATES:
            return await GenerationQueueService._release_lease_and_return(db, task)

        if p_status.account_used and not task.account_used:
            task.account_used = p_status.account_used

        if p_status.status == "needs_input":
            return await GenerationQueueService._mark_awaiting_input(
                db, task, shot, p_status, raw_secret, counted_poll=counted_poll
            )
        if task.state == QueueTaskState.AWAITING_INPUT or task.pending_input_json is not None:
            # Answered elsewhere (or the provider moved on): the question is no longer open.
            task.state = QueueTaskState.RUNNING
            task.pending_input_json = None
            task.history_log = list(task.history_log or []) + [
                f"Provider is no longer waiting for input (status={p_status.status})."
            ]
            if p_status.status not in ("queued", "running"):
                # Persist before the (possibly long) download/failure handling, which
                # refreshes the task from the database.
                await db.commit()

        if p_status.status in ("queued", "running"):
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
            delay = compute_backoff_seconds(task.poll_count)
            task.next_poll_delay_seconds = delay
            task.history_log = list(task.history_log or []) + [
                f"Poll #{task.poll_count}: status={p_status.status} (next poll in {delay}s)."
            ]
            await db.commit()
            await db.refresh(task)
            return task

        if p_status.status == "failed":
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
            task.state = QueueTaskState.FAILED
            task.error_category = _UPSTREAM_FAILURE_CATEGORIES.get(
                p_status.error_code or "", "upstream_generation_failed"
            )
            task.retryable = False
            task.error_message_redacted = SecretRedactor.redact_text(p_status.error_message or "Upstream reported generation failed.", [raw_secret])
            task.finished_at = datetime.now(timezone.utc)
            await GenerationQueueService.settle_shot_after_task_end(
                db, task, shot, ShotGenerationState.FAILED
            )
            task.history_log = list(task.history_log or []) + [f"Upstream task failed: {task.error_message_redacted}"]
            await db.commit()
            await db.refresh(task)
            return task

        dest_dir = os.path.join(settings.media_root, task.project_id, "generated")
        stored: StoredMedia | None = None
        try:
            stored = await provider.download_result(p_status.result_media_url or task.external_task_id, dest_dir)
            await _verify_downloaded_artifact(
                stored.local_path, task.task_type, conn.provider_type, stored.mime_type
            )
        except ProviderFailure as exc:
            _unlink_quietly(stored.local_path if stored is not None else None)
            return await GenerationQueueService._handle_poll_failure(db, task, shot, conn, exc, raw_secret, "download")
        except Exception as exc:
            # Any unexpected adapter/verification error is a classified terminal failure that
            # releases the lease (never a RUNNING task stuck behind its own lease).
            _unlink_quietly(stored.local_path if stored is not None else None)
            unexpected = ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                f"Unexpected result download error ({type(exc).__name__}).",
                retryable=False,
            )
            return await GenerationQueueService._handle_poll_failure(
                db, task, shot, conn, unexpected, raw_secret, "download"
            )
        return await GenerationQueueService._finalize_media(
            db, task, shot, conn, stored, dest_dir, raw_secret
        )

    @staticmethod
    async def _mark_awaiting_input(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        p_status: ProviderTaskStatus,
        raw_secret: str,
        *,
        counted_poll: bool,
    ) -> GenerationTaskRecord:
        """The provider asked the operator a question: keep the slot, poll slowly.

        The task keeps its provider job, dispatch account and connection concurrency slot;
        the shot stays RUNNING. The poll that discovered the question is not counted
        against max_polls. A history entry is written only when the question changes.
        """
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            await db.delete(lease)
        if counted_poll:
            task.poll_count = max(0, task.poll_count - 1)
        raw_question = p_status.question or {
            "kind": "question", "text": "", "options": [], "asked_at": None,
        }
        question = {
            "kind": raw_question.get("kind", "question"),
            "text": SecretRedactor.redact_text(str(raw_question.get("text") or ""), [raw_secret]),
            "options": [
                SecretRedactor.redact_text(str(option), [raw_secret])
                for option in (raw_question.get("options") or [])
            ],
            "asked_at": raw_question.get("asked_at"),
        }
        is_new_question = (
            task.state != QueueTaskState.AWAITING_INPUT or task.pending_input_json != question
        )
        task.state = QueueTaskState.AWAITING_INPUT
        task.pending_input_json = question
        if shot.generation_state in (ShotGenerationState.QUEUED, ShotGenerationState.RUNNING):
            shot.generation_state = ShotGenerationState.RUNNING
        task.next_poll_delay_seconds = AWAITING_INPUT_POLL_SECONDS
        # Always change the row so the follow-up ARQ job ID differs from the running job.
        task.updated_at = datetime.now(timezone.utc)
        if is_new_question:
            preview = question["text"][:_HISTORY_TEXT_MAX_CHARS]
            task.history_log = list(task.history_log or []) + [
                f"Provider is waiting for operator input ({question['kind']}): {preview}"
            ]
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def _task_provider(
        db: AsyncSession, task: GenerationTaskRecord
    ) -> tuple[VideoProvider, str] | None:
        """Provider bound to the account that owns ``task``'s provider job, plus its secret.

        Returns None when the connection no longer exists; raises LookupError when the
        pinned account cannot be resolved.
        """
        conn = await db.get(ProviderConnection, task.connection_id)
        if conn is None:
            return None
        accounts = list((
            await db.execute(
                select(ConnectionAccount).where(ConnectionAccount.connection_id == conn.id)
            )
        ).scalars().all())
        account = resolve_task_account(accounts, task.account_id_used, True)
        provider = build_provider(conn, account)
        raw_secret = (
            vault.decrypt(account.encrypted_api_key)
            if account is not None
            else vault.decrypt(conn.encrypted_api_key)
        ) or ""
        return provider, raw_secret

    @staticmethod
    async def _release_reply_lease(db: AsyncSession, task_id: str) -> None:
        """Drop the lease taken for an answer that failed or was interrupted (best effort)."""
        try:
            await db.rollback()
            lease = await db.get(GenerationDispatchLeaseRecord, task_id)
            if lease is not None:
                await db.delete(lease)
                await db.commit()
        except Exception:  # noqa: BLE001 - the lease expires on its own
            logger.warning("Could not release the reply lease of task %s", task_id)

    @staticmethod
    async def reply_to_task(
        db: AsyncSession, task_id: str, answer: dict[str, str]
    ) -> GenerationTaskRecord:
        """Send the operator's answer to the provider question a task is waiting on.

        ``answer`` is exactly one of ``{"text": ...}`` / ``{"choice": ...}`` (validated by
        the API). Raises TaskNotFoundError, TaskNotAwaitingInputError,
        InvalidOperatorAnswerError, or ProviderFailure (INPUT_NOT_PENDING when the
        provider task is no longer waiting). On success the task is RUNNING again (or
        still AWAITING_INPUT with the provider's follow-up question); the caller must
        schedule the next step because the row version changed.
        """
        task = (
            await db.execute(
                select(GenerationTaskRecord)
                .where(GenerationTaskRecord.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if task is None:
            raise TaskNotFoundError(task_id)
        if task.state != QueueTaskState.AWAITING_INPUT or not task.external_task_id:
            state = task.state
            await db.rollback()
            raise TaskNotAwaitingInputError(state)
        if "choice" in answer:
            options = list((task.pending_input_json or {}).get("options") or [])
            if answer["choice"] not in options:
                await db.rollback()
                raise InvalidOperatorAnswerError("choice must be one of the pending question's options.")

        try:
            # The answer must reach the account that owns the provider task.
            loaded = await GenerationQueueService._task_provider(db, task)
        except LookupError as exc:
            await db.rollback()
            raise ProviderFailure(ErrorCategory.INVALID_REQUEST, str(exc), retryable=False) from exc
        if loaded is None:
            await db.rollback()
            raise TaskNotFoundError(task_id)
        provider, raw_secret = loaded
        if not isinstance(provider, InteractiveProvider):
            await db.rollback()
            raise ProviderFailure(
                ErrorCategory.INVALID_REQUEST,
                "This provider does not support answering operator questions.",
                retryable=False,
            )
        external_task_id = task.external_task_id
        # Claim the task with the same lease a worker poll takes, so neither a concurrent
        # answer nor a poll can interleave with this one while the row lock is released.
        now = datetime.now(timezone.utc)
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            expiry = lease.expires_at if lease.expires_at.tzinfo else lease.expires_at.replace(tzinfo=timezone.utc)
            if expiry > now:
                await db.rollback()
                raise TaskStateConflictError(
                    "Another answer or a provider poll is in progress for this task; retry shortly."
                )
            await db.delete(lease)
            await db.flush()
        conn = await db.get(ProviderConnection, task.connection_id)
        db.add(GenerationDispatchLeaseRecord(
            task_id=task.id,
            connection_id=task.connection_id,
            expires_at=now + timedelta(seconds=dispatch_lease_seconds(conn)),
        ))
        # Release the row lock before network I/O.
        try:
            await db.commit()
        except IntegrityError as exc:
            # SQLite has no row lock: a concurrent poll/answer inserted the lease first.
            await db.rollback()
            raise TaskStateConflictError(
                "Another answer or a provider poll is in progress for this task; retry shortly."
            ) from exc

        try:
            p_status = await provider.reply(external_task_id, dict(answer))
        except ProviderFailure as exc:
            await GenerationQueueService._release_reply_lease(db, task.id)
            raise ProviderFailure(
                exc.category,
                SecretRedactor.redact_text(exc.message, [raw_secret]),
                exc.http_status,
                exc.retryable,
            ) from exc
        except BaseException:
            await GenerationQueueService._release_reply_lease(db, task.id)
            raise

        try:
            return await GenerationQueueService._record_reply(db, task_id, answer, p_status, raw_secret)
        except BaseException:
            # The provider already has the answer; never leave the task locked for a whole
            # lease because recording it locally failed.
            logger.warning("Task %s: the answer was delivered but recording it failed", task_id)
            await GenerationQueueService._release_reply_lease(db, task_id)
            raise

    @staticmethod
    async def _record_reply(
        db: AsyncSession,
        task_id: str,
        answer: dict[str, str],
        p_status: ProviderTaskStatus,
        raw_secret: str,
    ) -> GenerationTaskRecord:
        """Persist a delivered answer and drop the reply lease in the same transaction."""
        task = (
            await db.execute(
                select(GenerationTaskRecord)
                .where(GenerationTaskRecord.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            await db.delete(lease)
        kind, value = next(iter(answer.items()))
        answered = SecretRedactor.redact_text(value, [raw_secret])[:_HISTORY_TEXT_MAX_CHARS]
        entry = f"Operator answered ({kind}): {answered}"
        if task.state != QueueTaskState.AWAITING_INPUT:
            # Canceled (or otherwise moved on) while the answer was in flight.
            task.history_log = list(task.history_log or []) + [
                f"{entry} (delivered after the local state changed to '{task.state.value}'; state preserved)."
            ]
        else:
            task.history_log = list(task.history_log or []) + [entry]
            follow_up = p_status.question if p_status.status == "needs_input" else None
            if follow_up is not None and follow_up != task.pending_input_json:
                task.pending_input_json = {
                    **follow_up,
                    "text": SecretRedactor.redact_text(str(follow_up.get("text") or ""), [raw_secret]),
                    "options": [
                        SecretRedactor.redact_text(str(option), [raw_secret])
                        for option in (follow_up.get("options") or [])
                    ],
                }
                task.next_poll_delay_seconds = AWAITING_INPUT_POLL_SECONDS
                task.history_log = list(task.history_log) + [
                    f"Provider asked a follow-up ({follow_up.get('kind')}): "
                    f"{task.pending_input_json['text'][:_HISTORY_TEXT_MAX_CHARS]}"
                ]
            else:
                task.state = QueueTaskState.RUNNING
                task.pending_input_json = None
                task.next_poll_delay_seconds = AFTER_REPLY_POLL_SECONDS
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def _lock_task(db: AsyncSession, task_id: str) -> GenerationTaskRecord:
        """Re-read a task under its row lock (no-op lock on SQLite); raises TaskNotFoundError.

        Operator state changes must not overwrite a state the worker committed meanwhile
        (e.g. a pause after the result was persisted would re-poll and create a duplicate take).
        """
        task = (
            await db.execute(
                select(GenerationTaskRecord)
                .where(GenerationTaskRecord.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if task is None:
            await db.rollback()
            raise TaskNotFoundError(task_id)
        return task

    @staticmethod
    async def pause_task(db: AsyncSession, task_id: str) -> GenerationTaskRecord:
        """Pause a QUEUED/RUNNING task; TaskStateConflictError otherwise."""
        task = await GenerationQueueService._lock_task(db, task_id)
        if task.state not in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
            state = task.state.value
            await db.rollback()
            raise TaskStateConflictError(f"Cannot pause a task in state '{state}'.")
        task.state = QueueTaskState.PAUSED
        task.history_log = list(task.history_log or []) + ["Paused by operator."]
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def resume_task(db: AsyncSession, task_id: str) -> GenerationTaskRecord:
        """Resume a PAUSED task. A task that was never dispatched needs an enabled connection."""
        task = await GenerationQueueService._lock_task(db, task_id)
        if task.state != QueueTaskState.PAUSED:
            state = task.state.value
            await db.rollback()
            raise TaskStateConflictError(f"Cannot resume a task in state '{state}'.")
        if not task.external_task_id:
            conn = await db.get(ProviderConnection, task.connection_id)
            if conn is None or not conn.enabled:
                await db.rollback()
                raise TaskStateConflictError(
                    "Cannot resume: the task was never dispatched and its connection is disabled "
                    "or missing. Cancel it and enqueue on an enabled connection."
                )
        task.state = QueueTaskState.RUNNING if task.external_task_id else QueueTaskState.QUEUED
        task.history_log = list(task.history_log or []) + ["Resumed by operator."]
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def cancel_task(db: AsyncSession, task_id: str) -> GenerationTaskRecord:
        """Cancel a non-terminal task (QUEUED/RUNNING/PAUSED/AWAITING_INPUT).

        Raises TaskNotFoundError, or TaskStateConflictError for a terminal task. The local
        cancel is committed first; then, for a RUNNING/AWAITING_INPUT task with a provider
        job, the provider is asked to cancel it best effort (see
        ``_propagate_cancel_to_provider``), which never fails or undoes the local cancel.
        """
        task = await GenerationQueueService._lock_task(db, task_id)
        if task.state in (
            QueueTaskState.SUCCEEDED,
            QueueTaskState.FAILED,
            QueueTaskState.CANCELED,
            QueueTaskState.POLL_TIMEOUT,
        ):
            state = task.state.value
            await db.rollback()
            raise TaskStateConflictError(f"Cannot cancel a task in terminal state '{state}'.")
        previous_state = task.state
        task.state = QueueTaskState.CANCELED
        task.pending_input_json = None
        task.finished_at = datetime.now(timezone.utc)
        shot = await db.get(ShotRecord, task.shot_id)
        if shot is not None:
            # A stale/extra task must not downgrade a shot that has newer active work or
            # an already selected take.
            await GenerationQueueService.settle_shot_after_task_end(
                db, task, shot, ShotGenerationState.CANCELED
            )
        task.history_log = list(task.history_log or []) + ["Canceled by operator."]
        await db.commit()
        await db.refresh(task)
        if previous_state in _PROVIDER_CANCEL_STATES and task.external_task_id:
            await GenerationQueueService._propagate_cancel_to_provider(db, task)
        return task

    @staticmethod
    async def _propagate_cancel_to_provider(db: AsyncSession, task: GenerationTaskRecord) -> None:
        """Best-effort provider cancel for an already committed local cancel.

        Never raises (except for the caller's own cancellation): every outcome is recorded
        as ``Provider cancel: cancelled | unsupported | already finished | not found |
        failed (<category>)`` in the task history, and failures are logged redacted.
        """
        raw_secret = ""
        try:
            loaded = await GenerationQueueService._task_provider(db, task)
            external_task_id = task.external_task_id or ""
            # End the read transaction before network I/O.
            await db.commit()
            if loaded is None:
                label = f"failed ({ErrorCategory.INVALID_REQUEST.value})"
                logger.warning("Provider cancel for task %s skipped: connection not found", task.id)
            else:
                provider, raw_secret = loaded
                if not isinstance(provider, CancellableProvider):
                    label = _CANCEL_OUTCOME_LABELS[CancelOutcome.UNSUPPORTED]
                else:
                    outcome = await asyncio.wait_for(
                        provider.cancel(external_task_id), timeout=PROVIDER_CANCEL_TIMEOUT_SECONDS
                    )
                    label = _CANCEL_OUTCOME_LABELS.get(outcome, f"unexpected outcome ({outcome})")
        except ProviderFailure as exc:
            label = f"failed ({exc.category.value})"
            logger.warning(
                "Provider cancel for task %s failed [%s]: %s",
                task.id,
                exc.category.value,
                SecretRedactor.redact_text(exc.message, [raw_secret])[:_HISTORY_TEXT_MAX_CHARS],
            )
        except (asyncio.TimeoutError, TimeoutError):
            label = "failed (timeout)"
            logger.warning("Provider cancel for task %s timed out", task.id)
        except LookupError:
            label = f"failed ({ErrorCategory.INVALID_REQUEST.value})"
            logger.warning("Provider cancel for task %s skipped: task account not found", task.id)
        except Exception as exc:  # noqa: BLE001 - best effort; the local cancel stands
            label = f"failed ({type(exc).__name__})"
            logger.warning("Provider cancel for task %s failed unexpectedly (%s)", task.id, type(exc).__name__)
        try:
            await db.refresh(task)
            task.history_log = list(task.history_log or []) + [f"Provider cancel: {label}"]
            await db.commit()
            await db.refresh(task)
        except Exception as exc:  # noqa: BLE001 - the history entry is informational
            logger.warning(
                "Could not record the provider cancel outcome for task %s (%s)", task.id, type(exc).__name__
            )
            await db.rollback()

    @staticmethod
    async def _finalize_media(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        conn: ProviderConnection,
        stored: StoredMedia,
        dest_dir: str,
        raw_secret: str,
    ) -> GenerationTaskRecord:
        """Extract boundary frames and compute storage paths before persisting the take."""
        await db.refresh(task)
        await db.refresh(shot)
        if task.state in _TERMINAL_TASK_STATES:
            _unlink_quietly(stored.local_path)
            return await GenerationQueueService._release_lease_and_return(db, task)

        existing_max_take_num = (
            await db.execute(select(func.max(ShotTakeRecord.take_number)).where(ShotTakeRecord.shot_id == shot.id))
        ).scalar_one()
        take_num = (int(existing_max_take_num) if existing_max_take_num is not None else 0) + 1

        boundary_artifacts: list[_BoundaryFrameArtifact] = []
        if task.task_type == "video":
            try:
                boundary_artifacts = await _extract_video_boundary_frames(
                    stored.local_path,
                    dest_dir,
                    shot.id,
                    take_num,
                    conn.provider_type,
                )
            except ProviderFailure as exc:
                _unlink_quietly(stored.local_path)
                return await GenerationQueueService._handle_poll_failure(
                    db, task, shot, conn, exc, raw_secret, "boundary-frame extraction"
                )
        all_paths = [stored.local_path, *(frame.path for frame in boundary_artifacts)]

        # Extraction runs outside the event loop; re-check cancellation before persisting artifacts.
        await db.refresh(task)
        await db.refresh(shot)
        if task.state in _TERMINAL_TASK_STATES:
            _unlink_quietly(*all_paths)
            return await GenerationQueueService._release_lease_and_return(db, task)

        try:
            rel_path = media_rel_path(stored.local_path, settings.media_root)
            frame_rel_paths = {
                frame.path: media_rel_path(frame.path, settings.media_root)
                for frame in boundary_artifacts
            }
        except AssetStorageError:
            _unlink_quietly(*all_paths)
            return await GenerationQueueService._handle_poll_failure(
                db, task, shot, conn,
                ProviderFailure(
                    ErrorCategory.INVALID_REQUEST,
                    "Generated media path resolved outside MEDIA_ROOT; result rejected.",
                    retryable=False,
                ),
                raw_secret, "media persistence",
            )

        return await GenerationQueueService._persist_take(
            db, task, shot, conn, stored, boundary_artifacts, rel_path, frame_rel_paths, raw_secret
        )

    @staticmethod
    async def _persist_take(
        db: AsyncSession,
        task: GenerationTaskRecord,
        shot: ShotRecord,
        conn: ProviderConnection,
        stored: StoredMedia,
        boundary_artifacts: list[_BoundaryFrameArtifact],
        rel_path: str,
        frame_rel_paths: dict[str, str],
        raw_secret: str,
    ) -> GenerationTaskRecord:
        """Allocate the take number, persist assets/take and complete the task."""
        # Serialize take-number selection per shot on databases that support row locks.
        # This lock is acquired after FFmpeg work so it is not held during extraction.
        shot = (
            await db.execute(
                select(ShotRecord)
                .where(ShotRecord.id == shot.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        latest_max_take_num = (
            await db.execute(
                select(func.max(ShotTakeRecord.take_number)).where(ShotTakeRecord.shot_id == shot.id)
            )
        ).scalar_one()
        take_num = (int(latest_max_take_num) if latest_max_take_num is not None else 0) + 1
        is_first_take = latest_max_take_num is None
        # Propagation skips shots with an active task, so the upstream selection may have
        # changed while this provider job ran. Output rendered on a superseded frame is
        # kept as a take but must not count as this shot's continuity.
        stale_conditioning: ConditioningFrame | None = None
        if task.task_type == "video" and shot.continuity_mode == "frame_chain" and shot.previous_shot_id:
            current = await resolve_conditioning_frame(db, shot)
            if current.missing or current.asset_id != conditioning_first_frame_of(task):
                stale_conditioning = current

        asset_id = f"ast_{uuid.uuid4().hex[:10]}"
        db.add(Asset(
            id=asset_id,
            project_id=task.project_id,
            scene_id=task.scene_id,
            shot_id=task.shot_id,
            asset_type=task.task_type,
            filename=os.path.basename(stored.local_path),
            storage_rel_path=rel_path,
            mime_type=stored.mime_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            provider_id=conn.id,
            model_id=task.model_id,
            is_locked_reference=(task.task_type == "image"),
        ))

        first_frame_aid: str | None = None
        last_frame_aid: str | None = None
        frame_assets: list[Asset] = []
        for frame in boundary_artifacts:
            asset_id_for_frame = (
                f"ast_ff_{uuid.uuid4().hex[:8]}"
                if frame.position == "first"
                else f"ast_lf_{uuid.uuid4().hex[:8]}"
            )
            if frame.position == "first":
                first_frame_aid = asset_id_for_frame
            elif frame.position == "last":
                last_frame_aid = asset_id_for_frame
            frame_assets.append(Asset(
                id=asset_id_for_frame,
                project_id=task.project_id,
                scene_id=task.scene_id,
                shot_id=shot.id,
                asset_type="reference",
                filename=os.path.basename(frame.path),
                storage_rel_path=frame_rel_paths[frame.path],
                mime_type="image/png",
                size_bytes=frame.size_bytes,
                sha256=frame.sha256,
                provider_id=conn.id,
                model_id=task.model_id,
                is_locked_reference=True,
            ))
        if frame_assets:
            db.add_all(frame_assets)

        take_id = f"tke_{uuid.uuid4().hex[:10]}"
        take = ShotTakeRecord(
            id=take_id,
            project_id=task.project_id,
            shot_id=shot.id,
            take_number=take_num,
            task_id=task.id,
            asset_id=asset_id,
            first_frame_asset_id=first_frame_aid,
            last_frame_asset_id=last_frame_aid,
            model_id=task.model_id,
            prompt_hash=task.prompt_hash,
            account_used=task.account_used,
            is_selected=is_first_take and stale_conditioning is None,
        )
        if not await _insert_take_with_number_retry(db, take):
            _unlink_quietly(stored.local_path, *(frame.path for frame in boundary_artifacts))
            await db.rollback()
            conflict = ProviderFailure(
                ErrorCategory.TRANSIENT_NETWORK,
                "Concurrent take-number allocation could not be completed; retrying persistence.",
                retryable=True,
            )
            return await GenerationQueueService._handle_poll_failure(
                db, task, shot, conn, conflict, raw_secret, "take persistence"
            )

        if take.is_selected:
            shot.selected_take_id = take_id
            # C1: the take keeps its own opening frame (take.first_frame_asset_id);
            # shot.first_frame_asset_id is reserved for the conditioning input.
            shot.last_frame_asset_id = last_frame_aid

        task.result_asset_id = asset_id
        task.take_id = take_id
        task.state = QueueTaskState.SUCCEEDED
        task.finished_at = datetime.now(timezone.utc)
        task.history_log = list(task.history_log or []) + [
            f"Completed! Saved asset={asset_id}, take={take_id}, last_frame={last_frame_aid}."
        ]
        if stale_conditioning is None:
            shot.generation_state = ShotGenerationState.SUCCEEDED
        else:
            if stale_conditioning.missing:
                shot.generation_state = ShotGenerationState.BLOCKED_UPSTREAM_FRAME
            else:
                shot.generation_state = ShotGenerationState.NOT_STARTED
                if stale_conditioning.source == "upstream":
                    shot.first_frame_asset_id = stale_conditioning.asset_id
            task.history_log = list(task.history_log) + [
                f"Stale continuity: rendered on first_frame={conditioning_first_frame_of(task)}, but the "
                f"upstream selection now yields {stale_conditioning.asset_id}. The take was kept but not "
                "selected; re-enqueue to render on the current frame."
            ]

        lease = await db.get(GenerationDispatchLeaseRecord, task.id)
        if lease is not None:
            await db.delete(lease)
        await db.commit()
        # Re-evaluate downstream frame-chain shots after every upstream success (not only the
        # first take): a forced re-render must still unblock shots waiting on this shot's
        # selected last frame. Propagation uses the *selected* take's last frame.
        if shot.last_frame_asset_id:
            await GenerationQueueService.propagate_frame_chain_downstream(db, shot)
        await db.refresh(task)
        return task

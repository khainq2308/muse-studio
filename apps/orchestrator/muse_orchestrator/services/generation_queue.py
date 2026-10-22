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
    ConnectionAccount,
)
from muse_orchestrator.providers.base import (
    VideoProvider, ImageGenerationRequest, VideoGenerationRequest,
    ProviderFailure, ErrorCategory
)
from muse_orchestrator.services.asset_storage import AssetStorageError, LocalAssetStorage
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
_ACTIVE_TASK_STATES = (QueueTaskState.QUEUED, QueueTaskState.RUNNING)

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
_FIRST_FRAME_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


class GenerationQueueService:
    """
    Điều phối vòng đời sinh Ảnh/Video qua muse2api + Tự động tách Boundary Frames
    và lan truyền Frame Chaining giữa các Shot liên tiếp (M4 & M5).
    """

    @staticmethod
    async def load_first_frame_data_uri(db: AsyncSession, shot: ShotRecord) -> str | None:
        """Load a validated local frame as a data URI; never pass an internal Asset ID upstream."""
        # A shot's stored opening frame is also retained for review/take history. Only
        # frame_chain mode means it should be sent as the conditioning input on dispatch.
        if shot.continuity_mode != "frame_chain":
            return None
        if not shot.first_frame_asset_id:
            if shot.previous_shot_id:
                raise ProviderFailure(
                    ErrorCategory.INVALID_REQUEST,
                    "Frame-chained shot has no first-frame asset; refusing to dispatch without continuity.",
                    retryable=False,
                )
            return None

        asset = await db.get(Asset, shot.first_frame_asset_id)
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
    async def propagate_frame_chain_downstream(db: AsyncSession, upstream_shot: ShotRecord) -> list[str]:
        """
        Nếu Shot kế tiếp có continuity_mode == 'frame_chain' và phụ thuộc vào upstream_shot,
        tự động gán first_frame_asset_id = upstream_shot.last_frame_asset_id và mở khoá.
        """
        if not upstream_shot.last_frame_asset_id:
            return []

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
        for ds in downstream_shots:
            ds.first_frame_asset_id = upstream_shot.last_frame_asset_id
            if ds.generation_state == ShotGenerationState.BLOCKED_UPSTREAM_FRAME:
                ds.generation_state = ShotGenerationState.NOT_STARTED
            unlocked_ids.append(ds.id)

        if unlocked_ids:
            await db.commit()
        return unlocked_ids

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
        if shot.selected_take_id:
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
        blockers = [i for i in (shot.lint_report_json or []) if i.get("severity") == "blocker"]
        if not blockers:
            return None
        return f"Shot còn lỗi Blocker từ Prompt Linter: {blockers[0].get('code')}."

    @staticmethod
    async def check_enqueue_preconditions(db: AsyncSession, shot: ShotRecord) -> str | None:
        """Side-effect-free version of the enqueue guards; returns an error message or None."""
        if shot.generation_state == ShotGenerationState.BLOCKED_REFERENCE:
            return "Shot đang ở trạng thái BLOCKED_REFERENCE (thiếu file tham chiếu bắt buộc)."
        if (message := GenerationQueueService.lint_blocker_message(shot)) is not None:
            return message
        if shot.continuity_mode == "frame_chain" and shot.previous_shot_id and not shot.first_frame_asset_id:
            prev_shot = await db.get(ShotRecord, shot.previous_shot_id)
            if not await GenerationQueueService.upstream_has_valid_last_frame(db, prev_shot):
                return "Shot đang chờ last_frame từ Shot phía trước (BLOCKED_UPSTREAM_FRAME)."
        return None

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
                # Upstream frame disappeared between the scan and the enqueue; the shot
                # is now BLOCKED_UPSTREAM_FRAME and will be re-evaluated on the next success.
                continue
            results.append((cs, task, created))
        return results

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
        if shot.generation_state == ShotGenerationState.BLOCKED_REFERENCE:
            raise ValueError("Shot đang ở trạng thái BLOCKED_REFERENCE (thiếu file tham chiếu bắt buộc).")
        if (message := GenerationQueueService.lint_blocker_message(shot)) is not None:
            # Same state batch launch assigns to any linter blocker.
            shot.generation_state = ShotGenerationState.BLOCKED_REFERENCE
            await db.commit()
            raise ValueError(message)

        # Kiểm tra Frame Chaining: Nếu cần nối từ shot trước mà shot trước chưa có last_frame_asset_id -> Chặn!
        if shot.continuity_mode == "frame_chain" and shot.previous_shot_id and not shot.first_frame_asset_id:
            prev_shot = await db.get(ShotRecord, shot.previous_shot_id)
            if await GenerationQueueService.upstream_has_valid_last_frame(db, prev_shot):
                shot.first_frame_asset_id = prev_shot.last_frame_asset_id
            else:
                shot.generation_state = ShotGenerationState.BLOCKED_UPSTREAM_FRAME
                await db.commit()
                raise ValueError("Shot đang chờ last_frame từ Shot phía trước (BLOCKED_UPSTREAM_FRAME).")

        if force_retry:
            # Serialize forced re-renders per shot (row lock; no-op on SQLite) and never
            # start a second paid generation while one is already queued/running.
            await db.execute(
                select(ShotRecord.id).where(ShotRecord.id == shot.id).with_for_update()
            )
            active = (
                await db.execute(
                    select(GenerationTaskRecord)
                    .where(
                        GenerationTaskRecord.shot_id == shot.id,
                        GenerationTaskRecord.task_type == task_type,
                        GenerationTaskRecord.state.in_(_ACTIVE_TASK_STATES),
                    )
                    .order_by(GenerationTaskRecord.created_at.desc(), GenerationTaskRecord.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if active is not None:
                return active, False

        frame_chain_seed = shot.first_frame_asset_id or "no_first_frame"
        computed_idem = idempotency_key or hashlib.sha256(
            f"{shot.id}:{task_type}:{model_id}:{shot.prompt_hash}:{frame_chain_seed}".encode("utf-8")
        ).hexdigest()[:32]

        existing = (
            await db.execute(
                select(GenerationTaskRecord).where(GenerationTaskRecord.idempotency_key == computed_idem)
            )
        ).scalar_one_or_none()

        if existing is not None and not force_retry:
            # Strict idempotency: the same key returns the same task, whatever its
            # state. A new generation can cost provider quota, so it requires an
            # explicit force_retry=True instead of happening on a plain retry.
            # The shot must not stay BLOCKED/NOT_STARTED behind an already-finished or
            # in-flight task for the same inputs (it would never be re-evaluated).
            synced_state: ShotGenerationState | None = None
            if existing.state == QueueTaskState.SUCCEEDED and shot.selected_take_id:
                synced_state = ShotGenerationState.SUCCEEDED
            elif existing.state == QueueTaskState.QUEUED:
                synced_state = ShotGenerationState.QUEUED
            elif existing.state == QueueTaskState.RUNNING:
                synced_state = ShotGenerationState.RUNNING
            if synced_state is not None and shot.generation_state in (
                ShotGenerationState.BLOCKED_UPSTREAM_FRAME, ShotGenerationState.NOT_STARTED,
            ):
                shot.generation_state = synced_state
                await db.commit()
            return existing, False

        if existing is not None:
            # force_retry=True: idempotency_key is UNIQUE, so a fresh task needs a
            # fresh key (was an IntegrityError).
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
            history_log=[f"Task enqueued for shot {shot.id} ({task_type}, first_frame={shot.first_frame_asset_id})."],
        )
        shot.generation_state = ShotGenerationState.QUEUED
        db.add(task)
        try:
            await db.commit()
        except IntegrityError:
            # Lost a concurrent-enqueue race: two requests computed the same
            # idempotency_key and both passed the pre-check above, so the UNIQUE
            # constraint rejected this insert. Roll back and return the task the
            # winning request created instead of surfacing a 500.
            await db.rollback()
            existing = (
                await db.execute(
                    select(GenerationTaskRecord).where(
                        GenerationTaskRecord.idempotency_key == computed_idem
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False
            raise
        await db.refresh(task)
        return task, True

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
        if task.state in (
            QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
            QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
        ):
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

    @staticmethod
    async def step_task(db: AsyncSession, task_id: str) -> GenerationTaskRecord:
        """Advance one task by a single dispatch/poll/download step.

        If the step is cancelled (ARQ job timeout, worker shutdown) while this call owns
        the dispatch lease, the lease is released before re-raising so another worker can
        resume the task instead of waiting for the lease to expire.
        """
        lease_state: dict[str, bool] = {"owned": False}
        try:
            return await GenerationQueueService._step_task_impl(db, task_id, lease_state)
        except asyncio.CancelledError:
            if lease_state["owned"]:
                await GenerationQueueService._release_lease_after_cancel(db, task_id)
            raise

    @staticmethod
    async def _release_lease_after_cancel(db: AsyncSession, task_id: str) -> None:
        try:
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001 - best effort; the session may be mid-statement
                pass
            lease = await db.get(GenerationDispatchLeaseRecord, task_id)
            if lease is not None:
                await db.delete(lease)
                await db.commit()
        except Exception as exc:  # noqa: BLE001 - never mask the cancellation itself
            logger.warning(
                "Could not release dispatch lease for task %s after cancellation (%s)",
                task_id,
                type(exc).__name__,
            )

    @staticmethod
    async def _step_task_impl(
        db: AsyncSession, task_id: str, lease_state: dict[str, bool]
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

        if task.state in (QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED, QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT):
            return task

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

        if not conn.enabled and not task.external_task_id:
            # Policy: a disabled connection never receives a new paid dispatch. The task
            # fails non-retryably (instead of waiting forever) so the operator can retry
            # explicitly on an enabled connection. Already-dispatched tasks keep polling
            # so a result that was already paid for is still collected.
            task.state = QueueTaskState.FAILED
            task.error_category = "connection_disabled"
            task.retryable = False
            task.error_message_redacted = (
                "Connection is disabled; the task was not dispatched. Retry on an enabled connection."
            )
            task.finished_at = datetime.now(timezone.utc)
            task.history_log = list(task.history_log or []) + [task.error_message_redacted]
            await GenerationQueueService.settle_shot_after_task_end(
                db, task, shot, ShotGenerationState.FAILED
            )
            await db.commit()
            await db.refresh(task)
            return task

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
            task.state = QueueTaskState.FAILED
            task.error_category = "pinned_account_missing"
            task.retryable = False
            task.error_message_redacted = str(exc)
            task.finished_at = datetime.now(timezone.utc)
            await GenerationQueueService.settle_shot_after_task_end(
                db, task, shot, ShotGenerationState.FAILED
            )
            task.history_log = list(task.history_log or []) + [str(exc)]
            await db.commit()
            await db.refresh(task)
            return task

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
            # Reserve the slot in a short transaction, then release DB locks before network I/O.
            now = datetime.now(timezone.utc)
            running_count = (
                await db.execute(
                    select(func.count(GenerationTaskRecord.id)).where(
                        GenerationTaskRecord.connection_id == conn.id,
                        GenerationTaskRecord.state == QueueTaskState.RUNNING,
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

            task.attempt_count += 1
            task.state = QueueTaskState.RUNNING
            shot.generation_state = ShotGenerationState.RUNNING
            task.next_poll_delay_seconds = compute_backoff_seconds(1)
            lease_seconds = dispatch_lease_seconds(conn)
            db.add(GenerationDispatchLeaseRecord(
                task_id=task.id,
                connection_id=conn.id,
                expires_at=now + timedelta(seconds=lease_seconds),
            ))
            # expire_on_commit=False keeps the claimed task snapshot available without opening a
            # new DB transaction while the provider request is in flight.
            await db.commit()
            lease_state["owned"] = True

            dispatch_markers: list[str] = []
            try:
                if task.task_type == "image":
                    p_task = await provider.generate_image(
                        ImageGenerationRequest(
                            shot_id=shot.id,
                            prompt=task.prompt_snapshot,
                            negative_prompt=task.negative_prompt_snapshot,
                            model_id=task.model_id,
                            aspect_ratio=shot.aspect_ratio,
                            idempotency_key=task.idempotency_key,
                        )
                    )
                else:
                    first_frame_data_uri = await GenerationQueueService.load_first_frame_data_uri(db, shot)
                    conditioning_id = shot.first_frame_asset_id if first_frame_data_uri else None
                    dispatch_markers.append(
                        f"{_CONDITIONING_MARKER}{conditioning_id or _CONDITIONING_NONE}"
                    )
                    p_task = await provider.generate_video(
                        VideoGenerationRequest(
                            shot_id=shot.id,
                            prompt=task.prompt_snapshot,
                            negative_prompt=task.negative_prompt_snapshot,
                            model_id=task.model_id,
                            duration_seconds=shot.duration_seconds,
                            aspect_ratio=shot.aspect_ratio,
                            first_frame_url=first_frame_data_uri,
                            idempotency_key=task.idempotency_key,
                        )
                    )
            except ProviderFailure as exc:
                await db.refresh(task)
                await db.refresh(shot)
                lease = await db.get(GenerationDispatchLeaseRecord, task.id)
                if lease is not None:
                    await db.delete(lease)
                if task.state in (
                    QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
                    QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
                ):
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
            except Exception as exc:
                # Unexpected adapter failures are terminal rather than leaving RUNNING with no upstream ID.
                await db.refresh(task)
                await db.refresh(shot)
                lease = await db.get(GenerationDispatchLeaseRecord, task.id)
                if lease is not None:
                    await db.delete(lease)
                if task.state not in (
                    QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
                    QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
                ):
                    task.state = QueueTaskState.FAILED
                    task.finished_at = datetime.now(timezone.utc)
                    task.retryable = False
                    task.error_category = "provider_internal_error"
                    task.error_message_redacted = f"Unexpected provider adapter error ({type(exc).__name__})."
                    await GenerationQueueService.settle_shot_after_task_end(
                        db, task, shot, ShotGenerationState.FAILED
                    )
                    task.history_log = list(task.history_log or []) + [task.error_message_redacted]
                await db.commit()
                await db.refresh(task)
                return task

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
            if task.state not in (
                QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
                QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
            ):
                task.state = QueueTaskState.RUNNING
                shot.generation_state = ShotGenerationState.RUNNING
            else:
                task.history_log = list(task.history_log or []) + [
                    "Provider accepted the request after the local task state changed; local state was preserved."
                ]
            await db.commit()
            await db.refresh(task)
            return task

        task.poll_count += 1
        if task.poll_count > task.max_polls:
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
        if task.state in (
            QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
            QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
        ):
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
            await db.commit()
            await db.refresh(task)
            return task

        if p_status.account_used and not task.account_used:
            task.account_used = p_status.account_used

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
            task.error_category = "upstream_generation_failed"
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
        stored = None
        try:
            stored = await provider.download_result(p_status.result_media_url or task.external_task_id, dest_dir)
            await _verify_downloaded_artifact(
                stored.local_path, task.task_type, conn.provider_type, stored.mime_type
            )
        except ProviderFailure as exc:
            if stored is not None:
                try:
                    os.unlink(stored.local_path)
                except OSError:
                    pass
            return await GenerationQueueService._handle_poll_failure(db, task, shot, conn, exc, raw_secret, "download")
        await db.refresh(task)
        await db.refresh(shot)
        if task.state in (
            QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
            QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
        ):
            try:
                os.unlink(stored.local_path)
            except OSError:
                pass
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
            await db.commit()
            await db.refresh(task)
            return task

        existing_max_take_num = (
            await db.execute(select(func.max(ShotTakeRecord.take_number)).where(ShotTakeRecord.shot_id == shot.id))
        ).scalar_one()
        take_num = (int(existing_max_take_num) if existing_max_take_num is not None else 0) + 1
        is_first_take = existing_max_take_num is None

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
                try:
                    os.unlink(stored.local_path)
                except OSError:
                    pass
                return await GenerationQueueService._handle_poll_failure(
                    db, task, shot, conn, exc, raw_secret, "boundary-frame extraction"
                )

        # Extraction runs outside the event loop; re-check cancellation before persisting artifacts.
        await db.refresh(task)
        await db.refresh(shot)
        if task.state in (
            QueueTaskState.PAUSED, QueueTaskState.CANCELED, QueueTaskState.SUCCEEDED,
            QueueTaskState.FAILED, QueueTaskState.POLL_TIMEOUT,
        ):
            for path in [stored.local_path, *(frame.path for frame in boundary_artifacts)]:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            lease = await db.get(GenerationDispatchLeaseRecord, task.id)
            if lease is not None:
                await db.delete(lease)
            await db.commit()
            await db.refresh(task)
            return task

        # Serialize take-number selection per shot on databases that support row locks.
        # This lock is acquired after FFmpeg work so it is not held during extraction.
        locked_shot = (
            await db.execute(
                select(ShotRecord)
                .where(ShotRecord.id == shot.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        shot = locked_shot
        latest_max_take_num = (
            await db.execute(
                select(func.max(ShotTakeRecord.take_number)).where(ShotTakeRecord.shot_id == shot.id)
            )
        ).scalar_one()
        take_num = (int(latest_max_take_num) if latest_max_take_num is not None else 0) + 1
        is_first_take = latest_max_take_num is None

        rel_path = os.path.relpath(stored.local_path, settings.media_root)

        asset_id = f"ast_{uuid.uuid4().hex[:10]}"
        asset = Asset(
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
        )
        db.add(asset)

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
                storage_rel_path=os.path.relpath(frame.path, settings.media_root),
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
            is_selected=is_first_take,
        )
        inserted_take = await _insert_take_with_number_retry(db, take)
        if not inserted_take:
            for path in [stored.local_path, *(frame.path for frame in boundary_artifacts)]:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            await db.rollback()
            conflict = ProviderFailure(
                ErrorCategory.TRANSIENT_NETWORK,
                "Concurrent take-number allocation could not be completed; retrying persistence.",
                retryable=True,
            )
            return await GenerationQueueService._handle_poll_failure(
                db, task, shot, conn, conflict, raw_secret, "take persistence"
            )

        take_num = take.take_number
        is_first_take = take.is_selected
        if is_first_take:
            shot.selected_take_id = take_id
            if not shot.first_frame_asset_id:
                shot.first_frame_asset_id = first_frame_aid
            shot.last_frame_asset_id = last_frame_aid

        shot.generation_state = ShotGenerationState.SUCCEEDED
        task.result_asset_id = asset_id
        task.take_id = take_id
        task.state = QueueTaskState.SUCCEEDED
        task.finished_at = datetime.now(timezone.utc)
        task.history_log = list(task.history_log or []) + [
            f"Completed! Saved asset={asset_id}, take={take_id}, last_frame={last_frame_aid}."
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

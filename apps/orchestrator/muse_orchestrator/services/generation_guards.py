"""Single source of truth for "may this shot be (re)generated now?".

Every enqueue path (POST /queue/tasks, batch launch, retry-failed, flag-continuity,
frame-chain auto-enqueue) and the dispatch step use :func:`evaluate_generation_guard`.
The decision itself (:func:`decide_generation_guard`) is a pure function of the shot,
connection and resolved conditioning frame; only the frame resolution reads the database.

Conditioning-frame contract (C1): ``shot.first_frame_asset_id`` is a conditioning *input*
(operator-set, or resolved from the upstream shot). A shot's own extracted opening frame
lives on its take (``take.first_frame_asset_id``) and is never a conditioning input.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.models.domain import (
    ACTIVE_GENERATION_TASK_STATES,
    Asset,
    GenerationTaskRecord,
    ProviderConnection,
    ShotGenerationState,
    ShotRecord,
    ShotTakeRecord,
)

GuardPhase = Literal["enqueue", "dispatch"]
ConditioningSource = Literal["upstream", "operator", "none"]

# Tasks in these states occupy the per-(shot, task_type) active slot. Mirrors the partial
# unique index ``uq_generation_tasks_active_shot_type``.
ACTIVE_SLOT_TASK_STATES = ACTIVE_GENERATION_TASK_STATES

BLOCK_BLOCKED_REFERENCE = "blocked_reference"
BLOCK_LINT = "lint_blocker"
BLOCK_CONNECTION_DISABLED = "connection_disabled"
BLOCK_UPSTREAM_FRAME = "blocked_upstream_frame"
BLOCK_CONDITIONING_FRAME_INVALID = "conditioning_frame_invalid"

MSG_BLOCKED_REFERENCE = "Shot đang ở trạng thái BLOCKED_REFERENCE (thiếu file tham chiếu bắt buộc)."
MSG_UPSTREAM_FRAME = "Shot đang chờ last_frame từ Shot phía trước (BLOCKED_UPSTREAM_FRAME)."
MSG_CONNECTION_DISABLED = "Connection is disabled; no new provider dispatch is allowed."
MSG_CONDITIONING_FRAME_INVALID = (
    "The operator-set first frame is missing or belongs to another project; refusing to "
    "generate without the requested conditioning frame."
)

_REQUEST_SNAPSHOT_MARKER = "request_snapshot="


@dataclass(frozen=True)
class ConditioningFrame:
    """The first frame a video generation must be conditioned on (None = text-to-video)."""

    asset_id: str | None
    source: ConditioningSource
    # True when conditioning is required but no valid frame exists (fail closed).
    missing: bool = False


NO_CONDITIONING = ConditioningFrame(asset_id=None, source="none")


@dataclass(frozen=True)
class GenerationGuardDecision:
    code: str | None
    message: str | None
    # Shot state to record when blocked at enqueue (None = leave the shot state alone).
    blocked_state: ShotGenerationState | None
    conditioning: ConditioningFrame

    @property
    def allowed(self) -> bool:
        return self.code is None


class GenerationGuardError(ValueError):
    """Expected refusal to enqueue; a ValueError for backward-compatible route handling."""

    def __init__(self, decision: GenerationGuardDecision) -> None:
        super().__init__(decision.message or "Generation is not allowed.")
        self.decision = decision

    @property
    def code(self) -> str | None:
        return self.decision.code


def lint_blockers(shot: Any) -> list[dict[str, Any]]:
    return [
        issue for issue in (getattr(shot, "lint_report_json", None) or [])
        if isinstance(issue, dict) and issue.get("severity") == "blocker"
    ]


def lint_blocker_message(shot: Any) -> str | None:
    """Every Prompt Linter blocker (not only BLOCKED_REFERENCE) forbids provider dispatch."""
    blockers = lint_blockers(shot)
    if not blockers:
        return None
    return f"Shot còn lỗi Blocker từ Prompt Linter: {blockers[0].get('code')}."


def decide_generation_guard(
    shot: Any,
    conn: ProviderConnection | None,
    conditioning: ConditioningFrame,
    *,
    phase: GuardPhase,
) -> GenerationGuardDecision:
    """Pure decision shared by enqueue and dispatch.

    At dispatch the shot state is QUEUED/RUNNING (owned by the task), so only the lint
    report is authoritative; at enqueue an explicit BLOCKED_REFERENCE state also blocks.
    """
    if phase == "enqueue" and shot.generation_state == ShotGenerationState.BLOCKED_REFERENCE:
        return GenerationGuardDecision(
            BLOCK_BLOCKED_REFERENCE, MSG_BLOCKED_REFERENCE,
            ShotGenerationState.BLOCKED_REFERENCE, conditioning,
        )
    message = lint_blocker_message(shot)
    if message is not None:
        # Batch launch has always recorded every linter blocker as BLOCKED_REFERENCE.
        return GenerationGuardDecision(
            BLOCK_LINT, message, ShotGenerationState.BLOCKED_REFERENCE, conditioning
        )
    if conn is not None and not conn.enabled:
        return GenerationGuardDecision(
            BLOCK_CONNECTION_DISABLED, MSG_CONNECTION_DISABLED, None, conditioning
        )
    if conditioning.missing:
        if conditioning.source == "upstream":
            return GenerationGuardDecision(
                BLOCK_UPSTREAM_FRAME, MSG_UPSTREAM_FRAME,
                ShotGenerationState.BLOCKED_UPSTREAM_FRAME, conditioning,
            )
        return GenerationGuardDecision(
            BLOCK_CONDITIONING_FRAME_INVALID, MSG_CONDITIONING_FRAME_INVALID, None, conditioning
        )
    return GenerationGuardDecision(None, None, None, conditioning)


async def _valid_project_asset(db: AsyncSession, asset_id: str | None, project_id: str) -> bool:
    if not asset_id:
        return False
    asset = await db.get(Asset, asset_id)
    return asset is not None and asset.project_id == project_id


async def upstream_selected_last_frame(
    db: AsyncSession, shot: Any, upstream: ShotRecord | None
) -> str | None:
    """The validated last frame of the upstream shot's *selected* take, or None.

    Legacy rows without a selected take fall back to ``upstream.last_frame_asset_id``.
    """
    if upstream is None or upstream.project_id != shot.project_id:
        return None
    frame_id = upstream.last_frame_asset_id
    if upstream.selected_take_id:
        take = await db.get(ShotTakeRecord, upstream.selected_take_id)
        if take is not None and take.shot_id == upstream.id:
            frame_id = take.last_frame_asset_id
    if not await _valid_project_asset(db, frame_id, shot.project_id):
        return None
    return frame_id


async def is_frame_produced_by_shot(db: AsyncSession, shot_id: str, asset_id: str) -> bool:
    """True when ``asset_id`` is a boundary frame extracted from one of the shot's own takes."""
    row = (
        await db.execute(
            select(ShotTakeRecord.id)
            .where(
                ShotTakeRecord.shot_id == shot_id,
                or_(
                    ShotTakeRecord.first_frame_asset_id == asset_id,
                    ShotTakeRecord.last_frame_asset_id == asset_id,
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def resolve_conditioning_frame(db: AsyncSession, shot: Any) -> ConditioningFrame:
    """Resolve the conditioning first frame for a video generation of ``shot``.

    - Only ``frame_chain`` shots are conditioned (unchanged product rule).
    - With ``previous_shot_id``: the upstream selected take's last frame (same project,
      asset exists); missing → fail closed (BLOCKED_UPSTREAM_FRAME).
    - Otherwise: the operator-set ``first_frame_asset_id`` unless it is a frame extracted
      from this shot's own takes (legacy rows wrote that there); a dangling operator frame
      fails closed instead of silently becoming text-to-video.
    """
    if getattr(shot, "continuity_mode", None) != "frame_chain":
        return NO_CONDITIONING
    if shot.previous_shot_id:
        upstream = await db.get(ShotRecord, shot.previous_shot_id)
        frame_id = await upstream_selected_last_frame(db, shot, upstream)
        if frame_id is None:
            return ConditioningFrame(asset_id=None, source="upstream", missing=True)
        return ConditioningFrame(asset_id=frame_id, source="upstream")
    operator_frame = shot.first_frame_asset_id
    if not operator_frame or await is_frame_produced_by_shot(db, shot.id, operator_frame):
        return NO_CONDITIONING
    if not await _valid_project_asset(db, operator_frame, shot.project_id):
        return ConditioningFrame(asset_id=None, source="operator", missing=True)
    return ConditioningFrame(asset_id=operator_frame, source="operator")


async def evaluate_generation_guard(
    db: AsyncSession,
    shot: Any,
    conn: ProviderConnection | None,
    *,
    task_type: str = "video",
    phase: GuardPhase,
) -> GenerationGuardDecision:
    """Resolve the conditioning frame (videos only) and apply the shared decision."""
    conditioning = (
        await resolve_conditioning_frame(db, shot) if task_type == "video" else NO_CONDITIONING
    )
    return decide_generation_guard(shot, conn, conditioning, phase=phase)


# --- request snapshot ------------------------------------------------------------------------
# generation_tasks has no JSON request column; the snapshot is stored as one structured
# history_log entry (the same mechanism as the dispatch conditioning marker).


def request_snapshot_entry(shot: Any, conditioning: ConditioningFrame) -> str:
    payload = {
        "duration_seconds": float(shot.duration_seconds),
        "aspect_ratio": str(shot.aspect_ratio),
        "conditioning_first_frame": conditioning.asset_id,
        "conditioning_source": conditioning.source,
    }
    return _REQUEST_SNAPSHOT_MARKER + json.dumps(payload, sort_keys=True)


def request_snapshot_of(task: GenerationTaskRecord) -> dict[str, Any] | None:
    """The enqueue-time request snapshot, or None for legacy tasks."""
    for entry in task.history_log or []:
        if isinstance(entry, str) and entry.startswith(_REQUEST_SNAPSHOT_MARKER):
            try:
                data = json.loads(entry[len(_REQUEST_SNAPSHOT_MARKER):])
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None

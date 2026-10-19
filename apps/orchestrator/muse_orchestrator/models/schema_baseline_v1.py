"""Frozen schema snapshot for Alembic bootstrap revision 0001.

This copy defines only the ORM metadata that existed when the Alembic bridge was
introduced. Do not edit it after migration 0001 is deployed. New schema changes
must be represented by new, explicit Alembic revisions; they must not silently
change what the bootstrap revision creates on a fresh database.
"""

from __future__ import annotations
import enum
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import String, Integer, Float, Boolean, ForeignKey, DateTime, Enum, JSON, Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --- 4 NHÓM TRẠNG THÁI ĐỘC LẬP (MỤC 7 PRD) ---

class ShotApprovalState(str, enum.Enum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ShotGenerationState(str, enum.Enum):
    NOT_STARTED = "not_started"
    BLOCKED_REFERENCE = "blocked_reference"
    BLOCKED_UPSTREAM_FRAME = "blocked_upstream_frame"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class NodeExecutionState(str, enum.Enum):
    IDLE = "idle"
    READY = "ready"
    BLOCKED = "blocked"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    SKIPPED = "skipped"


class ConnectionHealthState(str, enum.Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    AUTH_ERROR = "auth_error"
    UNAVAILABLE = "unavailable"


class QueueTaskState(str, enum.Enum):
    QUEUED = "queued"
    PAUSED = "paused"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    POLL_TIMEOUT = "poll_timeout"
    CANCELED = "canceled"


class ExportJobState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


# --- M1: FOUNDATION MODELS ---

class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9")
    fps: Mapped[int] = mapped_column(Integer, default=24)
    style_preset: Mapped[str] = mapped_column(String(128), default="2d_anime_cinematic")
    language: Mapped[str] = mapped_column(String(16), default="vi")
    output_profile: Mapped[str] = mapped_column(String(64), default="1080p_h264")
    current_revision_num: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProjectRevision(Base):
    __tablename__ = "project_revisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    commit_message: Mapped[str] = mapped_column(String(512), default="Initial project snapshot")
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderConnection(Base):
    __tablename__ = "provider_connections"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    provider_type: Mapped[str] = mapped_column(String(64), default="muse2api")
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_masked: Mapped[str] = mapped_column(String(64), default="[NOT_SET]")
    proxy_display_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_state: Mapped[ConnectionHealthState] = mapped_column(
        Enum(ConnectionHealthState), default=ConnectionHealthState.UNKNOWN
    )
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    capabilities_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_error_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    shot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_rel_path: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_locked_reference: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), default="admin_operator")
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- M2: SCRIPT STUDIO, STORY BIBLE & SHOT PIPELINE MODELS ---

class ScriptRevision(Base):
    __tablename__ = "script_revisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_format: Mapped[str] = mapped_column(String(32), default="markdown")
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_proposal_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    diff_summary: Mapped[str] = mapped_column(Text, default="")
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StoryBibleEntry(Base):
    __tablename__ = "story_bible_entries"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    immutable_traits: Mapped[str] = mapped_column(Text, default="")
    mutable_attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reference_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    requires_reference_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SceneRecord(Base):
    __tablename__ = "scenes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scene_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    location_code: Mapped[str] = mapped_column(String(128), default="loc_default")
    time_of_day: Mapped[str] = mapped_column(String(64), default="day")
    lighting_mood: Mapped[str] = mapped_column(String(255), default="cinematic natural light")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShotRecord(Base):
    __tablename__ = "shots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    story_beat: Mapped[str] = mapped_column(Text, default="")

    subject_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    location_code: Mapped[str] = mapped_column(String(128), nullable=False)
    prop_codes: Mapped[list[str]] = mapped_column(JSON, default=list)

    shot_size: Mapped[str] = mapped_column(String(64), default="medium_shot")
    camera_angle: Mapped[str] = mapped_column(String(64), default="eye_level")
    camera_motion: Mapped[str] = mapped_column(String(64), default="static")
    composition: Mapped[str] = mapped_column(Text, default="subject centered")

    action: Mapped[str] = mapped_column(Text, default="")
    action_start: Mapped[str] = mapped_column(Text, default="")
    action_end: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[str] = mapped_column(Text, default="")
    continuity_notes: Mapped[str] = mapped_column(Text, default="")
    continuity_mode: Mapped[str] = mapped_column(String(32), default="standard")

    duration_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9")

    requires_reference_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    reference_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    first_frame_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_frame_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_shot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    next_shot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    prompt_final: Mapped[str] = mapped_column(Text, default="")
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")
    lint_report_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    approval_state: Mapped[ShotApprovalState] = mapped_column(
        Enum(ShotApprovalState), default=ShotApprovalState.NEEDS_REVIEW
    )
    generation_state: Mapped[ShotGenerationState] = mapped_column(
        Enum(ShotGenerationState), default=ShotGenerationState.NOT_STARTED
    )
    selected_take_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --- M3: WORKFLOW CANVAS & EXECUTION ENGINE MODELS ---

class WorkflowDefinition(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), default="1.0")
    nodes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    edges_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    ui_layout_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    run_mode: Mapped[str] = mapped_column(String(32), default="full")
    status: Mapped[str] = mapped_column(String(32), default="running")
    execution_plan_order: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NodeExecution(Base):
    __tablename__ = "node_executions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[NodeExecutionState] = mapped_column(
        Enum(NodeExecutionState), default=NodeExecutionState.IDLE
    )
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    logs: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowCacheEntry(Base):
    __tablename__ = "workflow_cache_entries"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    output_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- M4 & M5: ASYNC GENERATION QUEUE, TAKES & BATCH RENDER MODELS ---

class GenerationTaskRecord(Base):
    __tablename__ = "generation_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("provider_connections.id"), index=True)

    task_type: Mapped[str] = mapped_column(String(32), default="video")
    model_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=10, index=True)

    state: Mapped[QueueTaskState] = mapped_column(
        Enum(QueueTaskState), default=QueueTaskState.QUEUED, index=True
    )
    external_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    account_used: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Stable account identity; account_used remains the human-readable display label.
    account_id_used: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    poll_count: Mapped[int] = mapped_column(Integer, default=0)
    max_polls: Mapped[int] = mapped_column(Integer, default=60)
    next_poll_delay_seconds: Mapped[float] = mapped_column(Float, default=5.0)

    prompt_snapshot: Mapped[str] = mapped_column(Text, default="")
    negative_prompt_snapshot: Mapped[str] = mapped_column(Text, default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")

    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    history_log: Mapped[list[str]] = mapped_column(JSON, default=list)

    result_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    take_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GenerationDispatchLeaseRecord(Base):
    """Short-lived reservation preventing duplicate dispatch while a provider call is in flight."""

    __tablename__ = "generation_dispatch_leases"
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShotTakeRecord(Base):
    __tablename__ = "shot_takes"
    __table_args__ = (
        UniqueConstraint("shot_id", "take_number", name="uq_shot_takes_shot_number"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    take_number: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("generation_tasks.id", ondelete="CASCADE"))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    first_frame_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_frame_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    account_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    continuity_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    director_notes: Mapped[str] = mapped_column(Text, default="")
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BatchRenderJobRecord(Base):
    __tablename__ = "batch_render_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("provider_connections.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running")
    total_shots: Mapped[int] = mapped_column(Integer, default=0)
    enqueued_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)
    task_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    skipped_shot_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    blocked_shot_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- M6: TIMELINE EDITOR & FFMPEG EXPORT MODELS ---

class TimelineSequenceRecord(Base):
    __tablename__ = "timeline_sequences"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="Main Edit Sequence")
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9")
    fps: Mapped[int] = mapped_column(Integer, default=24)
    resolution_profile: Mapped[str] = mapped_column(String(32), default="1920x1080")
    total_duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    title_card_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    subtitle_format: Mapped[str] = mapped_column(String(16), default="srt")
    subtitle_content: Mapped[str] = mapped_column(Text, default="")
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TimelineTrackRecord(Base):
    __tablename__ = "timeline_tracks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence_id: Mapped[str] = mapped_column(ForeignKey("timeline_sequences.id", ondelete="CASCADE"), index=True)
    track_index: Mapped[int] = mapped_column(Integer, default=0)
    track_type: Mapped[str] = mapped_column(String(32), default="video")  # video | audio | voice_over | music | subtitle
    name: Mapped[str] = mapped_column(String(128), default="V1 - Main Video")
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    volume: Mapped[float] = mapped_column(Float, default=1.0)
    clips_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class TimelineClipRecord(Base):
    __tablename__ = "timeline_clips"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence_id: Mapped[str] = mapped_column(ForeignKey("timeline_sequences.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("timeline_tracks.id", ondelete="CASCADE"), index=True)
    shot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    take_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    source_duration_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    trim_start_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    trim_end_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    effective_duration_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    transition_type: Mapped[str] = mapped_column(String(32), default="hard_cut")  # hard_cut | cross_dissolve
    transition_duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=1.0)
    fade_in_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    fade_out_seconds: Mapped[float] = mapped_column(Float, default=0.0)


class ExportJobRecord(Base):
    __tablename__ = "export_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    sequence_id: Mapped[str] = mapped_column(ForeignKey("timeline_sequences.id", ondelete="CASCADE"), index=True)
    state: Mapped[ExportJobState] = mapped_column(
        Enum(ExportJobState), default=ExportJobState.QUEUED, index=True
    )
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9")
    fps: Mapped[int] = mapped_column(Integer, default=24)
    resolution: Mapped[str] = mapped_column(String(32), default="1920x1080")
    video_codec: Mapped[str] = mapped_column(String(32), default="h264")
    audio_codec: Mapped[str] = mapped_column(String(32), default="aac")
    ffmpeg_args_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    manifest_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ffprobe_verification_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackupRecord(Base):
    """M7: a project backup archive (ZIP stored under media_root/backups)."""

    __tablename__ = "project_backups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_rel_path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)




class ConnectionAccount(Base):
    """M7: one provider account under a ProviderConnection.

    A connection may hold several accounts (e.g. multiple muse2api API keys),
    each with its own optional proxy. Secrets are stored encrypted; API
    responses only ever expose masked values and the proxy display host.
    """

    __tablename__ = "connection_accounts"
    __table_args__ = (UniqueConstraint("connection_id", "label", name="uq_account_connection_label"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_masked: Mapped[str] = mapped_column(String(64), default="[NOT_SET]")
    encrypted_proxy_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_display_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    health_state: Mapped[ConnectionHealthState] = mapped_column(
        Enum(ConnectionHealthState), default=ConnectionHealthState.UNKNOWN
    )
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_error_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)




class StructuredLogRecord(Base):
    """M7: structured operational log entry with correlation ID (ported from PR #9)."""

    __tablename__ = "structured_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payload_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


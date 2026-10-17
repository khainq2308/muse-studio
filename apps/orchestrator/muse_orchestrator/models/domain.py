from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import String, Integer, Float, Boolean, ForeignKey, DateTime, Enum, JSON, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


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
    """Lưu lịch sử kịch bản gốc; phân tích AI không bao giờ tự động ghi đè bản gốc (Mục 3.2 PRD)."""
    __tablename__ = "script_revisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_format: Mapped[str] = mapped_column(String(32), default="markdown")  # markdown | museflow | txt | json
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_proposal_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    diff_summary: Mapped[str] = mapped_column(Text, default="")
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StoryBibleEntry(Base):
    """
    Quản lý thống nhất 4 danh mục Story Bible (Mục 3.3 PRD):
    entry_type: 'character' | 'location' | 'style' | 'prop'
    """
    __tablename__ = "story_bible_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)  # VD: char_conan, loc_cafe
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
    continuity_mode: Mapped[str] = mapped_column(String(32), default="standard")  # standard | frame_chain

    duration_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9")

    requires_reference_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    reference_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)

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
    """Lưu trữ tách biệt logic đồ thị (nodes/edges) và vị trí giao diện (ui_layout_json) theo Mục 3.6 PRD."""
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
    run_mode: Mapped[str] = mapped_column(String(32), default="full")  # full | dry_run | from_here | downstream
    status: Mapped[str] = mapped_column(String(32), default="running")  # running | waiting_approval | succeeded | failed | canceled
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

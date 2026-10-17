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

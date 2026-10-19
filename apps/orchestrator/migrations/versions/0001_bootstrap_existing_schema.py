"""Bootstrap existing schemas into Alembic history.

Revision ID: 0001_bootstrap
Revises:
Create Date: 2026-10-19

This one-time bridge creates any missing tables from current metadata and applies
the existing compatibility repair used by pre-Alembic installations. New schema
changes must be represented by explicit, immutable revisions after this baseline.
"""
from __future__ import annotations

from alembic import op

from muse_orchestrator.core.database import _ensure_schema_compatibility
from muse_orchestrator.models.domain import Base

revision = "0001_bootstrap"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Existing tables are preserved; absent tables are created. This allows databases
    # created before Alembic to adopt a revision marker without destructive recreation.
    Base.metadata.create_all(bind=bind, checkfirst=True)
    _ensure_schema_compatibility(bind)


def downgrade() -> None:
    # This bridge adopts an already-running schema; dropping every application table
    # would destroy data in upgraded installations and cannot be a safe generic rollback.
    raise RuntimeError(
        "The schema bootstrap bridge cannot be downgraded safely. "
        "Restore a tested database backup instead of dropping application tables."
    )

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from muse_orchestrator.core.database import get_db
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.config import settings
from muse_orchestrator.services.maintenance_service import (
    cleanup_orphan_media,
    scan_orphan_media,
)

router = APIRouter(prefix="/api/v1/maintenance", tags=["Maintenance"])


class OrphanScanIn(BaseModel):
    retention_hours: float = Field(default=24.0, ge=0.0, le=8760.0)


class OrphanCleanupIn(BaseModel):
    retention_hours: float = Field(default=24.0, ge=0.0, le=8760.0)
    dry_run: bool = True


@router.post("/orphans/scan")
async def scan_orphans(
    payload: OrphanScanIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """M7: list orphan media files without deleting anything."""
    orphans = await scan_orphan_media(db, settings.media_root, payload.retention_hours)
    return {
        "retention_hours": payload.retention_hours,
        "count": len(orphans),
        "total_bytes": sum(item.size_bytes for item in orphans),
        "orphans": [
            {"rel_path": item.rel_path, "size_bytes": item.size_bytes, "age_hours": item.age_hours}
            for item in orphans
        ],
    }


@router.post("/orphans/cleanup")
async def cleanup_orphans(
    payload: OrphanCleanupIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """M7: delete orphan media files (dry_run=true only reports)."""
    return await cleanup_orphan_media(
        db, settings.media_root, payload.retention_hours, dry_run=payload.dry_run
    )

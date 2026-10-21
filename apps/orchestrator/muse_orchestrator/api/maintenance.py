from __future__ import annotations

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


class OrphanFileOut(BaseModel):
    rel_path: str
    size_bytes: int
    age_hours: float


class OrphanScanOut(BaseModel):
    retention_hours: float
    count: int
    total_bytes: int
    orphans: list[OrphanFileOut]


class OrphanCleanupIn(BaseModel):
    retention_hours: float = Field(default=24.0, ge=0.0, le=8760.0)
    dry_run: bool = True


class OrphanCleanupOut(BaseModel):
    dry_run: bool
    retention_hours: float
    scanned_orphans: int
    deleted: int
    freed_bytes: int
    errors: list[str]
    orphans: list[OrphanFileOut]


@router.post("/orphans/scan", response_model=OrphanScanOut)
async def scan_orphans(
    payload: OrphanScanIn,
    db: AsyncSession = Depends(get_db),
) -> OrphanScanOut:
    """M7: list orphan media files without deleting anything."""
    orphans = await scan_orphan_media(db, settings.media_root, payload.retention_hours)
    return OrphanScanOut(
        retention_hours=payload.retention_hours,
        count=len(orphans),
        total_bytes=sum(item.size_bytes for item in orphans),
        orphans=[
            OrphanFileOut(rel_path=item.rel_path, size_bytes=item.size_bytes, age_hours=item.age_hours)
            for item in orphans
        ],
    )


@router.post("/orphans/cleanup", response_model=OrphanCleanupOut)
async def cleanup_orphans(
    payload: OrphanCleanupIn,
    db: AsyncSession = Depends(get_db),
) -> OrphanCleanupOut:
    """M7: delete orphan media files (dry_run=true only reports)."""
    result = await cleanup_orphan_media(
        db, settings.media_root, payload.retention_hours, dry_run=payload.dry_run
    )
    return OrphanCleanupOut.model_validate(result)

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import Asset, BackupRecord


@dataclass
class OrphanFile:
    rel_path: str
    size_bytes: int
    age_hours: float


async def _referenced_rel_paths(db: AsyncSession) -> set[str]:
    """All media-root-relative paths that are still referenced by the DB."""
    referenced: set[str] = set()
    asset_rows = (await db.execute(select(Asset.storage_rel_path))).all()
    referenced.update(row[0] for row in asset_rows)
    backup_rows = (await db.execute(select(BackupRecord.storage_rel_path))).all()
    referenced.update(row[0] for row in backup_rows)
    return referenced


def _iter_media_files(media_root: Path) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for dirpath, _dirnames, filenames in os.walk(media_root):
        for name in filenames:
            full = Path(dirpath) / name
            try:
                rel = full.relative_to(media_root).as_posix()
            except ValueError:
                continue
            found.append((full, rel))
    return found


async def scan_orphan_media(
    db: AsyncSession,
    media_root: str | None = None,
    retention_hours: float | None = None,
) -> list[OrphanFile]:
    """M7: find files under the media root that no DB record references.

    Only files older than ``retention_hours`` are reported, so in-flight
    uploads/exports are never flagged.
    """
    root = Path(media_root or settings.media_root).resolve()
    retention = retention_hours if retention_hours is not None else settings.orphan_cleanup_retention_hours
    if not root.is_dir():
        return []
    referenced = await _referenced_rel_paths(db)
    now = time.time()
    orphans: list[OrphanFile] = []
    for full, rel in _iter_media_files(root):
        if rel in referenced:
            continue
        try:
            stat = full.stat()
        except OSError:
            continue
        age_hours = (now - stat.st_mtime) / 3600.0
        if age_hours >= retention:
            orphans.append(OrphanFile(rel_path=rel, size_bytes=stat.st_size, age_hours=round(age_hours, 2)))
    orphans.sort(key=lambda item: item.rel_path)
    return orphans


async def cleanup_orphan_media(
    db: AsyncSession,
    media_root: str | None = None,
    retention_hours: float | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """M7: delete orphan media files (or just report them when dry_run)."""
    root = Path(media_root or settings.media_root).resolve()
    orphans = await scan_orphan_media(db, str(root), retention_hours)
    deleted = 0
    freed_bytes = 0
    errors: list[str] = []
    if not dry_run:
        for orphan in orphans:
            candidate = (root / orphan.rel_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                errors.append(f"{orphan.rel_path}: escaped media root, skipped")
                continue
            try:
                candidate.unlink(missing_ok=True)
                deleted += 1
                freed_bytes += orphan.size_bytes
            except OSError as exc:
                errors.append(f"{orphan.rel_path}: {exc}")
    if not dry_run and deleted:
        # M7: audit trail for destructive cleanup (ported from PR #9).
        from muse_orchestrator.application.system_service import write_audit
        from muse_orchestrator.infrastructure.repositories import StudioRepository

        await write_audit(
            StudioRepository(db),
            action="storage.cleanup",
            target_type="media_root",
            target_id="orphan_media",
            details={"deleted": deleted, "freed_bytes": freed_bytes, "errors": errors},
        )
    return {
        "dry_run": dry_run,
        "retention_hours": retention_hours if retention_hours is not None else settings.orphan_cleanup_retention_hours,
        "scanned_orphans": len(orphans),
        "deleted": deleted,
        "freed_bytes": freed_bytes,
        "errors": errors,
        "orphans": [
            {"rel_path": item.rel_path, "size_bytes": item.size_bytes, "age_hours": item.age_hours}
            for item in orphans
        ],
    }

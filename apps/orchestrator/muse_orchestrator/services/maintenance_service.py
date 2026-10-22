from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import Asset, BackupRecord, Project
from muse_orchestrator.services.asset_storage import normalize_stored_rel_path

# Orphan cleanup only ever considers files inside subtrees the orchestrator itself
# writes. Anything else under MEDIA_ROOT (operator files, lost+found, or everything
# in a mis-pointed MEDIA_ROOT such as a home directory) is never reported or deleted:
#   * <root>/<project dir>/...  where <project dir> is the storage directory of an
#     existing project, or matches the generated project-id pattern (deleted projects);
#   * <root>/backups/<project>/... (backup archives and their .part temp files);
#   * <root>/.asset-<hex>.part and <root>/.restore-<hex>.part (interrupted uploads/restores).
_GENERATED_PROJECT_DIR_RE = re.compile(r"^proj_[0-9a-f]{10}$")
_ROOT_TEMP_FILE_RE = re.compile(r"^\.(?:asset|restore)-[0-9a-f]{32}\.part$")
_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9._-]")


@dataclass
class OrphanFile:
    rel_path: str
    size_bytes: int
    age_hours: float


async def _stored_rel_paths(db: AsyncSession) -> list[str]:
    """Raw storage paths referenced by the DB (may be legacy non-canonical values)."""
    asset_rows = (await db.execute(select(Asset.storage_rel_path))).all()
    backup_rows = (await db.execute(select(BackupRecord.storage_rel_path))).all()
    return [row[0] for row in [*asset_rows, *backup_rows] if row[0]]


def _normalise_referenced(stored: list[str], media_root: str) -> set[str]:
    """Canonical forms of every stored path (blocking: resolves symlinks).

    Stored paths may be non-canonical (``../../real/proj/x`` computed against a
    symlinked MEDIA_ROOT). Both the raw and the canonical form are kept so a
    referenced file is never mistaken for an orphan.
    """
    referenced: set[str] = set()
    for value in stored:
        referenced.add(Path(value).as_posix())
        canonical = normalize_stored_rel_path(value, media_root)
        if canonical is not None:
            referenced.add(canonical)
    return referenced


async def _managed_project_dirs(db: AsyncSession) -> set[str]:
    project_ids = (await db.execute(select(Project.id))).scalars().all()
    return {_SAFE_SEGMENT_RE.sub("_", project_id) for project_id in project_ids} | set(project_ids)


def is_managed_media_path(rel_path: str, project_dirs: set[str]) -> bool:
    """True when ``rel_path`` lies in a subtree orphan cleanup may delete from."""
    parts = rel_path.split("/")
    if len(parts) == 1:
        return bool(_ROOT_TEMP_FILE_RE.match(parts[0]))
    head = parts[0]
    if head == "backups":
        return len(parts) >= 3
    return head in project_dirs or bool(_GENERATED_PROJECT_DIR_RE.match(head))


MIN_RETENTION_HOURS = 1.0


def _iter_media_files(media_root: Path) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    # followlinks=False (default): a symlinked directory is never descended into.
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

    Only files older than ``retention_hours`` and inside managed subtrees (see
    ``is_managed_media_path``) are reported, so in-flight uploads/exports and
    files the orchestrator did not write are never flagged.
    """
    configured_root = str(media_root or settings.media_root)
    root = Path(configured_root).resolve()
    retention = retention_hours if retention_hours is not None else settings.orphan_cleanup_retention_hours
    # Never report files younger than the floor: in-flight restores/exports write
    # media before their DB rows commit.
    retention = max(float(retention), MIN_RETENTION_HOURS)
    if not await asyncio.to_thread(root.is_dir):
        return []
    stored = await _stored_rel_paths(db)
    project_dirs = await _managed_project_dirs(db)
    return await asyncio.to_thread(
        _scan_orphans_sync, root, configured_root, stored, project_dirs, retention
    )


def _scan_orphans_sync(
    root: Path,
    configured_root: str,
    stored: list[str],
    project_dirs: set[str],
    retention: float,
) -> list[OrphanFile]:
    """Blocking normalise/os.walk/stat pass; run in a worker thread."""
    referenced = _normalise_referenced(stored, configured_root)
    now = time.time()
    orphans: list[OrphanFile] = []
    for full, rel in _iter_media_files(root):
        if rel in referenced or not is_managed_media_path(rel, project_dirs):
            continue
        try:
            stat = full.lstat()
        except OSError:
            continue
        age_hours = (now - stat.st_mtime) / 3600.0
        if age_hours >= retention:
            orphans.append(OrphanFile(rel_path=rel, size_bytes=stat.st_size, age_hours=round(age_hours, 2)))
    orphans.sort(key=lambda item: item.rel_path)
    return orphans


def _delete_orphans_sync(root: Path, orphans: list[OrphanFile]) -> tuple[int, int, list[str]]:
    """Blocking unlink pass; run in a worker thread."""
    deleted = 0
    freed_bytes = 0
    errors: list[str] = []
    for orphan in orphans:
        # Unlink the lexical path rather than resolving the file itself: if it is
        # a symlink, resolving first could delete its target instead of the link.
        candidate = root / orphan.rel_path
        try:
            candidate.parent.resolve().relative_to(root)
        except ValueError:
            errors.append(f"{orphan.rel_path}: parent escaped media root, skipped")
            continue
        try:
            candidate.unlink(missing_ok=True)
            deleted += 1
            freed_bytes += orphan.size_bytes
        except OSError as exc:
            errors.append(f"{orphan.rel_path}: {exc.strerror or type(exc).__name__}")
    return deleted, freed_bytes, errors


async def cleanup_orphan_media(
    db: AsyncSession,
    media_root: str | None = None,
    retention_hours: float | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """M7: delete orphan media files (or just report them when dry_run).

    Deletion is limited to managed subtrees and refused outright when MEDIA_ROOT
    resolves to a filesystem root.
    """
    configured_root = str(media_root or settings.media_root)
    root = Path(configured_root).resolve()
    orphans = await scan_orphan_media(db, configured_root, retention_hours)
    deleted = 0
    freed_bytes = 0
    errors: list[str] = []
    if not dry_run and root == Path(root.anchor):
        errors.append("MEDIA_ROOT resolves to a filesystem root; cleanup refused.")
    elif not dry_run:
        deleted, freed_bytes, errors = await asyncio.to_thread(_delete_orphans_sync, root, orphans)
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

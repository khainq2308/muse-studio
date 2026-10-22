from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from muse_orchestrator.api.dependencies import get_backup_service
from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.models.domain import BackupRecord
from muse_orchestrator.services import backup_service as backup_module
from muse_orchestrator.services.backup_service import BackupService

router = APIRouter(tags=["Backups"])


class BackupManifestOut(BaseModel):
    """Typed known fields while retaining forward-compatible manifest metadata."""

    format_version: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    created_at: datetime | None = None
    tables: dict[str, int] = Field(default_factory=dict)
    excluded_tables: list[str] = Field(default_factory=list)
    missing_media: list[str] = Field(default_factory=list)
    media_files: int | None = None

    model_config = {"extra": "allow"}


class BackupOut(BaseModel):
    id: str
    project_id: str
    filename: str
    size_bytes: int
    sha256: str
    manifest_json: BackupManifestOut
    created_at: datetime

    model_config = {"from_attributes": True}


class BackupRestoreSkippedRowOut(BaseModel):
    table: str
    original_id: str
    reason: str


class BackupRestoreReportOut(BaseModel):
    tables_restored: dict[str, int]
    rows_skipped: dict[str, int]
    skipped_rows: list[BackupRestoreSkippedRowOut] = Field(default_factory=list)
    media_restored: int
    media_missing: list[str]
    tasks_neutralized: int = 0
    exports_neutralized: int = 0
    tasks_with_placeholder_connection: int = 0


class BackupRestoreOut(BaseModel):
    project_id: str
    project_name: str
    report: BackupRestoreReportOut


def _to_backup_out(record: BackupRecord) -> BackupOut:
    return BackupOut(
        id=record.id,
        project_id=record.project_id,
        filename=record.filename,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        manifest_json=record.manifest_json,
        created_at=record.created_at,
    )


@router.post(
    "/api/v1/projects/{project_id}/backup",
    response_model=BackupOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_backup(
    project_id: str,
    service: BackupService = Depends(get_backup_service),
) -> BackupOut:
    """M7: snapshot a project (DB rows + media) into a ZIP archive."""
    return _to_backup_out(await service.create_backup(project_id))


@router.get("/api/v1/projects/{project_id}/backups", response_model=list[BackupOut])
async def list_backups(
    project_id: str,
    service: BackupService = Depends(get_backup_service),
) -> list[BackupOut]:
    return [_to_backup_out(record) for record in await service.list_backups(project_id)]


@router.get(
    "/api/v1/backups/{backup_id}/download",
    response_class=FileResponse,
    responses={
        200: {
            "content": {
                "application/zip": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    },
)
async def download_backup(
    backup_id: str,
    service: BackupService = Depends(get_backup_service),
) -> FileResponse:
    """M7: download the backup ZIP file."""
    record, path = await service.get_backup_download(backup_id)
    return FileResponse(
        path=str(path),
        filename=record.filename,
        media_type="application/zip",
    )


@router.delete("/api/v1/backups/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(
    backup_id: str,
    service: BackupService = Depends(get_backup_service),
) -> None:
    await service.delete_backup(backup_id)


@router.post("/api/v1/backups/restore", response_model=BackupRestoreOut, status_code=status.HTTP_201_CREATED)
async def restore_backup(
    file: UploadFile,
    project_name: str | None = Query(default=None, min_length=1, max_length=255),
    service: BackupService = Depends(get_backup_service),
) -> BackupRestoreOut:
    """M7: restore a project from an uploaded backup ZIP (IDs are remapped)."""
    temporary_path: Path | None = None
    try:
        # Keep the upload on disk; calling bytes(content) after accumulating a bytearray
        # creates another full-size copy of potentially 500MB in the API worker.
        with NamedTemporaryFile(prefix="muse_restore_", suffix=".zip", delete=False) as target:
            temporary_path = Path(target.name)
            total = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > backup_module.backup_limits().max_archive_bytes:
                    raise ApplicationError(413, "Backup file exceeds the 500MB limit.")
                await asyncio.to_thread(target.write, chunk)

        project, report = await service.restore_backup(temporary_path, project_name=project_name)
    finally:
        await file.close()
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return BackupRestoreOut(project_id=project.id, project_name=project.name, report=report)

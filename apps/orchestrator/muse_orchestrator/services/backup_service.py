from __future__ import annotations

import asyncio
import enum
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.application.system_service import write_audit
from muse_orchestrator.core.config import settings
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import (
    Asset,
    BackupRecord,
    ConnectionAccount,
    ConnectionHealthState,
    ExportJobRecord,
    ExportJobState,
    GenerationTaskRecord,
    Project,
    ProjectRevision,
    ProviderConnection,
    QueueTaskState,
    SceneRecord,
    ScriptRevision,
    ShotGenerationState,
    ShotRecord,
    ShotTakeRecord,
    StoryBibleEntry,
    TimelineClipRecord,
    TimelineSequenceRecord,
    TimelineTrackRecord,
    WorkflowDefinition,
    WorkflowVersion,
)
from muse_orchestrator.services.asset_storage import (
    AssetStorageError,
    LocalAssetStorage,
    SavedAssetFile,
)
from muse_orchestrator.services.workflow_engine import (
    WorkflowEngine,
    WorkflowValidationError,
)

BACKUP_FORMAT_VERSION = "m7-backup-1"
BACKUP_MAX_BYTES = 500 * 1024 * 1024
BACKUP_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
BACKUP_MAX_ENTRIES = 10_000
# JSON table entries are parsed fully in memory; cap them far below the archive limit.
BACKUP_MAX_JSON_ENTRY_BYTES = 50 * 1024 * 1024
# Restored media may be long export MP4s, so the per-file cap follows the archive cap
# rather than the interactive upload limit.
RESTORE_MAX_MEDIA_BYTES = BACKUP_MAX_BYTES
RESTORE_ALLOWED_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "video/mp4"})
RESTORED_PLACEHOLDER_CONNECTION_ID = "conn_restored_unavailable"
_RESTORED_PLACEHOLDER_CONNECTION_NAME = "[restored] unavailable provider connection"
_TERMINAL_TASK_STATES = frozenset({
    QueueTaskState.SUCCEEDED,
    QueueTaskState.FAILED,
    QueueTaskState.POLL_TIMEOUT,
    QueueTaskState.CANCELED,
})
_TERMINAL_EXPORT_STATES = frozenset({
    ExportJobState.SUCCEEDED,
    ExportJobState.FAILED,
    ExportJobState.CANCELED,
})
_IN_FLIGHT_SHOT_STATES = frozenset({ShotGenerationState.QUEUED, ShotGenerationState.RUNNING})
_RESTORED_TASK_MESSAGE = (
    "Task was not finished when the backup was taken; restored tasks are never resumed. "
    "Create a new generation to retry."
)
_RESTORED_EXPORT_MESSAGE = (
    "Export was not finished when the backup was taken; restored exports are never resumed. "
    "Start a new export to retry."
)
# Keys inside export snapshots that carry filesystem paths or render commands.
_UNTRUSTED_SNAPSHOT_KEYS = frozenset({"resolved_clips", "ffmpeg_args", "output_path"})

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass
class _TableSpec:
    key: str
    model: Any
    id_prefix: str
    # attribute -> table key whose id map should be applied ("__keep__" = leave value untouched)
    fks: dict[str, str]


# Dependency order: parents before children (matters for restore inserts).
_TABLES: list[_TableSpec] = [
    _TableSpec("projects", Project, "proj", {}),
    _TableSpec("project_revisions", ProjectRevision, "rev", {"project_id": "projects"}),
    _TableSpec("script_revisions", ScriptRevision, "scr", {"project_id": "projects"}),
    _TableSpec("story_bible_entries", StoryBibleEntry, "sbe", {"project_id": "projects"}),
    _TableSpec("scenes", SceneRecord, "scn", {"project_id": "projects"}),
    _TableSpec(
        "shots", ShotRecord, "sht", {"project_id": "projects", "scene_id": "scenes"}
    ),
    _TableSpec("workflows", WorkflowDefinition, "wfl", {"project_id": "projects"}),
    _TableSpec(
        "workflow_versions",
        WorkflowVersion,
        "wfv",
        {"workflow_id": "workflows"},
    ),
    _TableSpec(
        "generation_tasks",
        GenerationTaskRecord,
        "tsk",
        {
            "project_id": "projects",
            "scene_id": "scenes",
            "shot_id": "shots",
            "connection_id": "__keep__",
            "account_id_used": "__keep__",
        },
    ),
    _TableSpec(
        "assets",
        Asset,
        "ast",
        {"project_id": "projects", "scene_id": "scenes", "shot_id": "shots"},
    ),
    _TableSpec(
        "shot_takes",
        ShotTakeRecord,
        "tke",
        {
            "project_id": "projects",
            "shot_id": "shots",
            "task_id": "generation_tasks",
            "asset_id": "assets",
            "first_frame_asset_id": "assets",
            "last_frame_asset_id": "assets",
        },
    ),
    _TableSpec("timeline_sequences", TimelineSequenceRecord, "seq", {"project_id": "projects"}),
    _TableSpec("timeline_tracks", TimelineTrackRecord, "trk", {"sequence_id": "timeline_sequences"}),
    _TableSpec(
        "timeline_clips",
        TimelineClipRecord,
        "clp",
        {
            "sequence_id": "timeline_sequences",
            "track_id": "timeline_tracks",
            "shot_id": "shots",
            "take_id": "shot_takes",
            "asset_id": "assets",
        },
    ),
    _TableSpec(
        "export_jobs",
        ExportJobRecord,
        "exp",
        {
            "project_id": "projects",
            "sequence_id": "timeline_sequences",
            "output_asset_id": "assets",
        },
    ),
]

# Tables intentionally left out of backups (operational, reproducible, or meta).
EXCLUDED_TABLES = [
    "audit_events",
    "workflow_runs",
    "node_executions",
    "workflow_cache_entries",
    "batch_render_jobs",
    "project_backups",
]


def _row_to_dict(obj: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in obj.__table__.columns:
        value = getattr(obj, column.key)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, enum.Enum):
            value = value.value
        data[column.key] = value
    return data


def _dict_to_row(model: Any, data: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    for column in model.__table__.columns:
        if column.key not in data:
            continue
        value = data[column.key]
        if value is None:
            kwargs[column.key] = None
            continue
        col_type = column.type
        if isinstance(col_type, SAEnum) and isinstance(value, str):
            value = col_type.enum_class(value)
        elif str(col_type).upper().startswith("DATETIME") and isinstance(value, str):
            value = datetime.fromisoformat(value)
        kwargs[column.key] = value
    return model(**kwargs)


def _backup_dir(media_root: str, project_id: str) -> Path:
    safe_project = _SAFE_SEGMENT_RE.sub("_", project_id)
    target = (Path(media_root).resolve() / "backups" / safe_project).resolve()
    root = Path(media_root).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ApplicationError(400, "Invalid project id for backup path.") from exc
    target.mkdir(parents=True, exist_ok=True)
    return target


def _strip_untrusted_snapshot_keys(value: Any) -> Any:
    """Recursively drop path/command keys an archive could use to steer FFmpeg."""
    if isinstance(value, dict):
        return {
            key: _strip_untrusted_snapshot_keys(item)
            for key, item in value.items()
            if key not in _UNTRUSTED_SNAPSHOT_KEYS
            and not (isinstance(key, str) and (key == "path" or key.endswith("_path")))
        }
    if isinstance(value, list):
        return [_strip_untrusted_snapshot_keys(item) for item in value]
    return value


def _write_backup_archive(
    tmp_zip: Path,
    zip_path: Path,
    tables: dict[str, list[dict[str, Any]]],
    manifest: dict[str, Any],
    storage: LocalAssetStorage,
) -> tuple[int, str]:
    """Blocking ZIP/hash work for create_backup; must run in a worker thread."""
    media_files = 0
    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for key, rows in tables.items():
                archive.writestr(f"data/{key}.json", json.dumps(rows, ensure_ascii=False))
            for asset_row in tables["assets"]:
                rel_path = asset_row.get("storage_rel_path")
                if not rel_path:
                    continue
                try:
                    full = storage.resolve_safe_path(rel_path)
                except (AssetStorageError, OSError):
                    manifest["missing_media"].append(rel_path)
                    continue
                archive.write(full, f"media/{rel_path}")
                media_files += 1
                if tmp_zip.stat().st_size > BACKUP_MAX_BYTES:
                    raise ApplicationError(413, "Generated backup exceeds the 500MB limit.")
            manifest["media_files"] = media_files
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))

        size_bytes = tmp_zip.stat().st_size
        if size_bytes > BACKUP_MAX_BYTES:
            raise ApplicationError(413, "Generated backup exceeds the 500MB limit.")

        hasher = hashlib.sha256()
        with tmp_zip.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)

        # Atomic promotion: a failed or interrupted backup is never exposed as complete.
        os.replace(tmp_zip, zip_path)
    except Exception:
        try:
            tmp_zip.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return size_bytes, hasher.hexdigest()


def _open_restore_archive(zip_source: bytes | Path) -> zipfile.ZipFile:
    """Open and structurally validate an uploaded archive (blocking)."""
    if isinstance(zip_source, bytes):
        if len(zip_source) > BACKUP_MAX_BYTES:
            raise ApplicationError(413, "Backup file exceeds the 500MB limit.")
        archive_source: io.BytesIO | Path = io.BytesIO(zip_source)
    else:
        archive_source = Path(zip_source)
        try:
            archive_size = archive_source.stat().st_size
        except OSError as exc:
            raise ApplicationError(422, "Uploaded backup file is unavailable.") from exc
        if archive_size > BACKUP_MAX_BYTES:
            raise ApplicationError(413, "Backup file exceeds the 500MB limit.")
    try:
        archive = zipfile.ZipFile(archive_source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ApplicationError(422, "Uploaded file is not a valid ZIP archive.") from exc

    try:
        infos = archive.infolist()
        if len(infos) > BACKUP_MAX_ENTRIES:
            raise ApplicationError(413, "Backup ZIP contains too many entries.")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ApplicationError(422, "Backup ZIP contains duplicate entry names.")
        if sum(info.file_size for info in infos) > BACKUP_MAX_UNCOMPRESSED_BYTES:
            raise ApplicationError(413, "Backup ZIP exceeds the uncompressed size limit.")
        for info in infos:
            if info.filename.startswith("/") or any(part == ".." for part in info.filename.split("/")):
                raise ApplicationError(422, "Backup ZIP contains an unsafe entry path.")
            if info.flag_bits & 0x1:
                raise ApplicationError(422, "Encrypted backup ZIP entries are not supported.")
            if info.file_size > BACKUP_MAX_BYTES:
                raise ApplicationError(413, "A backup ZIP entry exceeds the size limit.")
            is_json_entry = info.filename == "manifest.json" or (
                info.filename.startswith("data/") and info.filename.endswith(".json")
            )
            if is_json_entry and info.file_size > BACKUP_MAX_JSON_ENTRY_BYTES:
                raise ApplicationError(413, "A backup JSON entry exceeds the 50MB limit.")
    except BaseException:
        archive.close()
        raise
    return archive


def _read_json_entry(archive: zipfile.ZipFile, name: str) -> Any:
    """Read one JSON entry with a hard byte cap (header sizes are attacker-controlled)."""
    with archive.open(name, "r") as source:
        raw = source.read(BACKUP_MAX_JSON_ENTRY_BYTES + 1)
    if len(raw) > BACKUP_MAX_JSON_ENTRY_BYTES:
        raise ApplicationError(413, "A backup JSON entry exceeds the 50MB limit.")
    return json.loads(raw)


def _validate_workflow_version_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        nodes_json = row.get("nodes_json") or []
        edges_json = row.get("edges_json") or []
        if not isinstance(nodes_json, list) or not isinstance(edges_json, list):
            raise ApplicationError(422, "Backup contains an invalid workflow graph.")
        try:
            nodes, edges = WorkflowEngine.parse_graph_json(nodes_json, edges_json)
            WorkflowEngine.validate_and_plan(nodes, edges)
        except (WorkflowValidationError, AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ApplicationError(422, "Backup contains an invalid workflow graph.") from exc


def _load_restore_payload(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Parse manifest and table JSON (blocking); validates shapes and workflow graphs."""
    try:
        manifest = _read_json_entry(archive, "manifest.json")
    except KeyError as exc:
        raise ApplicationError(422, "Backup ZIP has no manifest.json.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise ApplicationError(422, "Backup ZIP manifest is invalid or corrupted.") from exc
    if not isinstance(manifest, dict):
        raise ApplicationError(422, "Backup ZIP manifest must be a JSON object.")
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ApplicationError(
            422,
            f"Unsupported backup format: {str(manifest.get('format_version'))[:64]!r} "
            f"(expected {BACKUP_FORMAT_VERSION!r}).",
        )

    tables: dict[str, list[dict[str, Any]]] = {}
    for spec in _TABLES:
        try:
            rows = _read_json_entry(archive, f"data/{spec.key}.json")
        except KeyError:
            rows = []
        except (json.JSONDecodeError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
            raise ApplicationError(422, f"Backup table data is invalid: {spec.key}.") from exc
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ApplicationError(422, f"Backup table data has an invalid shape: {spec.key}.")
        tables[spec.key] = rows
    _validate_workflow_version_rows(tables["workflow_versions"])
    return manifest, tables


def _extract_asset_media(
    archive: zipfile.ZipFile,
    entry: str,
    storage: LocalAssetStorage,
    project_id: str,
    filename: str,
    declared_mime: str,
) -> SavedAssetFile:
    """Stream one archived media file into storage (blocking).

    Mirrors LocalAssetStorage.save_stream but with the restore size limit so
    long exported MP4s (larger than the interactive upload cap) can be restored.
    """
    if declared_mime not in RESTORE_ALLOWED_MIME_TYPES:
        raise AssetStorageError("MIME type is not allowed for restore.")
    info = archive.getinfo(entry)
    if info.file_size > RESTORE_MAX_MEDIA_BYTES:
        raise AssetStorageError("Media entry exceeds the restore size limit.")
    safe_name = storage.sanitize_filename(filename)
    temp_path = storage.root_path / f".restore-{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    total = 0
    prefix = bytearray()
    try:
        with archive.open(info, "r") as source, temp_path.open("xb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > RESTORE_MAX_MEDIA_BYTES:
                    raise AssetStorageError("Media entry exceeds the restore size limit.")
                if len(prefix) < 12:
                    prefix.extend(chunk[: 12 - len(prefix)])
                digest.update(chunk)
                output.write(chunk)
        verified_mime = storage.verify_mime_signature(bytes(prefix), declared_mime)
        digest_hex = digest.hexdigest()
        relative_directory = Path(_SAFE_SEGMENT_RE.sub("_", project_id)) / digest_hex[:2]
        target_directory = (storage.root_path / relative_directory).resolve()
        target_directory.relative_to(storage.root_path)
        target_directory.mkdir(parents=True, exist_ok=True)
        final_filename = f"{digest_hex[:12]}_{uuid.uuid4().hex[:8]}_{safe_name}"
        full_path = (target_directory / final_filename).resolve()
        full_path.relative_to(storage.root_path)
        os.replace(temp_path, full_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return SavedAssetFile(
        safe_filename=safe_name,
        storage_rel_path=(relative_directory / final_filename).as_posix(),
        full_disk_path=str(full_path),
        size_bytes=total,
        sha256=digest_hex,
        detected_mime=verified_mime,
    )


def _unlink_files(paths: list[str]) -> None:
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


class BackupService:
    """M7: project backup (ZIP) and restore with ID remapping."""

    def __init__(
        self,
        repository: StudioRepository,
        storage: LocalAssetStorage | None = None,
    ) -> None:
        self.repository = repository
        self.storage = storage or LocalAssetStorage(settings.media_root)

    async def create_backup(self, project_id: str) -> BackupRecord:
        db = self.repository.session
        project = await db.get(Project, project_id)
        if project is None:
            raise ApplicationError(404, "Project not found.")

        tables: dict[str, list[dict[str, Any]]] = {}
        sequence_ids: list[str] = []
        workflow_ids: list[str] = []
        for spec in _TABLES:
            if spec.key in ("timeline_tracks", "timeline_clips"):
                stmt = select(spec.model).where(
                    spec.model.sequence_id.in_(sequence_ids)
                )
            elif spec.key == "workflow_versions":
                stmt = select(spec.model).where(spec.model.workflow_id.in_(workflow_ids))
            elif spec.key == "projects":
                stmt = select(spec.model).where(spec.model.id == project_id)
            else:
                stmt = select(spec.model).where(spec.model.project_id == project_id)
            rows = (await db.execute(stmt)).scalars().all()
            tables[spec.key] = [_row_to_dict(row) for row in rows]
            if spec.key == "timeline_sequences":
                sequence_ids = [row["id"] for row in tables[spec.key]]
            elif spec.key == "workflows":
                workflow_ids = [row["id"] for row in tables[spec.key]]

        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "project_id": project_id,
            "project_name": project.name,
            "created_at": datetime.now(UTC).isoformat(),
            "tables": {key: len(rows) for key, rows in tables.items()},
            "excluded_tables": EXCLUDED_TABLES,
            "missing_media": [],
        }

        backup_id = f"bkp_{uuid.uuid4().hex[:10]}"
        filename = f"{backup_id}.zip"
        target_dir = await asyncio.to_thread(_backup_dir, settings.media_root, project_id)
        zip_path = target_dir / filename

        # Keep the ZIP on disk end-to-end (no read_bytes() of the archive), and run
        # the blocking zip/sha256/file I/O in a worker thread off the event loop.
        tmp_zip = target_dir / f".{backup_id}.part"
        size_bytes, sha256_hex = await asyncio.to_thread(
            _write_backup_archive, tmp_zip, zip_path, tables, manifest, self.storage
        )

        record = BackupRecord(
            id=backup_id,
            project_id=project_id,
            filename=filename,
            storage_rel_path=zip_path.relative_to(Path(settings.media_root).resolve()).as_posix(),
            size_bytes=size_bytes,
            sha256=sha256_hex,
            manifest_json=manifest,
        )
        try:
            record = await self.repository.create_backup_record(record)
        except Exception:
            # Don't leave a completed archive with no corresponding DB record.
            await asyncio.to_thread(_unlink_files, [str(zip_path)])
            raise
        pruned = await self._prune_old_backups(project_id)
        await write_audit(
            self.repository,
            action="backup.create",
            target_type="project_backup",
            target_id=record.id,
            details={"project_id": project_id, "size_bytes": record.size_bytes, "pruned": pruned},
        )
        return record

    async def _prune_old_backups(self, project_id: str) -> int:
        """Keep only the newest N backups per project (config: backup_retention_count)."""
        keep = max(1, settings.backup_retention_count)
        records = await self.repository.list_backup_records(project_id)
        pruned = 0
        for record in records[keep:]:
            await asyncio.to_thread(self._unlink_backup_file, record.storage_rel_path)
            await self.repository.delete_backup_record(record)
            pruned += 1
        return pruned

    async def list_backups(self, project_id: str) -> list[BackupRecord]:
        db = self.repository.session
        if await db.get(Project, project_id) is None:
            raise ApplicationError(404, "Project not found.")
        return await self.repository.list_backup_records(project_id)

    async def delete_backup(self, backup_id: str) -> None:
        record = await self.repository.get_backup_record(backup_id)
        if record is None:
            raise ApplicationError(404, "Backup not found.")
        await asyncio.to_thread(self._unlink_backup_file, record.storage_rel_path)
        await self.repository.delete_backup_record(record)
        await write_audit(
            self.repository,
            action="backup.delete",
            target_type="project_backup",
            target_id=backup_id,
            details={"project_id": record.project_id},
        )

    def _unlink_backup_file(self, storage_rel_path: str) -> None:
        """Best-effort delete of a backup archive; a missing file is not an error."""
        try:
            self.storage.resolve_safe_path(storage_rel_path).unlink(missing_ok=True)
        except (AssetStorageError, OSError):
            return

    def backup_file_path(self, record: BackupRecord) -> Path:
        try:
            return self.storage.resolve_safe_path(record.storage_rel_path)
        except Exception as exc:
            raise ApplicationError(404, "Backup file is missing from disk.") from exc

    async def restore_backup(
        self, zip_source: bytes | Path, project_name: str | None = None
    ) -> tuple[Project, dict[str, Any]]:
        """Restore a project from bytes or a temporary ZIP path, remapping all IDs.

        Restored data is treated as untrusted: in-flight tasks/exports become
        terminal, archive-supplied paths are discarded, workflow graphs are
        validated and media is re-verified. Any failure rolls back the
        transaction and deletes media files already written by this restore.
        """
        archive = await asyncio.to_thread(_open_restore_archive, zip_source)
        try:
            manifest, tables = await asyncio.to_thread(_load_restore_payload, archive)
            written_files: list[str] = []
            try:
                id_maps, report = await self._insert_restored_rows(archive, tables, written_files)
            except IntegrityError as exc:
                await self._abort_restore(written_files)
                raise ApplicationError(
                    409, "Restore conflicts with existing data; nothing was restored."
                ) from exc
            except BaseException:
                await self._abort_restore(written_files)
                raise
        finally:
            await asyncio.to_thread(archive.close)

        db = self.repository.session
        new_project_id = id_maps["projects"].get(manifest.get("project_id", ""))
        project = await db.get(Project, new_project_id) if new_project_id else None
        if project is None:
            raise ApplicationError(500, "Restore completed but the new project was not found.")
        if project_name:
            project.name = project_name
            await db.commit()
            await db.refresh(project)
        await write_audit(
            self.repository,
            action="backup.restore",
            target_type="project",
            target_id=project.id,
            details={
                "project_name": project.name,
                "tables_restored": report.get("tables_restored"),
                "rows_skipped": report.get("rows_skipped"),
                "tasks_neutralized": report.get("tasks_neutralized"),
                "exports_neutralized": report.get("exports_neutralized"),
            },
        )
        return project, report

    async def _abort_restore(self, written_files: list[str]) -> None:
        try:
            await self.repository.session.rollback()
        finally:
            await asyncio.to_thread(_unlink_files, list(written_files))

    async def _placeholder_connection_id(self) -> str:
        """Disabled, credential-less connection that keeps terminal history rows valid.

        Provider connections are environment-scoped and never restored. Tasks whose
        connection is absent here are re-pointed to this placeholder (they are always
        terminal after restore) so their shot takes and selected take survive.
        """
        db = self.repository.session
        if await db.get(ProviderConnection, RESTORED_PLACEHOLDER_CONNECTION_ID) is None:
            db.add(ProviderConnection(
                id=RESTORED_PLACEHOLDER_CONNECTION_ID,
                name=_RESTORED_PLACEHOLDER_CONNECTION_NAME,
                provider_type="restored_placeholder",
                base_url="about:blank",
                encrypted_api_key=None,
                api_key_masked="[NOT_SET]",
                enabled=False,
                max_concurrency=0,
                health_state=ConnectionHealthState.UNAVAILABLE,
                last_error_redacted=(
                    "Placeholder for restored history whose original provider connection "
                    "does not exist on this instance. It cannot dispatch work."
                ),
            ))
        return RESTORED_PLACEHOLDER_CONNECTION_ID

    @staticmethod
    def _skip(report: dict[str, Any], table: str, row: dict[str, Any], reason: str) -> None:
        report["rows_skipped"][table] = report["rows_skipped"].get(table, 0) + 1
        report["skipped_rows"].append({
            "table": table,
            "original_id": str(row.get("id") or "")[:64],
            "reason": reason,
        })

    def _neutralize_generation_task(
        self, row: dict[str, Any], report: dict[str, Any], now: datetime
    ) -> None:
        # external_task_id would let the poller hit a provider with a foreign task id.
        row["external_task_id"] = None
        state = row.get("state")
        try:
            parsed = QueueTaskState(state) if state is not None else QueueTaskState.QUEUED
        except ValueError:
            parsed = QueueTaskState.QUEUED
        if parsed not in _TERMINAL_TASK_STATES:
            row["state"] = QueueTaskState.FAILED.value
            row["error_category"] = "restored_incomplete"
            row["retryable"] = False
            row["error_message_redacted"] = _RESTORED_TASK_MESSAGE
            row["finished_at"] = now.isoformat()
            row["history_log"] = [
                str(item)[:500] for item in (row.get("history_log") or []) if isinstance(item, str)
            ][-50:] + [_RESTORED_TASK_MESSAGE]
            report["tasks_neutralized"] += 1

    @staticmethod
    def _neutralize_export_job(row: dict[str, Any], report: dict[str, Any], now: datetime) -> None:
        row["ffmpeg_args_json"] = []
        snapshot = row.get("manifest_snapshot_json")
        row["manifest_snapshot_json"] = (
            _strip_untrusted_snapshot_keys(snapshot) if isinstance(snapshot, dict) else {}
        )
        state = row.get("state")
        try:
            parsed = ExportJobState(state) if state is not None else ExportJobState.QUEUED
        except ValueError:
            parsed = ExportJobState.QUEUED
        if parsed not in _TERMINAL_EXPORT_STATES:
            row["state"] = ExportJobState.FAILED.value
            row["error_message_redacted"] = _RESTORED_EXPORT_MESSAGE
            row["finished_at"] = now.isoformat()
            report["exports_neutralized"] += 1

    async def _insert_restored_rows(
        self,
        archive: zipfile.ZipFile,
        tables: dict[str, list[dict[str, Any]]],
        written_files: list[str],
    ) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        db = self.repository.session
        now = datetime.now(UTC)
        available_connection_ids = set((await db.execute(select(ProviderConnection.id))).scalars().all())
        available_account_ids = set((await db.execute(select(ConnectionAccount.id))).scalars().all())
        id_maps: dict[str, dict[str, str]] = {}
        restored_rows: dict[str, list[tuple[dict[str, Any], Any]]] = {}
        report: dict[str, Any] = {
            "tables_restored": {},
            "rows_skipped": {},
            "skipped_rows": [],
            "media_restored": 0,
            "media_missing": [],
            "tasks_neutralized": 0,
            "exports_neutralized": 0,
            "tasks_with_placeholder_connection": 0,
        }
        for spec in _TABLES:
            report["rows_skipped"][spec.key] = 0
            id_map: dict[str, str] = {}
            new_objects: list[Any] = []
            for original_row in tables.get(spec.key, []):
                row = dict(original_row)
                # If a referenced row was skipped, null nullable references or skip
                # the row when a required FK cannot be mapped safely.
                skip_reason: str | None = None
                for attr, target_key in spec.fks.items():
                    value = row.get(attr)
                    if target_key == "__keep__" or value is None or target_key not in id_maps:
                        continue
                    mapped = id_maps[target_key].get(value)
                    if mapped is None:
                        column = next((c for c in spec.model.__table__.columns if c.key == attr), None)
                        if column is not None and column.nullable:
                            row[attr] = None
                        else:
                            skip_reason = f"missing required reference {attr}"
                            break
                    else:
                        row[attr] = mapped
                if skip_reason is not None:
                    self._skip(report, spec.key, original_row, skip_reason)
                    continue

                new_id = f"{spec.id_prefix}_{uuid.uuid4().hex[:10]}"

                if spec.key == "generation_tasks":
                    if row.get("connection_id") not in available_connection_ids:
                        row["connection_id"] = await self._placeholder_connection_id()
                        available_connection_ids.add(row["connection_id"])
                        report["tasks_with_placeholder_connection"] += 1
                    if row.get("account_id_used") and row["account_id_used"] not in available_account_ids:
                        row["account_id_used"] = None
                        row["account_used"] = None
                    # The original key is unique per instance; a same-instance restore would collide.
                    row["idempotency_key"] = f"restored:{new_id}"[:128]
                    self._neutralize_generation_task(row, report, now)
                elif spec.key == "export_jobs":
                    self._neutralize_export_job(row, report, now)
                elif spec.key == "shots":
                    if row.get("generation_state") in {state.value for state in _IN_FLIGHT_SHOT_STATES}:
                        row["generation_state"] = ShotGenerationState.FAILED.value
                elif spec.key == "assets":
                    # Never trust archive-supplied paths/sizes: only rows whose bytes are
                    # re-stored and re-verified here are kept.
                    saved = await self._restore_asset_media(archive, row, written_files)
                    if saved is None:
                        report["media_missing"].append(str(row.get("filename") or "")[:255])
                        self._skip(report, spec.key, original_row, "media missing or invalid")
                        continue
                    row["storage_rel_path"] = saved.storage_rel_path
                    row["size_bytes"] = saved.size_bytes
                    row["sha256"] = saved.sha256
                    row["mime_type"] = saved.detected_mime
                    report["media_restored"] += 1

                old_id = original_row.get("id")
                if old_id:
                    id_map[old_id] = new_id
                row["id"] = new_id
                try:
                    obj = _dict_to_row(spec.model, row)
                except (TypeError, ValueError) as exc:
                    raise ApplicationError(422, f"Backup table data has invalid values: {spec.key}.") from exc
                new_objects.append(obj)
                restored_rows.setdefault(spec.key, []).append((dict(original_row), obj))
            id_maps[spec.key] = id_map
            report["tables_restored"][spec.key] = len(new_objects)
            db.add_all(new_objects)

        self._remap_late_references(id_maps, restored_rows)
        await db.commit()
        return id_maps, report

    @staticmethod
    def _remap_late_references(
        id_maps: dict[str, dict[str, str]],
        restored_rows: dict[str, list[tuple[dict[str, Any], Any]]],
    ) -> None:
        """Fix references to tables that appear later in the restore dependency order."""
        assets = id_maps.get("assets", {})
        takes = id_maps.get("shot_takes", {})
        shots = id_maps.get("shots", {})
        for original_row, obj in restored_rows.get("shots", []):
            obj.selected_take_id = takes.get(original_row.get("selected_take_id"))
            obj.first_frame_asset_id = assets.get(original_row.get("first_frame_asset_id"))
            obj.last_frame_asset_id = assets.get(original_row.get("last_frame_asset_id"))
            obj.previous_shot_id = shots.get(original_row.get("previous_shot_id"))
            obj.next_shot_id = shots.get(original_row.get("next_shot_id"))
            obj.reference_asset_ids = [assets[value] for value in (original_row.get("reference_asset_ids") or []) if value in assets]
        for original_row, obj in restored_rows.get("story_bible_entries", []):
            obj.reference_asset_ids = [assets[value] for value in (original_row.get("reference_asset_ids") or []) if value in assets]
        for original_row, obj in restored_rows.get("generation_tasks", []):
            obj.result_asset_id = assets.get(original_row.get("result_asset_id"))
            obj.take_id = takes.get(original_row.get("take_id"))
        for _original_row, trk_obj in restored_rows.get("timeline_tracks", []):
            remapped_clips: list[dict[str, Any]] = []
            for clip_item in (trk_obj.clips_json or []):
                if not isinstance(clip_item, dict):
                    continue
                c_copy = dict(clip_item)
                if c_copy.get("shot_id"):
                    c_copy["shot_id"] = shots.get(c_copy["shot_id"])
                if c_copy.get("take_id"):
                    c_copy["take_id"] = takes.get(c_copy["take_id"])
                if c_copy.get("asset_id"):
                    c_copy["asset_id"] = assets.get(c_copy["asset_id"])
                remapped_clips.append(c_copy)
            trk_obj.clips_json = remapped_clips
        for _original_row, seq_obj in restored_rows.get("timeline_sequences", []):
            if isinstance(seq_obj.manifest_json, dict) and seq_obj.manifest_json:
                manifest_copy = dict(seq_obj.manifest_json)
                manifest_copy["sequence_id"] = seq_obj.id
                manifest_copy["project_id"] = seq_obj.project_id
                if isinstance(manifest_copy.get("video_clips"), list):
                    v_clips: list[dict[str, Any]] = []
                    for vc in manifest_copy["video_clips"]:
                        if isinstance(vc, dict):
                            vc_copy = dict(vc)
                            if vc_copy.get("shot_id"):
                                vc_copy["shot_id"] = shots.get(vc_copy["shot_id"])
                            if vc_copy.get("take_id"):
                                vc_copy["take_id"] = takes.get(vc_copy["take_id"])
                            if vc_copy.get("asset_id"):
                                vc_copy["asset_id"] = assets.get(vc_copy["asset_id"])
                            v_clips.append(vc_copy)
                    manifest_copy["video_clips"] = v_clips
                if isinstance(manifest_copy.get("audio_tracks"), list):
                    a_tracks: list[dict[str, Any]] = []
                    for at in manifest_copy["audio_tracks"]:
                        if isinstance(at, dict):
                            at_copy = dict(at)
                            if at_copy.get("asset_id"):
                                at_copy["asset_id"] = assets.get(at_copy["asset_id"])
                            a_tracks.append(at_copy)
                    manifest_copy["audio_tracks"] = a_tracks
                seq_obj.manifest_json = manifest_copy

    async def _restore_asset_media(
        self,
        archive: zipfile.ZipFile,
        row: dict[str, Any],
        written_files: list[str],
    ) -> SavedAssetFile | None:
        """Re-store and re-verify an asset's bytes under the new project.

        Returns None when the bytes are absent, oversized, or fail the MIME
        allowlist/signature check; the caller then skips the asset row instead
        of keeping an archive-supplied storage path.
        """
        original_rel_path = row.get("storage_rel_path")
        mime_type = row.get("mime_type")
        if not isinstance(original_rel_path, str) or not original_rel_path:
            return None
        if not isinstance(mime_type, str) or mime_type not in RESTORE_ALLOWED_MIME_TYPES:
            return None
        try:
            saved = await asyncio.to_thread(
                _extract_asset_media,
                archive,
                f"media/{original_rel_path}",
                self.storage,
                str(row.get("project_id") or ""),
                str(row.get("filename") or "restored_asset"),
                mime_type,
            )
        except (KeyError, AssetStorageError, OSError, ValueError, zipfile.BadZipFile):
            return None
        written_files.append(saved.full_disk_path)
        return saved

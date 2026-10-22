from __future__ import annotations

import enum
import os
import hashlib
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from muse_orchestrator.services.asset_storage import LocalAssetStorage
from sqlalchemy import Enum as SAEnum
from sqlalchemy import select

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.application.system_service import write_audit
from muse_orchestrator.core.config import settings
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import (
    Asset,
    BackupRecord,
    ConnectionAccount,
    ProviderConnection,
    ExportJobRecord,
    GenerationTaskRecord,
    Project,
    ProjectRevision,
    SceneRecord,
    ScriptRevision,
    ShotRecord,
    ShotTakeRecord,
    StoryBibleEntry,
    TimelineClipRecord,
    TimelineSequenceRecord,
    TimelineTrackRecord,
    WorkflowDefinition,
    WorkflowVersion,
)

BACKUP_FORMAT_VERSION = "m7-backup-1"
BACKUP_MAX_BYTES = 500 * 1024 * 1024
BACKUP_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
BACKUP_MAX_ENTRIES = 10_000

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
        target_dir = _backup_dir(settings.media_root, project_id)
        zip_path = target_dir / filename

        media_files = 0
        # Keep the ZIP on disk end-to-end. Reading the final archive with read_bytes()
        # doubles peak memory for large projects and can OOM the API container.
        tmp_zip = target_dir / f".{backup_id}.part"
        try:
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                for key, rows in tables.items():
                    archive.writestr(f"data/{key}.json", json.dumps(rows, ensure_ascii=False))
                for asset_row in tables["assets"]:
                    rel_path = asset_row.get("storage_rel_path")
                    if not rel_path:
                        continue
                    try:
                        full = self.storage.resolve_safe_path(rel_path)
                    except Exception:
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

        record = BackupRecord(
            id=backup_id,
            project_id=project_id,
            filename=filename,
            storage_rel_path=zip_path.relative_to(Path(settings.media_root).resolve()).as_posix(),
            size_bytes=size_bytes,
            sha256=hasher.hexdigest(),
            manifest_json=manifest,
        )
        try:
            record = await self.repository.create_backup_record(record)
        except Exception:
            # Don't leave a completed archive with no corresponding DB record.
            zip_path.unlink(missing_ok=True)
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
            try:
                full = self.storage.resolve_safe_path(record.storage_rel_path)
                full.unlink(missing_ok=True)
            except Exception:
                pass
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
        try:
            full = self.storage.resolve_safe_path(record.storage_rel_path)
            full.unlink(missing_ok=True)
        except Exception:
            pass
        await self.repository.delete_backup_record(record)
        await write_audit(
            self.repository,
            action="backup.delete",
            target_type="project_backup",
            target_id=backup_id,
            details={"project_id": record.project_id},
        )

    def backup_file_path(self, record: BackupRecord) -> Path:
        try:
            return self.storage.resolve_safe_path(record.storage_rel_path)
        except Exception as exc:
            raise ApplicationError(404, "Backup file is missing from disk.") from exc

    async def restore_backup(
        self, zip_source: bytes | Path, project_name: str | None = None
    ) -> tuple[Project, dict[str, Any]]:
        """Restore a project from bytes or a temporary ZIP path, remapping all IDs."""
        if isinstance(zip_source, bytes):
            if len(zip_source) > BACKUP_MAX_BYTES:
                raise ApplicationError(413, "Backup file exceeds the 500MB limit.")
            archive_source: bytes | Path = io.BytesIO(zip_source)
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

        with archive:
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
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except KeyError as exc:
                raise ApplicationError(422, "Backup ZIP has no manifest.json.") from exc
            except (json.JSONDecodeError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
                raise ApplicationError(422, "Backup ZIP manifest is invalid or corrupted.") from exc
            if not isinstance(manifest, dict):
                raise ApplicationError(422, "Backup ZIP manifest must be a JSON object.")
            if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
                raise ApplicationError(
                    422,
                    f"Unsupported backup format: {manifest.get('format_version')!r} "
                    f"(expected {BACKUP_FORMAT_VERSION!r}).",
                )

            db = self.repository.session
            available_connection_ids = set((await db.execute(select(ProviderConnection.id))).scalars().all())
            available_account_ids = set((await db.execute(select(ConnectionAccount.id))).scalars().all())
            id_maps: dict[str, dict[str, str]] = {}
            restored_rows: dict[str, list[tuple[dict[str, Any], Any]]] = {}
            report: dict[str, Any] = {
                "tables_restored": {},
                "rows_skipped": {},
                "media_restored": 0,
                "media_missing": [],
            }
            for spec in _TABLES:
                try:
                    rows = json.loads(archive.read(f"data/{spec.key}.json"))
                except KeyError:
                    rows = []
                except (json.JSONDecodeError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
                    raise ApplicationError(422, f"Backup table data is invalid: {spec.key}.") from exc
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ApplicationError(422, f"Backup table data has an invalid shape: {spec.key}.")

                original_count = len(rows)
                if spec.key == "generation_tasks":
                    accepted = []
                    for item in rows:
                        # Provider connections and credentials are environment-scoped, not project data.
                        if item.get("connection_id") not in available_connection_ids:
                            continue
                        account_id = item.get("account_id_used")
                        if item.get("external_task_id") and account_id and account_id not in available_account_ids:
                            # Cannot poll an in-flight upstream task safely without the exact account.
                            continue
                        clean_item = dict(item)
                        if account_id and account_id not in available_account_ids:
                            clean_item["account_id_used"] = None
                            clean_item["account_used"] = None
                        accepted.append(clean_item)
                    rows = accepted
                report["rows_skipped"][spec.key] = original_count - len(rows)

                id_map: dict[str, str] = {}
                new_objects: list[Any] = []
                accepted_original_rows: list[dict[str, Any]] = []
                for original_row in rows:
                    row = dict(original_row)
                    # If a referenced row was skipped, null nullable references or skip
                    # the row when a required FK cannot be mapped safely.
                    skip_row = False
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
                                skip_row = True
                                break
                        else:
                            row[attr] = mapped
                    if skip_row:
                        report["rows_skipped"][spec.key] += 1
                        continue
                    old_id = row.get("id")
                    new_id = f"{spec.id_prefix}_{uuid.uuid4().hex[:10]}"
                    if old_id:
                        id_map[old_id] = new_id
                    row["id"] = new_id
                    obj = _dict_to_row(spec.model, row)
                    new_objects.append(obj)
                    accepted_original_rows.append(dict(original_row))
                    restored_rows.setdefault(spec.key, []).append((dict(original_row), obj))
                id_maps[spec.key] = id_map
                report["tables_restored"][spec.key] = len(new_objects)
                db.add_all(new_objects)

                if spec.key == "assets":
                    for archived_row, obj in zip(accepted_original_rows, new_objects):
                        await self._restore_asset_media(
                            archive, obj, archived_row.get("storage_rel_path"), report
                        )

            # Fix references to tables that appear later in the restore dependency order.
            for original_row, obj in restored_rows.get("shots", []):
                obj.selected_take_id = id_maps.get("shot_takes", {}).get(original_row.get("selected_take_id"))
                obj.first_frame_asset_id = id_maps.get("assets", {}).get(original_row.get("first_frame_asset_id"))
                obj.last_frame_asset_id = id_maps.get("assets", {}).get(original_row.get("last_frame_asset_id"))
                obj.previous_shot_id = id_maps.get("shots", {}).get(original_row.get("previous_shot_id"))
                obj.next_shot_id = id_maps.get("shots", {}).get(original_row.get("next_shot_id"))
                obj.reference_asset_ids = [id_maps["assets"][value] for value in (original_row.get("reference_asset_ids") or []) if value in id_maps.get("assets", {})]
            for original_row, obj in restored_rows.get("story_bible_entries", []):
                obj.reference_asset_ids = [id_maps["assets"][value] for value in (original_row.get("reference_asset_ids") or []) if value in id_maps.get("assets", {})]
            for original_row, obj in restored_rows.get("generation_tasks", []):
                obj.result_asset_id = id_maps.get("assets", {}).get(original_row.get("result_asset_id"))
                obj.take_id = id_maps.get("shot_takes", {}).get(original_row.get("take_id"))
            for _original_row, trk_obj in restored_rows.get("timeline_tracks", []):
                remapped_clips: list[dict[str, Any]] = []
                for clip_item in (trk_obj.clips_json or []):
                    if not isinstance(clip_item, dict):
                        continue
                    c_copy = dict(clip_item)
                    if c_copy.get("shot_id"):
                        c_copy["shot_id"] = id_maps.get("shots", {}).get(c_copy["shot_id"])
                    if c_copy.get("take_id"):
                        c_copy["take_id"] = id_maps.get("shot_takes", {}).get(c_copy["take_id"])
                    if c_copy.get("asset_id"):
                        c_copy["asset_id"] = id_maps.get("assets", {}).get(c_copy["asset_id"])
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
                                    vc_copy["shot_id"] = id_maps.get("shots", {}).get(vc_copy["shot_id"])
                                if vc_copy.get("take_id"):
                                    vc_copy["take_id"] = id_maps.get("shot_takes", {}).get(vc_copy["take_id"])
                                if vc_copy.get("asset_id"):
                                    vc_copy["asset_id"] = id_maps.get("assets", {}).get(vc_copy["asset_id"])
                                v_clips.append(vc_copy)
                        manifest_copy["video_clips"] = v_clips
                    if isinstance(manifest_copy.get("audio_tracks"), list):
                        a_tracks: list[dict[str, Any]] = []
                        for at in manifest_copy["audio_tracks"]:
                            if isinstance(at, dict):
                                at_copy = dict(at)
                                if at_copy.get("asset_id"):
                                    at_copy["asset_id"] = id_maps.get("assets", {}).get(at_copy["asset_id"])
                                a_tracks.append(at_copy)
                        manifest_copy["audio_tracks"] = a_tracks
                    seq_obj.manifest_json = manifest_copy

            await db.commit()

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
            details={"project_name": project.name, "tables_restored": report.get("tables_restored")},
        )
        return project, report

    async def _restore_asset_media(
        self,
        archive: zipfile.ZipFile,
        asset: Asset,
        original_rel_path: str | None,
        report: dict[str, Any],
    ) -> None:
        """Re-store an asset's bytes under the new project.

        When the bytes are absent from the archive (recorded in
        ``manifest["missing_media"]`` at backup time), the row keeps its
        original path — on the same instance the file is usually still on
        disk — and the filename is reported.
        """
        if not original_rel_path:
            report["media_missing"].append(asset.filename)
            return
        entry = f"media/{original_rel_path}"
        if entry not in archive.namelist():
            report["media_missing"].append(asset.filename)
            return
        try:
            with archive.open(entry, "r") as source:
                saved = self.storage.save_stream(
                    asset.project_id, asset.filename, source, asset.mime_type
                )
        except Exception:
            report["media_missing"].append(asset.filename)
            return
        asset.storage_rel_path = saved.storage_rel_path
        asset.size_bytes = saved.size_bytes
        asset.sha256 = saved.sha256
        report["media_restored"] += 1

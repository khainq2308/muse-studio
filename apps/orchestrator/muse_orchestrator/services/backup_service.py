from __future__ import annotations

import asyncio
import enum
import hashlib
import io
import json
import math
import os
import re
import threading
import uuid
import zipfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, select
from sqlalchemy import Enum as SAEnum
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
    media_rel_path,
)
from muse_orchestrator.services.workflow_engine import (
    WorkflowEngine,
    WorkflowValidationError,
)

BACKUP_FORMAT_VERSION = "m7-backup-1"

# --- Backup limits -------------------------------------------------------------------------
# One set of limits shared by create and restore (read through backup_limits() at call
# time), so a backup this instance creates is never one its own restore rejects.
BACKUP_MAX_BYTES = 500 * 1024 * 1024
BACKUP_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
BACKUP_MAX_ENTRIES = 10_000
# JSON table entries are parsed fully in memory; cap them far below the archive limit,
# per entry and across all entries.
BACKUP_MAX_JSON_ENTRY_BYTES = 50 * 1024 * 1024
BACKUP_MAX_TOTAL_JSON_BYTES = 64 * 1024 * 1024
BACKUP_MAX_ROWS_PER_TABLE = 50_000
BACKUP_MAX_TOTAL_ROWS = 200_000
# Media is already compressed (PNG/JPEG/WebP/MP4): a media entry that inflates more than
# this is a decompression bomb, not a real asset. Created backups store media uncompressed.
BACKUP_MAX_COMPRESSION_RATIO = 100
_COMPRESSION_RATIO_MIN_BYTES = 1024 * 1024
# Restored media may be long export MP4s, so the per-file cap follows the archive cap
# rather than the interactive upload limit.
RESTORE_MAX_MEDIA_BYTES = BACKUP_MAX_BYTES
RESTORE_MAX_SKIPPED_ROWS_REPORTED = 200
# JSON column values deeper than this are refused (bounded recursion everywhere later).
_MAX_JSON_VALUE_DEPTH = 64
_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1

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


@dataclass(frozen=True)
class BackupLimits:
    max_archive_bytes: int
    max_uncompressed_bytes: int
    max_entries: int
    max_json_entry_bytes: int
    max_total_json_bytes: int
    max_rows_per_table: int
    max_total_rows: int
    max_media_bytes: int
    max_compression_ratio: int


def backup_limits() -> BackupLimits:
    """Current limits (module constants are read at call time so tests/ops can tune them)."""
    return BackupLimits(
        max_archive_bytes=BACKUP_MAX_BYTES,
        max_uncompressed_bytes=BACKUP_MAX_UNCOMPRESSED_BYTES,
        max_entries=BACKUP_MAX_ENTRIES,
        max_json_entry_bytes=BACKUP_MAX_JSON_ENTRY_BYTES,
        max_total_json_bytes=BACKUP_MAX_TOTAL_JSON_BYTES,
        max_rows_per_table=BACKUP_MAX_ROWS_PER_TABLE,
        max_total_rows=BACKUP_MAX_TOTAL_ROWS,
        max_media_bytes=RESTORE_MAX_MEDIA_BYTES,
        max_compression_ratio=BACKUP_MAX_COMPRESSION_RATIO,
    )


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

# --- Per-table value rules ------------------------------------------------------------------
# Column types/lengths/enums/NOT NULL come from the SQLAlchemy model. These add the API
# bounds for values that drive rendering, so a restored row cannot bypass them
# (api/timeline.py ClipEditItemIn/AudioTrackItemIn, api/projects.py, api/pipeline.py).
_MAX_TIMELINE_START_SECONDS = 86_400.0
_MAX_CLIP_SECONDS = 600.0
_MAX_SEQUENCE_SECONDS = _MAX_TIMELINE_START_SECONDS + _MAX_CLIP_SECONDS + 60.0
_ASPECT_RATIOS = frozenset({"16:9", "9:16", "1:1", "21:9"})
_RESOLUTION_RE = re.compile(r"^\d{2,4}x\d{2,4}$")
_FPS_BOUNDS = (12, 60)

_NUMERIC_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "projects": {"fps": _FPS_BOUNDS},
    "shots": {"duration_seconds": (0.0, _MAX_CLIP_SECONDS)},
    "timeline_sequences": {"fps": _FPS_BOUNDS, "total_duration_seconds": (0.0, _MAX_SEQUENCE_SECONDS)},
    "timeline_tracks": {"volume": (0.0, 2.0)},
    "timeline_clips": {
        "start_time_seconds": (0.0, _MAX_TIMELINE_START_SECONDS),
        "source_duration_seconds": (0.0, _MAX_CLIP_SECONDS),
        "trim_start_seconds": (0.0, _MAX_CLIP_SECONDS),
        "trim_end_seconds": (0.0, _MAX_CLIP_SECONDS),
        "effective_duration_seconds": (0.0, _MAX_CLIP_SECONDS),
        "transition_duration_seconds": (0.0, 5.0),
        "volume": (0.0, 2.0),
        "fade_in_seconds": (0.0, 10.0),
        "fade_out_seconds": (0.0, 10.0),
    },
    "export_jobs": {"fps": _FPS_BOUNDS},
}
_CHOICES: dict[str, dict[str, frozenset[str]]] = {
    "projects": {"aspect_ratio": _ASPECT_RATIOS},
    "timeline_sequences": {"aspect_ratio": _ASPECT_RATIOS, "subtitle_format": frozenset({"srt", "vtt"})},
    "timeline_clips": {"transition_type": frozenset({"hard_cut", "cross_dissolve"})},
}
_PATTERNS: dict[str, dict[str, re.Pattern[str]]] = {
    "timeline_sequences": {"resolution_profile": _RESOLUTION_RE},
    "export_jobs": {"resolution": _RESOLUTION_RE},
}


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


def _json_depth_ok(value: Any, max_depth: int) -> bool:
    """Iterative depth check (never recurses, so it cannot raise RecursionError)."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (list, dict)):
            if depth > max_depth:
                return False
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)
        elif isinstance(item, float) and not math.isfinite(item):
            return False
    return True


def _invalid_value(table: str, column: str) -> ApplicationError:
    # Never echo archive values: they are attacker-controlled and may be huge.
    return ApplicationError(422, f"Backup table data has invalid values: {table}.{column}.")


def _column_value_ok(column: Any, value: Any) -> bool:
    col_type = column.type
    if isinstance(col_type, SAEnum):
        return isinstance(value, str) and value in {member.value for member in col_type.enum_class}
    if isinstance(col_type, String):
        if not isinstance(value, str):
            return False
        return col_type.length is None or len(value) <= col_type.length
    if isinstance(col_type, Boolean):
        return isinstance(value, bool)
    if isinstance(col_type, Integer):
        return isinstance(value, int) and not isinstance(value, bool) and _INT32_MIN <= value <= _INT32_MAX
    if isinstance(col_type, Float):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if isinstance(col_type, DateTime):
        if not isinstance(value, str) or len(value) > 64:
            return False
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True
    if isinstance(col_type, JSON):
        return _json_depth_ok(value, _MAX_JSON_VALUE_DEPTH)
    return False


def _validate_row(spec: _TableSpec, row: dict[str, Any]) -> None:
    """Check one archived row against its table schema and API bounds (raises 422)."""
    for column in spec.model.__table__.columns:
        key = column.key
        if key not in row:
            required = (
                not column.nullable
                and not column.primary_key
                and column.default is None
                and column.server_default is None
            )
            if required:
                raise _invalid_value(spec.key, key)
            continue
        value = row[key]
        if value is None:
            if column.nullable or column.primary_key:
                continue
            raise _invalid_value(spec.key, key)
        if not _column_value_ok(column, value):
            raise _invalid_value(spec.key, key)
    for key, (low, high) in _NUMERIC_BOUNDS.get(spec.key, {}).items():
        value = row.get(key)
        if value is not None and not low <= value <= high:
            raise _invalid_value(spec.key, key)
    for key, allowed in _CHOICES.get(spec.key, {}).items():
        value = row.get(key)
        if value is not None and value not in allowed:
            raise _invalid_value(spec.key, key)
    for key, pattern in _PATTERNS.get(spec.key, {}).items():
        value = row.get(key)
        if value is not None and not pattern.match(value):
            raise _invalid_value(spec.key, key)


def _validate_tables(manifest: dict[str, Any], tables: dict[str, list[dict[str, Any]]], limits: BackupLimits) -> None:
    """Shared create/restore validation: row caps, per-row schema, single matching project."""
    total_rows = 0
    for spec in _TABLES:
        rows = tables.get(spec.key, [])
        if len(rows) > limits.max_rows_per_table:
            raise ApplicationError(413, f"Backup table has too many rows: {spec.key}.")
        total_rows += len(rows)
        if total_rows > limits.max_total_rows:
            raise ApplicationError(413, "Backup contains too many rows in total.")
    for spec in _TABLES:
        for row in tables.get(spec.key, []):
            _validate_row(spec, row)
    projects = tables.get("projects", [])
    manifest_project = manifest.get("project_id")
    if (
        len(projects) != 1
        or not isinstance(manifest_project, str)
        or projects[0].get("id") != manifest_project
    ):
        raise ApplicationError(
            422, "Backup must contain exactly one project matching manifest.project_id."
        )


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


def _collect_backup_media(
    tables: dict[str, list[dict[str, Any]]],
    manifest: dict[str, Any],
    storage: LocalAssetStorage,
    limits: BackupLimits,
) -> list[tuple[Path, str, int]]:
    """Resolve each asset file once, rewriting rows to canonical media-root paths.

    Legacy rows may hold non-canonical paths (``../../real/proj/x``); the archive entry
    and the archived row both use the canonical form so restore can find the bytes.
    """
    media: list[tuple[Path, str, int]] = []
    seen: set[str] = set()
    for asset_row in tables["assets"]:
        rel_path = asset_row.get("storage_rel_path")
        if not rel_path:
            continue
        try:
            full = storage.resolve_safe_path(rel_path)
            canonical = media_rel_path(full, storage.root_path)
            size = full.stat().st_size
        except (AssetStorageError, OSError):
            manifest["missing_media"].append(rel_path)
            continue
        asset_row["storage_rel_path"] = canonical
        if canonical in seen:
            continue
        if size > limits.max_media_bytes:
            raise ApplicationError(413, "A media file exceeds the backup/restore size limit.")
        seen.add(canonical)
        media.append((full, canonical, size))
    return media


def _write_backup_archive(
    tmp_zip: Path,
    zip_path: Path,
    tables: dict[str, list[dict[str, Any]]],
    manifest: dict[str, Any],
    storage: LocalAssetStorage,
) -> tuple[int, str]:
    """Blocking validation/ZIP/hash work for create_backup; must run in a worker thread.

    Enforces every limit restore enforces (DATA-009) before the archive is promoted; a
    rejected backup leaves no file behind.
    """
    limits = backup_limits()
    try:
        _validate_tables(manifest, tables, limits)
        media = _collect_backup_media(tables, manifest, storage, limits)
        manifest["media_files"] = len(media)
        payloads = {
            f"data/{key}.json": json.dumps(rows, ensure_ascii=False, allow_nan=False).encode("utf-8")
            for key, rows in tables.items()
        }
        payloads["manifest.json"] = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if any(len(data) > limits.max_json_entry_bytes for data in payloads.values()):
            raise ApplicationError(413, "Generated backup has a JSON entry above the restore limit.")
        json_total = sum(len(data) for data in payloads.values())
        if json_total > limits.max_total_json_bytes:
            raise ApplicationError(413, "Generated backup JSON exceeds the restore limit.")
        if len(payloads) + len(media) > limits.max_entries:
            raise ApplicationError(413, "Generated backup has too many entries.")
        if json_total + sum(size for _full, _rel, size in media) > limits.max_uncompressed_bytes:
            raise ApplicationError(413, "Generated backup exceeds the uncompressed size limit.")

        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in payloads.items():
                if name != "manifest.json":
                    archive.writestr(name, data)
            for full, canonical, _size in media:
                # Media is already compressed; storing it keeps the ratio at ~1:1 so
                # the restore-side bomb check can never reject a genuine backup.
                archive.write(full, f"media/{canonical}", compress_type=zipfile.ZIP_STORED)
                if tmp_zip.stat().st_size > limits.max_archive_bytes:
                    raise ApplicationError(413, "Generated backup exceeds the 500MB limit.")
            archive.writestr("manifest.json", payloads["manifest.json"])

        with zipfile.ZipFile(tmp_zip) as written:
            infos = written.infolist()
            if any(
                info.filename.startswith("media/") and info.file_size > limits.max_media_bytes
                for info in infos
            ) or sum(info.file_size for info in infos) > limits.max_uncompressed_bytes:
                # A media file grew between stat() and write().
                raise ApplicationError(413, "Generated backup exceeds the restore size limits.")

        size_bytes = tmp_zip.stat().st_size
        if size_bytes > limits.max_archive_bytes:
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
    limits = backup_limits()
    if isinstance(zip_source, bytes):
        if len(zip_source) > limits.max_archive_bytes:
            raise ApplicationError(413, "Backup file exceeds the 500MB limit.")
        archive_source: io.BytesIO | Path = io.BytesIO(zip_source)
    else:
        archive_source = Path(zip_source)
        try:
            archive_size = archive_source.stat().st_size
        except OSError as exc:
            raise ApplicationError(422, "Uploaded backup file is unavailable.") from exc
        if archive_size > limits.max_archive_bytes:
            raise ApplicationError(413, "Backup file exceeds the 500MB limit.")
    try:
        archive = zipfile.ZipFile(archive_source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ApplicationError(422, "Uploaded file is not a valid ZIP archive.") from exc

    try:
        infos = archive.infolist()
        if len(infos) > limits.max_entries:
            raise ApplicationError(413, "Backup ZIP contains too many entries.")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ApplicationError(422, "Backup ZIP contains duplicate entry names.")
        # ZipExtFile never yields more than the header file_size, so header sizes are
        # a hard upper bound on what extraction can write.
        if sum(info.file_size for info in infos) > limits.max_uncompressed_bytes:
            raise ApplicationError(413, "Backup ZIP exceeds the uncompressed size limit.")
        for info in infos:
            if info.filename.startswith("/") or any(part == ".." for part in info.filename.split("/")):
                raise ApplicationError(422, "Backup ZIP contains an unsafe entry path.")
            if info.flag_bits & 0x1:
                raise ApplicationError(422, "Encrypted backup ZIP entries are not supported.")
            if info.file_size > limits.max_archive_bytes:
                raise ApplicationError(413, "A backup ZIP entry exceeds the size limit.")
            is_json_entry = info.filename == "manifest.json" or (
                info.filename.startswith("data/") and info.filename.endswith(".json")
            )
            if is_json_entry and info.file_size > limits.max_json_entry_bytes:
                raise ApplicationError(413, "A backup JSON entry exceeds the 50MB limit.")
            if (
                info.filename.startswith("media/")
                and info.file_size > _COMPRESSION_RATIO_MIN_BYTES
                and info.file_size > limits.max_compression_ratio * max(info.compress_size, 1)
            ):
                raise ApplicationError(413, "A backup media entry has a suspicious compression ratio.")
    except BaseException:
        archive.close()
        raise
    return archive


def _reject_json_constant(name: str) -> Any:
    raise ValueError(f"Non-finite JSON constant {name[:16]!r} is not allowed.")


class _JsonBudget:
    """Running total of JSON bytes read from one archive."""

    def __init__(self, limits: BackupLimits) -> None:
        self.limits = limits
        self.used = 0


def _read_json_entry(archive: zipfile.ZipFile, name: str, budget: _JsonBudget) -> Any:
    """Read one JSON entry with hard byte caps (header sizes are attacker-controlled)."""
    limits = budget.limits
    remaining = limits.max_total_json_bytes - budget.used
    cap = min(limits.max_json_entry_bytes, max(remaining, 0))
    with archive.open(name, "r") as source:
        raw = source.read(cap + 1)
    if len(raw) > limits.max_json_entry_bytes:
        raise ApplicationError(413, "A backup JSON entry exceeds the 50MB limit.")
    if len(raw) > cap:
        raise ApplicationError(413, "Backup JSON data exceeds the total size limit.")
    budget.used += len(raw)
    return json.loads(raw, parse_constant=_reject_json_constant)


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


_JSON_PARSE_ERRORS = (ValueError, RecursionError, zipfile.BadZipFile)  # JSONDecodeError/UnicodeDecodeError are ValueErrors


def _load_restore_payload(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Parse and validate manifest and table JSON (blocking).

    Rejects (before anything is written) oversized or non-finite JSON, wrong shapes,
    rows that do not match their table schema/API bounds, invalid workflow graphs and
    archives whose single project does not match manifest.project_id.
    """
    limits = backup_limits()
    budget = _JsonBudget(limits)
    try:
        manifest = _read_json_entry(archive, "manifest.json", budget)
    except KeyError as exc:
        raise ApplicationError(422, "Backup ZIP has no manifest.json.") from exc
    except _JSON_PARSE_ERRORS as exc:
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
            rows = _read_json_entry(archive, f"data/{spec.key}.json", budget)
        except KeyError:
            rows = []
        except _JSON_PARSE_ERRORS as exc:
            raise ApplicationError(422, f"Backup table data is invalid: {spec.key}.") from exc
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ApplicationError(422, f"Backup table data has an invalid shape: {spec.key}.")
        tables[spec.key] = rows
    _validate_tables(manifest, tables, limits)
    _validate_workflow_version_rows(tables["workflow_versions"])
    return manifest, tables


class _RestoreByteBudget:
    """Running total of bytes written by one restore, shared with the worker thread."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def consume(self, count: int) -> None:
        with self._lock:
            self.used += count
            if self.used > self.limit:
                raise ApplicationError(413, "Restore exceeds the uncompressed size limit.")


class _RestoreCancelled(Exception):
    pass


def _extract_asset_media(
    archive: zipfile.ZipFile,
    entry: str,
    storage: LocalAssetStorage,
    project_id: str,
    filename: str,
    declared_mime: str,
    written_files: list[str] | None = None,
    budget: _RestoreByteBudget | None = None,
    cancel_event: threading.Event | None = None,
) -> SavedAssetFile:
    """Stream one archived media file into storage (blocking).

    Mirrors LocalAssetStorage.save_stream but with the restore size limit so
    long exported MP4s (larger than the interactive upload cap) can be restored.
    Every path is registered in ``written_files`` *before* it exists on disk, so a
    cancelled restore can always remove it; ``budget`` caps total bytes written.
    """
    if declared_mime not in RESTORE_ALLOWED_MIME_TYPES:
        raise AssetStorageError("MIME type is not allowed for restore.")
    max_media = backup_limits().max_media_bytes
    info = archive.getinfo(entry)
    if info.file_size > max_media:
        raise AssetStorageError("Media entry exceeds the restore size limit.")
    registry = written_files if written_files is not None else []
    safe_name = storage.sanitize_filename(filename)
    temp_path = storage.root_path / f".restore-{uuid.uuid4().hex}.part"
    registry.append(str(temp_path))
    digest = hashlib.sha256()
    total = 0
    prefix = bytearray()
    try:
        with archive.open(info, "r") as source, temp_path.open("xb") as output:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise _RestoreCancelled()
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_media:
                    raise AssetStorageError("Media entry exceeds the restore size limit.")
                if budget is not None:
                    budget.consume(len(chunk))
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
        registry.append(str(full_path))
        os.replace(temp_path, full_path)
    except BaseException:
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


def _mapped_id(id_map: dict[str, str], value: Any) -> str | None:
    return id_map.get(value) if isinstance(value, str) else None


def _mapped_ids(id_map: dict[str, str], values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [id_map[value] for value in values if isinstance(value, str) and value in id_map]


@dataclass
class _RestoreContext:
    archive: zipfile.ZipFile
    written_files: list[str]
    budget: _RestoreByteBudget
    cancel_event: threading.Event
    # archive entry -> stored file, so an entry referenced by many rows is written once
    media_cache: dict[str, SavedAssetFile]


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
            storage_rel_path=media_rel_path(zip_path, settings.media_root),
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

    async def get_backup_download(self, backup_id: str) -> tuple[BackupRecord, Path]:
        record = await self.repository.get_backup_record(backup_id)
        if record is None:
            raise ApplicationError(404, "Backup not found.")
        path = await asyncio.to_thread(self.backup_file_path, record)
        return record, path

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

        Restored data is treated as untrusted: the archive is fully validated before
        anything is written, in-flight tasks/exports become terminal, archive-supplied
        paths are discarded, workflow graphs are validated and media is re-verified.
        Any failure or cancellation rolls back the transaction and deletes media files
        already written by this restore.
        """
        archive = await asyncio.to_thread(_open_restore_archive, zip_source)
        context = _RestoreContext(
            archive=archive,
            written_files=[],
            budget=_RestoreByteBudget(backup_limits().max_uncompressed_bytes),
            cancel_event=threading.Event(),
            media_cache={},
        )
        try:
            manifest, tables = await asyncio.to_thread(_load_restore_payload, archive)
            try:
                project, report = await self._insert_restored_rows(context, manifest, tables, project_name)
            except IntegrityError as exc:
                await self._abort_restore(context)
                raise ApplicationError(
                    409, "Restore conflicts with existing data; nothing was restored."
                ) from exc
            except BaseException:
                await self._abort_restore(context)
                raise
        finally:
            # Every worker thread using the archive has finished by now (see _run_blocking).
            archive.close()

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

    async def _abort_restore(self, context: _RestoreContext) -> None:
        """Rollback and delete written media; shielded so a client disconnect cannot skip it."""
        context.cancel_event.set()

        async def cleanup() -> None:
            try:
                await self.repository.session.rollback()
            finally:
                await asyncio.to_thread(_unlink_files, list(context.written_files))

        await asyncio.shield(asyncio.ensure_future(cleanup()))

    @staticmethod
    async def _run_blocking(context: _RestoreContext, func: Any, *args: Any) -> Any:
        """Run blocking archive work in a thread; on cancellation, wait for the thread.

        The thread cannot be interrupted, so the caller waits for it (after signalling
        the cancel event) before cleanup runs; otherwise it could create a file after
        cleanup already ran, or read from a closed archive.
        """
        future = asyncio.ensure_future(asyncio.to_thread(func, *args))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            context.cancel_event.set()
            await asyncio.wait({future})
            raise

    async def _ensure_placeholder_connection(self) -> str:
        """Disabled, credential-less connection that keeps terminal history rows valid.

        Provider connections are environment-scoped and never restored. Tasks whose
        connection is absent here are re-pointed to this placeholder (they are always
        terminal after restore) so their shot takes and selected take survive. It is
        looked up by id, created at most once per restore and flushed immediately, so
        sessions with autoflush=False (production) never insert it twice.
        """
        db = self.repository.session
        if await db.get(ProviderConnection, RESTORED_PLACEHOLDER_CONNECTION_ID) is not None:
            return RESTORED_PLACEHOLDER_CONNECTION_ID
        name = _RESTORED_PLACEHOLDER_CONNECTION_NAME
        name_taken = (await db.execute(
            select(ProviderConnection.id).where(ProviderConnection.name == name)
        )).first()
        if name_taken is not None:
            name = f"{_RESTORED_PLACEHOLDER_CONNECTION_NAME} [{uuid.uuid4().hex[:8]}]"
        db.add(ProviderConnection(
            id=RESTORED_PLACEHOLDER_CONNECTION_ID,
            name=name,
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
        await db.flush()
        return RESTORED_PLACEHOLDER_CONNECTION_ID

    @staticmethod
    def _skip(report: dict[str, Any], table: str, row: dict[str, Any], reason: str) -> None:
        report["rows_skipped"][table] = report["rows_skipped"].get(table, 0) + 1
        if len(report["skipped_rows"]) < RESTORE_MAX_SKIPPED_ROWS_REPORTED:
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
            history = row.get("history_log")
            row["state"] = QueueTaskState.FAILED.value
            row["error_category"] = "restored_incomplete"
            row["retryable"] = False
            row["error_message_redacted"] = _RESTORED_TASK_MESSAGE
            row["finished_at"] = now.isoformat()
            row["history_log"] = [
                str(item)[:500] for item in (history if isinstance(history, list) else []) if isinstance(item, str)
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
        context: _RestoreContext,
        manifest: dict[str, Any],
        tables: dict[str, list[dict[str, Any]]],
        project_name: str | None,
    ) -> tuple[Project, dict[str, Any]]:
        db = self.repository.session
        now = datetime.now(UTC)
        available_connection_ids = set((await db.execute(select(ProviderConnection.id))).scalars().all())
        available_account_ids = set((await db.execute(select(ConnectionAccount.id))).scalars().all())
        placeholder_id: str | None = None
        if any(row.get("connection_id") not in available_connection_ids for row in tables["generation_tasks"]):
            placeholder_id = await self._ensure_placeholder_connection()
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
                    mapped = _mapped_id(id_maps[target_key], value)
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

                if spec.key == "projects" and project_name:
                    row["name"] = project_name
                elif spec.key == "generation_tasks":
                    if row.get("connection_id") not in available_connection_ids:
                        assert placeholder_id is not None
                        row["connection_id"] = placeholder_id
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
                    saved = await self._restore_asset_media(context, row)
                    if saved is None:
                        if len(report["media_missing"]) < RESTORE_MAX_SKIPPED_ROWS_REPORTED:
                            report["media_missing"].append(str(row.get("filename") or "")[:255])
                        self._skip(report, spec.key, original_row, "media missing or invalid")
                        continue
                    row["storage_rel_path"] = saved.storage_rel_path
                    row["size_bytes"] = saved.size_bytes
                    row["sha256"] = saved.sha256
                    row["mime_type"] = saved.detected_mime
                    report["media_restored"] += 1

                old_id = original_row.get("id")
                if isinstance(old_id, str) and old_id:
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
            # The models declare ForeignKeys without relationship(), so the unit of work
            # does not order INSERTs across tables. _TABLES is in FK dependency order;
            # flushing after each table guarantees parents exist before children
            # (PostgreSQL and SQLite with foreign_keys=ON reject the reverse order).
            await db.flush()

        # Late references are non-FK columns; they are written as UPDATEs at commit.
        self._remap_late_references(id_maps, restored_rows)
        # _load_restore_payload guarantees exactly one projects row matching the manifest.
        project_rows = restored_rows.get("projects", [])
        if len(project_rows) != 1 or project_rows[0][0].get("id") != manifest.get("project_id"):
            raise ApplicationError(422, "Backup must contain exactly one project matching manifest.project_id.")
        project = project_rows[0][1]
        await db.commit()
        return project, report

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
            obj.selected_take_id = _mapped_id(takes, original_row.get("selected_take_id"))
            obj.first_frame_asset_id = _mapped_id(assets, original_row.get("first_frame_asset_id"))
            obj.last_frame_asset_id = _mapped_id(assets, original_row.get("last_frame_asset_id"))
            obj.previous_shot_id = _mapped_id(shots, original_row.get("previous_shot_id"))
            obj.next_shot_id = _mapped_id(shots, original_row.get("next_shot_id"))
            obj.reference_asset_ids = _mapped_ids(assets, original_row.get("reference_asset_ids"))
        for original_row, obj in restored_rows.get("story_bible_entries", []):
            obj.reference_asset_ids = _mapped_ids(assets, original_row.get("reference_asset_ids"))
        for original_row, obj in restored_rows.get("generation_tasks", []):
            obj.result_asset_id = _mapped_id(assets, original_row.get("result_asset_id"))
            obj.take_id = _mapped_id(takes, original_row.get("take_id"))
        for _original_row, trk_obj in restored_rows.get("timeline_tracks", []):
            remapped_clips: list[dict[str, Any]] = []
            for clip_item in (trk_obj.clips_json if isinstance(trk_obj.clips_json, list) else []):
                if not isinstance(clip_item, dict):
                    continue
                c_copy = dict(clip_item)
                if c_copy.get("shot_id"):
                    c_copy["shot_id"] = _mapped_id(shots, c_copy["shot_id"])
                if c_copy.get("take_id"):
                    c_copy["take_id"] = _mapped_id(takes, c_copy["take_id"])
                if c_copy.get("asset_id"):
                    c_copy["asset_id"] = _mapped_id(assets, c_copy["asset_id"])
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
                                vc_copy["shot_id"] = _mapped_id(shots, vc_copy["shot_id"])
                            if vc_copy.get("take_id"):
                                vc_copy["take_id"] = _mapped_id(takes, vc_copy["take_id"])
                            if vc_copy.get("asset_id"):
                                vc_copy["asset_id"] = _mapped_id(assets, vc_copy["asset_id"])
                            v_clips.append(vc_copy)
                    manifest_copy["video_clips"] = v_clips
                if isinstance(manifest_copy.get("audio_tracks"), list):
                    a_tracks: list[dict[str, Any]] = []
                    for at in manifest_copy["audio_tracks"]:
                        if isinstance(at, dict):
                            at_copy = dict(at)
                            if at_copy.get("asset_id"):
                                at_copy["asset_id"] = _mapped_id(assets, at_copy["asset_id"])
                            a_tracks.append(at_copy)
                    manifest_copy["audio_tracks"] = a_tracks
                seq_obj.manifest_json = manifest_copy

    async def _restore_asset_media(
        self,
        context: _RestoreContext,
        row: dict[str, Any],
    ) -> SavedAssetFile | None:
        """Re-store and re-verify an asset's bytes under the new project.

        Returns None when the bytes are absent, oversized, or fail the MIME
        allowlist/signature check; the caller then skips the asset row instead
        of keeping an archive-supplied storage path. An archive entry referenced by
        several rows is extracted once and the rows share the stored file (asset
        deletion already keeps files that another asset row still references).
        """
        original_rel_path = row.get("storage_rel_path")
        mime_type = row.get("mime_type")
        if not isinstance(original_rel_path, str) or not original_rel_path:
            return None
        if not isinstance(mime_type, str) or mime_type not in RESTORE_ALLOWED_MIME_TYPES:
            return None
        entry = f"media/{original_rel_path}"
        cached = context.media_cache.get(entry)
        if cached is not None:
            return cached if cached.detected_mime == mime_type else None
        try:
            saved = await self._run_blocking(
                context,
                _extract_asset_media,
                context.archive,
                entry,
                self.storage,
                str(row.get("project_id") or ""),
                str(row.get("filename") or "restored_asset"),
                mime_type,
                context.written_files,
                context.budget,
                context.cancel_event,
            )
        except (KeyError, AssetStorageError, OSError, ValueError, zipfile.BadZipFile):
            return None
        context.media_cache[entry] = saved
        return replace(saved)

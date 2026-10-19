from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAX_UPLOAD_SIZE_BYTES = 150 * 1024 * 1024
_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._-]")


@dataclass
class SavedAssetFile:
    safe_filename: str
    storage_rel_path: str
    full_disk_path: str
    size_bytes: int
    sha256: str
    detected_mime: str


class AssetStorageError(Exception):
    pass


class LocalAssetStorage:
    def __init__(self, root_dir: str) -> None:
        self.root_path = Path(root_dir).resolve()
        self.root_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        base = os.path.basename(filename.strip())
        cleaned = _SAFE_FILENAME_RE.sub("_", base)
        return cleaned[:120] if cleaned and cleaned not in (".", "..") else "unnamed_asset.bin"

    @staticmethod
    def verify_mime_signature(content: bytes, declared_mime: str) -> str:
        if len(content) < 12:
            raise AssetStorageError("Chữ ký binary hoặc MIME không hợp lệ.")

        valid = False
        if declared_mime == "image/png":
            valid = content.startswith(b"\x89PNG\r\n\x1a\n")
        elif declared_mime == "image/jpeg":
            valid = content.startswith(b"\xff\xd8\xff")
        elif declared_mime == "image/webp":
            valid = content.startswith(b"RIFF") and content[8:12] == b"WEBP"
        elif declared_mime == "video/mp4":
            valid = content[4:8] == b"ftyp"
        else:
            raise AssetStorageError(f"MIME '{declared_mime}' không nằm trong danh sách cho phép.")

        if not valid:
            raise AssetStorageError(f"Chữ ký binary của file không khớp với '{declared_mime}'.")
        return declared_mime

    def save_bytes(
        self,
        project_id: str,
        filename: str,
        content: bytes,
        declared_mime: str,
    ) -> SavedAssetFile:
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise AssetStorageError("File vượt quá dung lượng cho phép.")
        verified_mime = self.verify_mime_signature(content, declared_mime)
        safe_name = self.sanitize_filename(filename)
        digest = hashlib.sha256(content).hexdigest()
        safe_project = _SAFE_FILENAME_RE.sub("_", project_id)
        relative_directory = Path(safe_project) / digest[:2]
        target_directory = (self.root_path / relative_directory).resolve()
        try:
            target_directory.relative_to(self.root_path)
        except ValueError as exc:
            raise AssetStorageError("Project path escaped the media root.") from exc
        target_directory.mkdir(parents=True, exist_ok=True)

        # A random token keeps two assets with identical bytes+name from sharing one file,
        # otherwise deleting one asset (or rolling back a failed insert) removes the other's file.
        final_filename = f"{digest[:12]}_{uuid.uuid4().hex[:8]}_{safe_name}"
        full_path = (target_directory / final_filename).resolve()
        try:
            full_path.relative_to(self.root_path)
        except ValueError as exc:
            raise AssetStorageError("Asset path escaped the media root.") from exc
        with open(full_path, "wb") as output:
            output.write(content)
        return SavedAssetFile(
            safe_filename=safe_name,
            storage_rel_path=str(relative_directory / final_filename),
            full_disk_path=str(full_path),
            size_bytes=len(content),
            sha256=digest,
            detected_mime=verified_mime,
        )

    def save_stream(
        self,
        project_id: str,
        filename: str,
        source: BinaryIO,
        declared_mime: str,
    ) -> SavedAssetFile:
        """Store a binary stream without buffering the full asset in memory.

        Bytes are streamed to a temporary file while the size and SHA-256 are
        computed. The first 12 bytes are enough to validate every supported media
        signature; the file is atomically moved to its final unique path only after
        the stream passes size and MIME checks.
        """
        safe_name = self.sanitize_filename(filename)
        temp_path = self.root_path / f".asset-{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        total = 0
        prefix = bytearray()

        try:
            with temp_path.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise AssetStorageError("Asset stream must produce bytes.")
                    total += len(chunk)
                    if total > MAX_UPLOAD_SIZE_BYTES:
                        raise AssetStorageError("File vượt quá dung lượng cho phép.")
                    if len(prefix) < 12:
                        prefix.extend(chunk[: 12 - len(prefix)])
                    digest.update(chunk)
                    output.write(chunk)

            verified_mime = self.verify_mime_signature(bytes(prefix), declared_mime)
            digest_hex = digest.hexdigest()
            safe_project = _SAFE_FILENAME_RE.sub("_", project_id)
            relative_directory = Path(safe_project) / digest_hex[:2]
            target_directory = (self.root_path / relative_directory).resolve()
            try:
                target_directory.relative_to(self.root_path)
            except ValueError as exc:
                raise AssetStorageError("Project path escaped the media root.") from exc
            target_directory.mkdir(parents=True, exist_ok=True)

            # A unique token avoids sharing a physical file between asset records.
            final_filename = f"{digest_hex[:12]}_{uuid.uuid4().hex[:8]}_{safe_name}"
            full_path = (target_directory / final_filename).resolve()
            try:
                full_path.relative_to(self.root_path)
            except ValueError as exc:
                raise AssetStorageError("Asset path escaped the media root.") from exc
            os.replace(temp_path, full_path)
            return SavedAssetFile(
                safe_filename=safe_name,
                storage_rel_path=str(relative_directory / final_filename),
                full_disk_path=str(full_path),
                size_bytes=total,
                sha256=digest_hex,
                detected_mime=verified_mime,
            )
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def resolve_safe_path(self, storage_rel_path: str) -> Path:
        candidate = (self.root_path / storage_rel_path).resolve()
        try:
            candidate.relative_to(self.root_path)
        except ValueError as exc:
            raise AssetStorageError("Asset path escaped the media root.") from exc
        if not candidate.is_file():
            raise AssetStorageError("Không tìm thấy file asset hợp lệ.")
        return candidate

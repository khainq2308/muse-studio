from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from dataclasses import dataclass

MAX_UPLOAD_SIZE_BYTES = 150 * 1024 * 1024

_MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/webp": [b"RIFF"],
    "video/mp4": [b"ftyp"],
}

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
        if len(content) < 12 or declared_mime not in _MAGIC_SIGNATURES:
            raise AssetStorageError(f"Chữ ký binary hoặc MIME '{declared_mime}' không hợp lệ.")
        header = content[:32]
        if not any(sig in header for sig in _MAGIC_SIGNATURES[declared_mime]):
            raise AssetStorageError(f"Chữ ký binary của file không khớp với '{declared_mime}'.")
        return declared_mime

    def save_bytes(self, project_id: str, filename: str, content: bytes, declared_mime: str) -> SavedAssetFile:
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise AssetStorageError("File vượt quá dung lượng cho phép.")
        verified_mime = self.verify_mime_signature(content, declared_mime)
        safe_name = self.sanitize_filename(filename)
        digest = hashlib.sha256(content).hexdigest()
        safe_proj = _SAFE_FILENAME_RE.sub("_", project_id)
        rel_dir = Path(safe_proj) / digest[:2]
        target_dir = (self.root_path / rel_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        final_filename = f"{digest[:12]}_{safe_name}"
        full_path = (target_dir / final_filename).resolve()
        with open(full_path, "wb") as f:
            f.write(content)
        return SavedAssetFile(safe_name, str(rel_dir / final_filename), str(full_path), len(content), digest, verified_mime)

    def resolve_safe_path(self, storage_rel_path: str) -> Path:
        candidate = (self.root_path / storage_rel_path).resolve()
        if not str(candidate).startswith(str(self.root_path)) or not candidate.is_file():
            raise AssetStorageError("Không tìm thấy file asset hợp lệ.")
        return candidate

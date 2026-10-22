"""File read/edit policy: protect secrets, generated artifacts and frozen history."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROTECTED_PARTS = {".git", "node_modules", "storage", "__pycache__", ".venv"}
PROTECTED_SUFFIXES = (".db", ".sqlite3", ".sqlite", ".pem", ".key")
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Pipfile.lock"}
MIGRATION_VERSIONS = "apps/orchestrator/migrations/versions/"

# Repository files an agent must regenerate with a tool, never hand-edit.
GENERATED_FILES = {
    "apps/studio-web/src/lib/api/schema.d.ts": "regenerate it with `npm run gen:api` in apps/studio-web",
    "apps/orchestrator/openapi.json": "regenerate it with `python scripts/export_openapi.py`",
}
FROZEN_FILES = {
    "apps/orchestrator/muse_orchestrator/models/schema_baseline_v1.py":
        "it is the frozen schema for revision 0001; change models/domain.py and add a new revision",
    "scripts/layering_baseline.json":
        "lower it only with `python3 scripts/check_layering.py --update-baseline` after removing real debt",
}

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "GitHub token"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}\b"), "API secret key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"(?i)\b(?:postgres(?:ql)?(?:\+\w+)?|redis)://[^:/\s]+:(?!\$\{|<|muse_secret_db_pass\b)[^@\s]{6,}@"), "URL with embedded password"),
]


def is_secret_env_name(name: str) -> bool:
    folded = name.casefold()
    return folded.startswith(".env") and folded != ".env.example"


def is_secret_env_path(path: Path) -> bool:
    try:
        parts = (*path.parts, *path.resolve().parts)
    except (OSError, RuntimeError):
        return True
    return any(is_secret_env_name(part) for part in parts)


def is_secret_pattern(pattern: str) -> bool:
    normalized = pattern.replace("\\", "/").rstrip("/")
    name = normalized.rsplit("/", 1)[-1]
    return is_secret_env_name(name) or name.casefold() in {".env*", ".env.*"}


def _absolute(root: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else root / path


def _tracked_in_head(root: Path, relative: str) -> bool:
    """True when HEAD contains the file; True as well if git cannot run or times out.

    Exit 128 covers both "path not in HEAD" and "not a repository": a brand-new revision
    file (the normal workflow) is therefore editable until it is committed.
    """
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{relative}"],
            cwd=root, capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    if proc.returncode == 0:
        return True
    # 128 with "not a valid object name" / "does not exist" means untracked or no HEAD.
    return proc.returncode != 128


def scan_secret_content(text: str) -> str | None:
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(text):
            return label
    return None


def check_edit(root: Path, raw_path: object, content: str = "") -> str | None:
    """Return a block message for creating/editing/deleting `raw_path`, or None."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return "[agent-guard:edit] Missing or invalid edit path; refusing to bypass the guard."
    root = root.resolve()
    try:
        resolved = _absolute(root, raw_path).resolve()
    except (OSError, RuntimeError):
        return "[agent-guard:edit] Path cannot be resolved safely."

    if is_secret_env_name(resolved.name):
        return "[agent-guard:edit] Real .env files are off-limits. Use .env.example for placeholders."
    if resolved.name.endswith(PROTECTED_SUFFIXES):
        return f"[agent-guard:edit] '{resolved.name}' looks like a database or key file and must not be edited."
    label = scan_secret_content(content)
    if label:
        return f"[agent-guard:edit] Possible {label} in content for '{resolved.name}'. Use placeholders / SecretVault; never hardcode secrets."

    try:
        rel = resolved.relative_to(root)
    except ValueError:
        # Outside the repository (agent memory, scratch files, plans): the repository rules
        # below do not apply, but secrets and key material are still refused above.
        return None

    relative = rel.as_posix()
    if PROTECTED_PARTS & set(rel.parts):
        return f"[agent-guard:edit] '{relative}' is a generated/protected location."
    if rel.name in LOCKFILES:
        return "[agent-guard:edit] Do not hand-edit lockfiles; run the package manager instead."
    if relative in GENERATED_FILES:
        return f"[agent-guard:edit] '{relative}' is generated; {GENERATED_FILES[relative]}."
    if relative in FROZEN_FILES:
        return f"[agent-guard:edit] '{relative}' is frozen: {FROZEN_FILES[relative]}."
    if relative.startswith(MIGRATION_VERSIONS) and relative.endswith(".py") and _tracked_in_head(root, relative):
        return (
            f"[agent-guard:edit] '{relative}' is a committed Alembic revision. Never edit applied "
            "history; add a new revision (see the alembic-migrations skill)."
        )
    return None


def check_read(root: Path, raw_path: object = "", patterns: list[str] | None = None, *, require_path: bool = False) -> str | None:
    """Return a block message for reading/searching a path or glob, or None."""
    if require_path and (not isinstance(raw_path, str) or not raw_path.strip()):
        return "[agent-guard:read] Read event has no file path; refusing to bypass the guard."
    if isinstance(raw_path, str) and raw_path.strip():
        if is_secret_env_path(_absolute(root, raw_path)):
            return "[agent-guard:read] Reading or searching real .env files is prohibited. Use .env.example placeholders."
    if any(is_secret_pattern(pattern) for pattern in patterns or []):
        return "[agent-guard:read] A search/glob pattern explicitly targets a real .env secret file."
    return None

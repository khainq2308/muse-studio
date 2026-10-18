#!/usr/bin/env python3
"""PreToolUse(Edit|Write|MultiEdit): protect sensitive files and scan new content for secrets."""
from __future__ import annotations

import re
from pathlib import Path

from _common import block, project_dir, read_event

PROTECTED_PARTS = {".git", "node_modules", "storage", "__pycache__"}
PROTECTED_SUFFIXES = (".db", ".sqlite3", ".sqlite", ".pem", ".key")
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "GitHub token"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}\b"), "API secret key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"(?i)\b(?:postgres(?:ql)?(?:\+\w+)?|redis)://[^:/\s]+:(?!\$\{|<|muse_secret_db_pass\b)[^@\s]{6,}@"), "URL with embedded password"),
]


def new_content(tool_input: dict) -> str:
    parts = [tool_input.get("content", ""), tool_input.get("new_string", "")]
    for edit in tool_input.get("edits", []) or []:
        parts.append(edit.get("new_string", ""))
    return "\n".join(p for p in parts if p)


def main() -> None:
    event = read_event()
    tool_input = event.get("tool_input") or {}
    raw_path = tool_input.get("file_path") or ""
    if not raw_path:
        return
    root = project_dir()
    path = Path(raw_path)
    path = path if path.is_absolute() else root / path
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        block(f"[pre_edit_guard] '{raw_path}' is outside the project directory.")
        return

    name = rel.name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        block("[pre_edit_guard] .env files hold real secrets and are off-limits. Document new variables in .env.example instead.")
    if PROTECTED_PARTS & set(rel.parts):
        block(f"[pre_edit_guard] '{rel}' is a generated/protected location.")
    if name.endswith(PROTECTED_SUFFIXES):
        block(f"[pre_edit_guard] '{rel}' looks like a database or key file and must not be edited.")
    if name in LOCKFILES:
        block("[pre_edit_guard] Do not hand-edit lockfiles; run the package manager instead.")

    # Applied Alembic revisions are immutable: add a new revision instead.
    if "migrations/versions" in rel.as_posix() and path.exists():
        block(f"[pre_edit_guard] Migration '{rel.name}' already exists. Applied revisions are immutable — create a new revision.")

    if name != ".env.example":
        text = new_content(tool_input)
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                block(f"[pre_edit_guard] Possible {label} in content for '{rel}'. Use environment variables / SecretVault; never hardcode secrets.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""PreToolUse(Read|Grep|Glob): block direct access to real .env secrets."""
from __future__ import annotations

from pathlib import Path

from _common import block, project_dir, read_event


def is_secret_env_path(path: Path) -> bool:
    try:
        parts = (*path.parts, *path.resolve().parts)
    except (OSError, RuntimeError):
        return True
    return any(
        part.casefold().startswith(".env") and part.casefold() != ".env.example"
        for part in parts
    )


def is_secret_pattern(pattern: str) -> bool:
    normalized = pattern.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    folded = name.casefold()
    return (
        folded.startswith(".env") and folded != ".env.example"
        or folded in {".env*", ".env.*", "**/.env", "**/.env.*", "**/.env*"}
    )


def main() -> None:
    event = read_event()
    if not isinstance(event, dict):
        block("[pre_read_guard] Missing or invalid hook event; refusing to bypass secret validation.")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        block("[pre_read_guard] Invalid tool_input; refusing to bypass secret validation.")

    tool_name = event.get("tool_name", "Read")
    root = project_dir()
    raw_path = tool_input.get("file_path") or tool_input.get("path") or ""

    # Direct file reads must always provide a path.
    if tool_name == "Read":
        if not isinstance(raw_path, str) or not raw_path.strip():
            block("[pre_read_guard] Read event has no file path; refusing to bypass the guard.")
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / path
        if is_secret_env_path(path):
            block("[pre_read_guard] Reading real .env files is prohibited. Use .env.example placeholders.")
        return

    # Grep/Glob are allowed for normal repository work, but cannot explicitly target env secrets.
    if isinstance(raw_path, str) and raw_path.strip():
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / path
        if is_secret_env_path(path):
            block("[pre_read_guard] Searching a real .env file is prohibited.")

    patterns = []
    for key in ("glob", "pattern", "include"):
        value = tool_input.get(key)
        if isinstance(value, str):
            patterns.append(value)
        elif isinstance(value, list):
            patterns.extend(p for p in value if isinstance(p, str))
    if any(is_secret_pattern(pattern) for pattern in patterns):
        block("[pre_read_guard] A Grep/Glob pattern explicitly targets a real .env secret file.")


if __name__ == "__main__":
    main()

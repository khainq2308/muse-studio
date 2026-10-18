#!/usr/bin/env python3
"""PreToolUse(Read): block reading real .env secrets."""
from pathlib import Path
from _common import block, project_dir, read_event

def main():
    event = read_event()
    tool_input = event.get("tool_input") or {}
    raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_dir() / path
    for part in (*path.parts, *path.resolve().parts):
        if part == ".env" or (part.startswith(".env.") and part != ".env.example"):
            block("[pre_read_guard] Reading real .env files is prohibited.")
    return

if __name__ == "__main__":
    main()

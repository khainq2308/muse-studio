#!/usr/bin/env python3
"""PreToolUse(Read): block reading real .env secrets."""
from pathlib import Path
from _common import block, project_dir, read_event

def main():
    event = read_event()
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    if not isinstance(tool_input, dict):
        block("[pre_read_guard] Missing or invalid hook event; refusing to read without validating the path.")
    raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not isinstance(raw_path, str) or not raw_path.strip():
        block("[pre_read_guard] Read hook event has no file path; refusing to bypass the guard.")
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_dir() / path
    for part in (*path.parts, *path.resolve().parts):
        if part == ".env" or (part.startswith(".env.") and part != ".env.example"):
            block("[pre_read_guard] Reading real .env files is prohibited.")
    return

if __name__ == "__main__":
    main()

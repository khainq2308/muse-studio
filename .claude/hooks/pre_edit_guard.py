#!/usr/bin/env python3
"""PreToolUse(Edit|Write|MultiEdit): Claude adapter for scripts/agent_guard/files.py."""
from __future__ import annotations

from _common import block, project_dir, read_event

from agent_guard.files import check_edit


def new_content(tool_input: dict) -> str:
    parts = [tool_input.get("content", ""), tool_input.get("new_string", "")]
    for edit in tool_input.get("edits", []) or []:
        if isinstance(edit, dict):
            parts.append(edit.get("new_string", ""))
    return "\n".join(p for p in parts if isinstance(p, str) and p)


def main() -> None:
    event = read_event()
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        block("[pre_edit_guard] Missing or invalid tool_input; refusing to bypass the guard.")
    raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
    message = check_edit(project_dir(), raw_path, new_content(tool_input))
    if message:
        block(message)


if __name__ == "__main__":
    main()

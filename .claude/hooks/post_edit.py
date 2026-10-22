#!/usr/bin/env python3
"""PostToolUse(Edit|Write|MultiEdit): Claude adapter for scripts/agent_guard/postedit.py."""
from __future__ import annotations

from _common import add_context, block, project_dir, read_event

from agent_guard.postedit import check_edited_file


def main() -> None:
    event = read_event()
    tool_input = event.get("tool_input") or {}
    raw = tool_input.get("file_path") if isinstance(tool_input, dict) else ""
    message, notes = check_edited_file(project_dir(), raw)
    if message:
        block(message)
    if notes:
        add_context("PostToolUse", " | ".join(notes))


if __name__ == "__main__":
    main()

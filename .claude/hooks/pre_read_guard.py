#!/usr/bin/env python3
"""PreToolUse(Read|Grep|Glob): Claude adapter for scripts/agent_guard/files.py."""
from __future__ import annotations

from _common import block, project_dir, read_event

from agent_guard.files import check_read, is_secret_env_path, is_secret_pattern  # noqa: F401


def main() -> None:
    event = read_event()
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        block("[pre_read_guard] Invalid tool_input; refusing to bypass secret validation.")

    tool_name = event.get("tool_name", "Read")
    raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
    patterns: list[str] = []
    if tool_name != "Read":
        for key in ("glob", "pattern", "include"):
            value = tool_input.get(key)
            if isinstance(value, str):
                patterns.append(value)
            elif isinstance(value, list):
                patterns.extend(p for p in value if isinstance(p, str))

    message = check_read(project_dir(), raw_path, patterns, require_path=tool_name == "Read")
    if message:
        block(message)


if __name__ == "__main__":
    main()

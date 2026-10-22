#!/usr/bin/env python3
"""Codex hook adapter for MuseStudio's shared policy (scripts/agent_guard/).

Contract (Codex hooks docs): one JSON object on stdin with `hook_event_name`, `tool_name`
and `tool_input`; shell calls arrive as tool `Bash` with `tool_input.command`, file edits as
`apply_patch` with the patch text in `tool_input.command`. Exit code 2 + stderr blocks a
PreToolUse call; for Stop it makes Codex continue with stderr as the next prompt.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_guard.files import check_edit  # noqa: E402
from agent_guard.shell import check_command  # noqa: E402
from agent_guard.verify import completion_gate  # noqa: E402

_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$")


def patch_targets(patch: str) -> tuple[list[str], str]:
    """Return (paths touched, added text) for an apply_patch envelope."""
    paths: list[str] = []
    added: list[str] = []
    for line in patch.splitlines():
        match = _PATCH_PATH.match(line)
        if match:
            paths.append((match.group(1) or match.group(2)).strip())
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return paths, "\n".join(added)


def check_pre_tool_use(event: dict) -> str | None:
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return "[codex guard] Invalid tool_input; refusing to bypass the guard."
    command = tool_input.get("command")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)

    if tool_name == "Bash":
        return check_command(command)
    if tool_name == "apply_patch":
        if not isinstance(command, str) or not command.strip():
            return "[codex guard] apply_patch has no patch text; refusing to bypass the guard."
        paths, added = patch_targets(command)
        if not paths:
            return "[codex guard] apply_patch names no file; refusing an unvalidated patch."
        for path in paths:
            message = check_edit(ROOT, path, added)
            if message:
                return message
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        print("[codex guard] Invalid hook JSON; refusing tool call.", file=sys.stderr)
        return 2
    if not isinstance(event, dict):
        print("[codex guard] Hook event must be a JSON object.", file=sys.stderr)
        return 2

    if event.get("hook_event_name") == "Stop":
        if event.get("stop_hook_active") or os.environ.get("MUSE_STOP_GATE", "1") == "0":
            print("{}")
            return 0
        message = completion_gate(ROOT, session_key=str(event.get("session_id") or "codex"))
        if message:
            print(message, file=sys.stderr)
            return 2
        print("{}")  # Stop expects JSON on stdout when exiting 0.
        return 0

    message = check_pre_tool_use(event)
    if message:
        print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

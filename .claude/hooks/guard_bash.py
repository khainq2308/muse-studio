#!/usr/bin/env python3
"""PreToolUse(Bash): Claude adapter for the shared shell policy (scripts/agent_guard/shell.py)."""
from __future__ import annotations

from _common import block, read_event

from agent_guard.shell import (  # noqa: F401  (re-exported for regression tests)
    RULES,
    SECRET_PATH,
    _shell_command_segments,
    check_command,
    dynamic_shell_expansion_risk,
    rm_is_dangerous,
)


def main() -> None:
    event = read_event()
    if not event:
        block("[guard_bash] Missing or invalid hook event; refusing to run an unvalidated shell command.")
    if event.get("tool_name") != "Bash":
        return
    tool_input = event.get("tool_input")
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    message = check_command(command)
    if message:
        block(message)


if __name__ == "__main__":
    main()

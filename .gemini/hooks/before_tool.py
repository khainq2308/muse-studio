#!/usr/bin/env python3
"""Gemini CLI BeforeTool adapter for the repository's shared safety guards.

Gemini hooks read JSON from stdin. Successful invocations write one JSON object to stdout;
exit code 2 blocks the tool. The adapter deliberately fails closed if a safety guard cannot run.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("GEMINI_PROJECT_DIR") or Path.cwd()).resolve()
HOOKS = ROOT / ".claude" / "hooks"

def run_guard(script: str, payload: dict) -> int:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(ROOT)
    try:
        proc = subprocess.run(
            [sys.executable, str(HOOKS / script)],
            input=json.dumps(payload), text=True, capture_output=True, timeout=10, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[gemini safety guard] Cannot run {script}: {exc}", file=sys.stderr)
        return 2
    if proc.returncode != 0:
        print(proc.stderr.rstrip() or f"{script} failed with exit {proc.returncode}", file=sys.stderr)
        return 2
    return 0

def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        print("[gemini safety guard] Invalid hook JSON; refusing tool call.", file=sys.stderr)
        return 2
    tool_name = event.get("tool_name", "")
    args = event.get("tool_input") or {}
    if not isinstance(args, dict):
        print("[gemini safety guard] Invalid tool_input; refusing tool call.", file=sys.stderr)
        return 2

    if tool_name == "run_shell_command":
        result = run_guard("guard_bash.py", {
            "tool_name": "Bash",
            "tool_input": {"command": str(args.get("command") or args.get("command_line") or "")}
        })
    elif tool_name in {"write_file", "replace"}:
        mapped = dict(args)
        mapped["file_path"] = mapped.get("file_path") or mapped.get("path") or mapped.get("target_file") or ""
        if "content" not in mapped and "new_string" not in mapped:
            mapped["new_string"] = mapped.get("replacement") or mapped.get("replace_text") or ""
        result = run_guard("pre_edit_guard.py", {"tool_name": "Write" if tool_name == "write_file" else "Edit", "tool_input": mapped})
    elif tool_name == "read_file":
        mapped = dict(args)
        mapped["file_path"] = mapped.get("file_path") or mapped.get("path") or mapped.get("absolute_path") or ""
        result = run_guard("pre_read_guard.py", {"tool_name": "Read", "tool_input": mapped})
    else:
        result = 0

    if result != 0:
        return 2
    print(json.dumps({"decision": "allow"}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

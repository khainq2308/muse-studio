#!/usr/bin/env python3
"""Gemini CLI BeforeTool adapter for the repository's safety guards."""
from __future__ import annotations
import glob
import json
import os
import re
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

def is_secret_path(path: Path) -> bool:
    try:
        parts = (*path.parts, *path.resolve().parts)
    except (OSError, RuntimeError):
        return True
    return any(part == ".env" or (part.startswith(".env.") and part != ".env.example") for part in parts)

def check_many_file_paths(args: dict) -> int:
    raw_paths = args.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths or any(not isinstance(item, str) or not item.strip() for item in raw_paths):
        print("[gemini safety guard] read_many_files has missing/invalid paths; refusing to bypass secret validation.", file=sys.stderr)
        return 2
    for raw in raw_paths:
        # Expand globs with hidden entries included, so .env files are not skipped by the guard.
        pattern_path = Path(raw).expanduser()
        if not pattern_path.is_absolute():
            pattern_path = ROOT / pattern_path
        try:
            matches = glob.glob(str(pattern_path), recursive=True, include_hidden=True)
        except (OSError, ValueError, re.error) as exc:
            print(f"[gemini safety guard] Cannot validate file pattern: {exc}", file=sys.stderr)
            return 2
        if not matches and not any(char in raw for char in "*?[]"):
            if pattern_path.exists():
                matches = [str(pattern_path)]
            else:
                # Non-matching literal paths are passed to the native tool for its normal error handling.
                continue
        for match in matches:
            path = Path(match)
            if path.is_dir():
                # A directory path is broad: search it for secret-style names, pruning generated trees.
                for base, dirs, names in os.walk(path, followlinks=False):
                    dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", "storage"}]
                    for name in names:
                        candidate = Path(base) / name
                        if is_secret_path(candidate):
                            print("[gemini safety guard] read_many_files pattern includes a real .env secret file.", file=sys.stderr)
                            return 2
            elif is_secret_path(path):
                print("[gemini safety guard] read_many_files pattern includes a real .env secret file.", file=sys.stderr)
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
    elif tool_name == "read_many_files":
        result = check_many_file_paths(args)
    else:
        result = 0
    if result != 0:
        return 2
    print(json.dumps({"decision": "allow"}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Gemini CLI BeforeTool adapter for MuseStudio's shared safety guards."""
from __future__ import annotations
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Resolve from this script, not from an undocumented host environment variable or current cwd.
ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".claude" / "hooks"
PRUNED_DIRS = {".git", "node_modules", ".venv", "venv", "storage", "__pycache__"}

def run_guard(script: str, payload: dict) -> int:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(ROOT)
    try:
        proc = subprocess.run(
            [sys.executable, str(HOOKS / script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=10,
            env=env,
            cwd=ROOT,
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
    # Current Gemini CLI schema uses include/exclude arrays. Accept paths as a legacy alias,
    # but fail closed when neither schema is present.
    patterns = args.get("include")
    if patterns is None:
        patterns = args.get("paths")
    if not isinstance(patterns, list) or not patterns or any(not isinstance(p, str) or not p.strip() for p in patterns):
        print("[gemini safety guard] read_many_files has missing/invalid include patterns; refusing to bypass secret validation.", file=sys.stderr)
        return 2

    exclusions = args.get("exclude") or []
    if not isinstance(exclusions, list) or any(not isinstance(p, str) for p in exclusions):
        print("[gemini safety guard] read_many_files has invalid exclude patterns; refusing to bypass secret validation.", file=sys.stderr)
        return 2
    explicit_exclusions = [Path(p).as_posix() for p in exclusions]

    for raw in patterns:
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
                # Non-matching literals are passed to the native tool for normal error handling.
                continue

        for match in matches:
            path = Path(match)
            try:
                rel = path.resolve().relative_to(ROOT)
            except (OSError, RuntimeError, ValueError):
                print("[gemini safety guard] read_many_files pattern resolves outside the repo; refusing to read it.", file=sys.stderr)
                return 2
            # Avoid rejecting broad repo reads because they contain locally ignored secrets that
            # the default tool filter would exclude. A direct/matched secret file still blocks.
            relative = rel.as_posix()
            excluded = any(
                glob.fnmatch.fnmatch(relative, pattern) or glob.fnmatch.fnmatch(path.name, pattern)
                for pattern in explicit_exclusions
            )
            if excluded:
                continue
            # Gemini's read_many_files uses glob patterns and nodir=true: directory
            # matches are not read directly. Recursive include patterns also yield files,
            # so inspect those matches only rather than walking each directory repeatedly.
            if path.is_dir():
                continue
            if any(part in PRUNED_DIRS for part in rel.parts):
                continue
            if is_secret_path(path):
                print("[gemini safety guard] read_many_files pattern includes a real .env secret file.", file=sys.stderr)
                return 2
    return 0

def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        print("[gemini safety guard] Invalid hook JSON; refusing tool call.", file=sys.stderr)
        return 2
    if not isinstance(event, dict):
        print("[gemini safety guard] Hook event must be a JSON object; refusing tool call.", file=sys.stderr)
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

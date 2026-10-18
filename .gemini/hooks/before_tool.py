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

def pattern_targets_secret(raw: str) -> bool:
    name = raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return (
        name == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or name in {".env*", ".env.*"}
    )


def pattern_matches(relative_path: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/")
    return (
        glob.fnmatch.fnmatchcase(relative_path, normalized)
        or glob.fnmatch.fnmatchcase(Path(relative_path).name, normalized)
    )


def path_is_git_ignored(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(ROOT).as_posix()
        proc = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError):
        return False
    return proc.returncode == 0


def check_many_file_paths(args: dict) -> int:
    # Current Gemini CLI uses include/exclude arrays. Accept paths only for older clients.
    patterns = args.get("include")
    if patterns is None:
        patterns = args.get("paths")
    if (
        not isinstance(patterns, list)
        or not patterns
        or any(not isinstance(pattern, str) or not pattern.strip() for pattern in patterns)
    ):
        print(
            "[gemini safety guard] read_many_files has missing/invalid include patterns; "
            "refusing to bypass secret validation.",
            file=sys.stderr,
        )
        return 2

    exclusions = args.get("exclude") or []
    if not isinstance(exclusions, list) or any(not isinstance(item, str) for item in exclusions):
        print(
            "[gemini safety guard] read_many_files has invalid exclude patterns; "
            "refusing to bypass secret validation.",
            file=sys.stderr,
        )
        return 2

    options = args.get("file_filtering_options") or {}
    if not isinstance(options, dict):
        print(
            "[gemini safety guard] read_many_files has invalid file filtering options.",
            file=sys.stderr,
        )
        return 2
    respect_git_ignore = options.get("respect_git_ignore", True)
    if not isinstance(respect_git_ignore, bool):
        print(
            "[gemini safety guard] respect_git_ignore must be a boolean.",
            file=sys.stderr,
        )
        return 2

    # Explicit targeting is blocked regardless of ignore rules.
    for pattern in patterns:
        if pattern_targets_secret(pattern):
            print(
                "[gemini safety guard] read_many_files explicitly targets a real .env secret file.",
                file=sys.stderr,
            )
            return 2
        candidate = Path(pattern).expanduser()
        resolved = candidate if candidate.is_absolute() else ROOT / candidate
        try:
            resolved_path = resolved.resolve()
            resolved_path.relative_to(ROOT)
        except (OSError, RuntimeError, ValueError):
            print(
                "[gemini safety guard] read_many_files pattern resolves outside the repository.",
                file=sys.stderr,
            )
            return 2

    # Walk the workspace while pruning generated/dependency trees. This avoids recursive
    # globbing through node_modules and .git merely to decide whether a secret would be read.
    for base, directories, names in os.walk(ROOT, followlinks=False):
        directories[:] = [name for name in directories if name not in PRUNED_DIRS]
        base_path = Path(base)
        for name in names:
            candidate = base_path / name
            if not (name == ".env" or name.startswith(".env.") or candidate.is_symlink()):
                continue
            if not is_secret_path(candidate):
                continue

            try:
                relative = candidate.resolve().relative_to(ROOT).as_posix()
            except (OSError, RuntimeError, ValueError):
                print(
                    "[gemini safety guard] Cannot safely resolve a potential secret path.",
                    file=sys.stderr,
                )
                return 2

            if not any(pattern_matches(relative, pattern) for pattern in patterns):
                continue
            if any(pattern_matches(relative, pattern) for pattern in exclusions):
                continue

            # The native tool respects git-ignore by default. Match that behavior for broad
            # context reads; if filtering is disabled or the check cannot prove ignore status,
            # block rather than risk sending a secret to the model.
            if respect_git_ignore and path_is_git_ignored(candidate):
                continue
            print(
                "[gemini safety guard] read_many_files would include a real .env secret file.",
                file=sys.stderr,
            )
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
    elif tool_name in {"grep_search", "glob"}:
        mapped = dict(args)
        mapped["path"] = mapped.get("path") or mapped.get("dir_path") or ""
        mapped["glob"] = mapped.get("glob") or mapped.get("include") or ""
        mapped["pattern"] = mapped.get("pattern") or ""
        result = run_guard("pre_read_guard.py", {"tool_name": "Grep" if tool_name == "grep_search" else "Glob", "tool_input": mapped})
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

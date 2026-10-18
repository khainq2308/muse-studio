"""Shared helpers for Claude Code hooks (stdlib only)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def read_event() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def project_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def block(message: str) -> None:
    """Exit code 2: the tool call is blocked and stderr is fed back to Claude."""
    print(message, file=sys.stderr)
    sys.exit(2)


def add_context(event_name: str, text: str) -> None:
    """Return extra context to Claude without blocking."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}))
    sys.exit(0)


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd)}: timed out after {timeout}s"
    return proc.returncode, (proc.stdout + proc.stderr)

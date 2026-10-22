"""Claude Code hook adapter helpers (stdlib only).

Policy lives in scripts/agent_guard/; these hooks only translate Claude's event contract.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Resolve the shared policy from this file, not from CLAUDE_PROJECT_DIR (tests point that
# at temporary repositories).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_guard.verify import run  # noqa: E402,F401  (re-exported for hook scripts)


def read_event() -> dict:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return event if isinstance(event, dict) else {}


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

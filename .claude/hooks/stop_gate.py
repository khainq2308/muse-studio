#!/usr/bin/env python3
"""Stop: Claude adapter for the shared completion gate (scripts/agent_guard/verify.py)."""
from __future__ import annotations

import os

from _common import block, project_dir, read_event

from agent_guard.verify import (  # noqa: F401  (re-exported for regression tests)
    collect_changed_paths,
    completion_gate,
    resolve_python,
    validate_changed,
)


def main() -> None:
    event = read_event()
    # stop_hook_active means Claude is already continuing because of this hook: one retry.
    if event.get("stop_hook_active") or os.environ.get("MUSE_STOP_GATE", "1") == "0":
        return
    message = completion_gate(project_dir())
    if message:
        block(message)


if __name__ == "__main__":
    main()

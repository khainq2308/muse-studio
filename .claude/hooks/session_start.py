#!/usr/bin/env python3
"""SessionStart: Claude adapter for scripts/agent_guard/session.py (stdout becomes context)."""
from __future__ import annotations

from _common import project_dir

from agent_guard.session import _has_arq_worker_service, session_context  # noqa: F401

if __name__ == "__main__":
    print(session_context(project_dir()))

#!/usr/bin/env python3
"""Stop: don't let the agent finish while changed backend code has failing tests or new layering violations.

Disable with MUSE_STOP_GATE=0. Never loops: exits quietly if a stop-hook already ran this turn.
"""
from __future__ import annotations

import os
import sys

from _common import block, project_dir, read_event, run

event = read_event()
if event.get("stop_hook_active") or os.environ.get("MUSE_STOP_GATE", "1") == "0":
    sys.exit(0)

root = project_dir()
_, diff = run(["git", "status", "--porcelain"], root, 15)
changed = [l[3:] for l in diff.splitlines() if l[3:].endswith(".py")]
backend = [p for p in changed if p.startswith("apps/orchestrator/")]
media = [p for p in changed if p.startswith("apps/media-engine/")]
if not backend and not media:
    sys.exit(0)

problems: list[str] = []

code, out = run(["python3", "scripts/check_layering.py", "--quiet"], root, 30)
if code == 1:
    problems.append("Layering ratchet failed (new DB access in api/):\n" + out.strip()[-1200:])

if backend:
    code, _ = run(["python3", "-c", "import pytest, fastapi"], root / "apps" / "orchestrator", 20)
    if code == 0:
        code, out = run(["python3", "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                        root / "apps" / "orchestrator", 200)
        if code not in (0, 5):
            problems.append("orchestrator tests failing:\n" + out.strip()[-2000:])

if media:
    code, out = run(["python3", "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                    root / "apps" / "media-engine", 60)
    if code not in (0, 5, 127) and "No module named pytest" not in out:
        problems.append("media-engine tests failing:\n" + out.strip()[-1200:])

if problems:
    block("[stop_gate] Work is not finished:\n\n" + "\n\n".join(problems) +
          "\n\nFix these, or explain to the user why they cannot be fixed. (Disable gate: MUSE_STOP_GATE=0)")

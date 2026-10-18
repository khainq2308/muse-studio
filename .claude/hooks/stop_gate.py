#!/usr/bin/env python3
"""Stop hook: block completion if required checks fail or cannot run."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Callable
from _common import block, project_dir, read_event, run

Runner = Callable[[list[str], Path | None, int], tuple[int, str]]

def validate_changed(root: Path, backend: list[str], media: list[str], runner: Runner | None = None) -> list[str]:
    runner = runner or run
    problems: list[str] = []
    code, out = runner(["python3", "scripts/check_layering.py", "--quiet"], root, 30)
    if code != 0:
        problems.append(f"Layering checker did not pass (exit {code}):\n{out.strip()[-1200:]}")
    if backend:
        cwd = root / "apps" / "orchestrator"
        code, out = runner(["python3", "-c", "import pytest, fastapi"], cwd, 20)
        if code != 0:
            problems.append(f"Orchestrator tests NOT RUN: prerequisites failed (exit {code}):\n{out.strip()[-1200:]}")
        else:
            code, out = runner(["python3", "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"], cwd, 200)
            if code != 0:
                problems.append(f"Orchestrator tests did not pass (exit {code}; no tests collected is not pass):\n{out.strip()[-2000:]}")
    if media:
        cwd = root / "apps" / "media-engine"
        code, out = runner(["python3", "-c", "import pytest"], cwd, 20)
        if code != 0:
            problems.append(f"Media-engine tests NOT RUN: pytest unavailable (exit {code}):\n{out.strip()[-1200:]}")
        else:
            code, out = runner(["python3", "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"], cwd, 60)
            if code != 0:
                problems.append(f"Media-engine tests did not pass (exit {code}; no tests collected is not pass):\n{out.strip()[-1200:]}")
    return problems

def main() -> None:
    event = read_event()
    if event.get("stop_hook_active") or os.environ.get("MUSE_STOP_GATE", "1") == "0":
        return
    root = project_dir()
    code, diff = run(["git", "status", "--porcelain"], root, 15)
    if code != 0:
        block("[stop_gate] Cannot inspect Git status; verify the working tree and tests before finishing.")
    changed = [line[3:] for line in diff.splitlines() if len(line) > 3 and line[3:].endswith(".py")]
    backend = [path for path in changed if path.startswith("apps/orchestrator/")]
    media = [path for path in changed if path.startswith("apps/media-engine/")]
    if not backend and not media:
        return
    problems = validate_changed(root, backend, media)
    if problems:
        block("[stop_gate] Work is not verified:\n\n" + "\n\n".join(problems) + "\n\nFix the issue or report exactly which required check could not run. Deliberate local override: MUSE_STOP_GATE=0.")

if __name__ == "__main__":
    main()

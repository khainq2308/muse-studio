#!/usr/bin/env python3
"""Stop hook: block completion if required checks fail or cannot run."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from _common import block, project_dir, read_event, run

Runner = Callable[[list[str], Path | None, int], tuple[int, str]]


def collect_changed_paths(root: Path, runner: Runner | None = None) -> set[str]:
    """Include uncommitted files and branch commits so committing cannot bypass the gate."""
    runner = runner or run
    changed: set[str] = set()

    status_code, status = runner(["git", "status", "--porcelain"], root, 15)
    if status_code != 0:
        raise RuntimeError(f"git status failed (exit {status_code}): {status[-1000:]}")
    for line in status.splitlines():
        if len(line) > 3:
            # Handles ordinary, staged, untracked and rename-style porcelain entries.
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            changed.add(path.replace("\\", "/"))

    # The branch comparison catches changes already committed in this task/session.
    branch_code, branch_diff = runner(
        ["git", "diff", "--name-only", "origin/main...HEAD"], root, 15
    )
    if branch_code == 0:
        changed.update(p.replace("\\", "/") for p in branch_diff.splitlines() if p.strip())
    else:
        # Shallow/non-standard clones may not have origin/main. At minimum check the last commit.
        last_code, last_diff = runner(["git", "diff", "--name-only", "HEAD^", "HEAD"], root, 15)
        if last_code == 0:
            changed.update(p.replace("\\", "/") for p in last_diff.splitlines() if p.strip())

    staged_code, staged = runner(["git", "diff", "--cached", "--name-only"], root, 15)
    if staged_code != 0:
        raise RuntimeError(f"git diff --cached failed (exit {staged_code}): {staged[-1000:]}")
    changed.update(p.replace("\\", "/") for p in staged.splitlines() if p.strip())

    work_code, work = runner(["git", "diff", "--name-only"], root, 15)
    if work_code != 0:
        raise RuntimeError(f"git diff failed (exit {work_code}): {work[-1000:]}")
    changed.update(p.replace("\\", "/") for p in work.splitlines() if p.strip())

    untracked_code, untracked = runner(["git", "ls-files", "--others", "--exclude-standard"], root, 15)
    if untracked_code != 0:
        raise RuntimeError(f"git ls-files failed (exit {untracked_code}): {untracked[-1000:]}")
    changed.update(p.replace("\\", "/") for p in untracked.splitlines() if p.strip())
    return changed


def validate_changed(
    root: Path,
    backend: list[str],
    media: list[str],
    runner: Runner | None = None,
) -> list[str]:
    runner = runner or run
    problems: list[str] = []

    code, out = runner(["python3", "scripts/check_layering.py", "--quiet"], root, 30)
    if code != 0:
        problems.append(f"Layering checker did not pass (exit {code}):\n{out.strip()[-1200:]}")

    if backend:
        cwd = root / "apps" / "orchestrator"
        code, out = runner(["python3", "-c", "import pytest, fastapi"], cwd, 20)
        if code != 0:
            problems.append(
                f"Orchestrator tests NOT RUN: prerequisites failed (exit {code}):\n{out.strip()[-1200:]}"
            )
        else:
            code, out = runner(
                ["python3", "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                cwd,
                200,
            )
            if code != 0:
                problems.append(
                    f"Orchestrator tests did not pass (exit {code}; no tests collected is not pass):\n{out.strip()[-2000:]}"
                )

    if media:
        cwd = root / "apps" / "media-engine"
        code, out = runner(["python3", "-c", "import pytest"], cwd, 20)
        if code != 0:
            problems.append(
                f"Media-engine tests NOT RUN: pytest unavailable (exit {code}):\n{out.strip()[-1200:]}"
            )
        else:
            code, out = runner(
                ["python3", "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                cwd,
                60,
            )
            if code != 0:
                problems.append(
                    f"Media-engine tests did not pass (exit {code}; no tests collected is not pass):\n{out.strip()[-1200:]}"
                )
    return problems


def main() -> None:
    event = read_event()
    if event.get("stop_hook_active") or os.environ.get("MUSE_STOP_GATE", "1") == "0":
        return

    root = project_dir()
    try:
        changed = collect_changed_paths(root)
    except RuntimeError as exc:
        block(f"[stop_gate] Cannot determine changed paths; do not claim checks passed.\n{exc}")

    py_changed = [p for p in changed if p.endswith(".py")]
    backend = [p for p in py_changed if p.startswith("apps/orchestrator/")]
    media = [p for p in py_changed if p.startswith("apps/media-engine/")]
    if not backend and not media:
        return

    problems = validate_changed(root, backend, media)
    if problems:
        block(
            "[stop_gate] Work is not verified:\n\n"
            + "\n\n".join(problems)
            + "\n\nFix the issue or report exactly which required check could not run. "
            "Deliberate local override: MUSE_STOP_GATE=0."
        )


if __name__ == "__main__":
    main()

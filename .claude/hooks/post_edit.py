#!/usr/bin/env python3
"""PostToolUse(Edit|Write|MultiEdit): ruff on Python files, light architectural nudges."""
from __future__ import annotations

import re
from pathlib import Path

from _common import add_context, block, project_dir, read_event, run

DB_CALLS = re.compile(r"\b(?:db|session)\.(?:execute|add|add_all|commit|delete|refresh|get|flush)\(|\bselect\(")


def main() -> None:
    event = read_event()
    raw = (event.get("tool_input") or {}).get("file_path") or ""
    if not raw:
        return
    root = project_dir()
    path = Path(raw)
    path = path if path.is_absolute() else root / path
    if not path.exists() or path.suffix != ".py":
        return
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return

    notes: list[str] = []

    # 1) Ruff: auto-fix, format, then fail only if real problems remain.
    code, _ = run(["ruff", "--version"], root, 10)
    if code == 0:
        run(["ruff", "check", "--fix", "--quiet", str(path)], root, 30)
        run(["ruff", "format", "--quiet", str(path)], root, 30)
        code, out = run(["ruff", "check", "--output-format", "concise", str(path)], root, 30)
        if code != 0 and out.strip():
            block(f"[post_edit] ruff found issues in {rel} that need a manual fix:\n{out.strip()[:1500]}")

    # 2) Layering nudge: new DB access inside HTTP routes.
    if "/muse_orchestrator/api/" in f"/{rel}" and not rel.endswith("dependencies.py"):
        hits = len(DB_CALLS.findall(path.read_text(encoding="utf-8", errors="ignore")))
        if hits:
            notes.append(
                f"{rel} still contains {hits} direct DB call(s). Routes must stay HTTP-only: "
                "new persistence logic belongs in application/ + infrastructure/repositories.py "
                "(run `python3 scripts/check_layering.py` to see the ratchet status)."
            )

    # 3) Unsafe subprocess usage.
    text = path.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"shell\s*=\s*True", text):
        notes.append(f"{rel} uses shell=True. Use an argv list (see skill `safe-ffmpeg`).")

    if notes:
        add_context("PostToolUse", " | ".join(notes))


if __name__ == "__main__":
    main()

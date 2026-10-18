#!/usr/bin/env python3
"""Layering ratchet: HTTP routes (apps/orchestrator/muse_orchestrator/api/) must not talk to the DB.

Existing violations are recorded in scripts/layering_baseline.json. The check fails only when a
file gets MORE direct DB calls than its baseline (or a new route file has any). Paying debt down is
rewarded: run with --update-baseline after refactoring to lock in the lower number.

  python3 scripts/check_layering.py                # check (exit 1 on regression)
  python3 scripts/check_layering.py --report       # per-file table
  python3 scripts/check_layering.py --update-baseline
  python3 scripts/check_layering.py --quiet        # only print on failure
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "apps" / "orchestrator" / "muse_orchestrator" / "api"
BASELINE = Path(__file__).resolve().parent / "layering_baseline.json"
EXEMPT = {"__init__.py", "dependencies.py"}
DB_OBJECTS = {"db", "session"}
DB_METHODS = {
    "execute", "add", "add_all", "commit", "rollback", "delete",
    "refresh", "get", "flush", "scalar", "scalars",
}


def count_violations(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in DB_METHODS:
            base = func.value
            if isinstance(base, ast.Name) and base.id in DB_OBJECTS:
                total += 1
        elif isinstance(func, ast.Name) and func.id == "select":
            total += 1
    return total


def scan() -> dict[str, int]:
    if not API_DIR.exists():
        return {}
    return {
        p.name: count_violations(p)
        for p in sorted(API_DIR.glob("*.py"))
        if p.name not in EXEMPT
    }


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    current = scan()

    if "--update-baseline" in argv:
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        files = sum(1 for v in current.values() if v)
        print(f"Baseline updated: {sum(current.values())} direct DB calls across {files} route files.")
        return 0

    baseline: dict[str, int] = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}

    if "--report" in argv:
        print(f"{'route file':32} {'now':>5} {'baseline':>9}")
        for name, n in sorted(current.items(), key=lambda kv: -kv[1]):
            print(f"{name:32} {n:5d} {baseline.get(name, 0):9d}")
        print(f"{'TOTAL':32} {sum(current.values()):5d} {sum(baseline.values()):9d}")
        return 0

    regressions = {n: (c, baseline.get(n, 0)) for n, c in current.items() if c > baseline.get(n, 0)}
    improved = {n: (c, baseline[n]) for n, c in current.items() if n in baseline and c < baseline[n]}

    if regressions:
        print("Layering regression: routes gained direct DB access (api -> application -> infrastructure).")
        for n, (c, b) in regressions.items():
            print(f"  {n}: {c} direct DB call(s), baseline {b}")
        print("Move the persistence into application/ services + infrastructure/repositories.py.")
        return 1

    if not quiet:
        total = sum(current.values())
        print(f"Layering OK ({total} legacy direct DB calls remain; baseline {sum(baseline.values())}).")
        if improved:
            print("Debt reduced in: " + ", ".join(improved) + " -> run --update-baseline to lock it in.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

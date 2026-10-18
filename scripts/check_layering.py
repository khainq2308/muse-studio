"""Layering ratchet for direct database access in API routes."""
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
    "execute",
    "add",
    "add_all",
    "commit",
    "rollback",
    "delete",
    "refresh",
    "get",
    "flush",
    "scalar",
    "scalars",
}


def count_violations(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in DB_METHODS:
            if isinstance(func.value, ast.Name) and func.value.id in DB_OBJECTS:
                total += 1
        elif isinstance(func, ast.Name) and func.id == "select":
            total += 1
    return total


def scan() -> dict[str, int]:
    if not API_DIR.is_dir():
        raise FileNotFoundError(f"API route directory does not exist: {API_DIR}")
    paths = [
        path for path in sorted(API_DIR.rglob("*.py"))
        if path.name not in EXEMPT
    ]
    if not paths:
        raise FileNotFoundError(f"No Python route files found under: {API_DIR}")
    return {
        path.relative_to(API_DIR).as_posix(): count_violations(path)
        for path in paths
    }


def load_baseline() -> dict[str, int]:
    if not BASELINE.exists():
        return {}
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or any(
        not isinstance(value, int) or value < 0 for value in raw.values()
    ):
        raise ValueError("Baseline must be a JSON object of non-negative integer counts.")
    return raw


def baseline_count(name: str, baseline: dict[str, int]) -> int:
    # Support the original flat-name baseline while recursively scanning API subpackages.
    return baseline.get(name, baseline.get(Path(name).name, 0))


def update_baseline(current: dict[str, int], baseline: dict[str, int]) -> int:
    regressions = {
        name: (count, baseline_count(name, baseline))
        for name, count in current.items()
        if count > baseline_count(name, baseline)
    }
    if regressions:
        print("Refusing to update baseline: fix layering regressions first.")
        for name, (count, old) in sorted(regressions.items()):
            print(f"  {name}: current {count}, baseline {old}")
        return 1

    old_total = sum(baseline.values())
    new_total = sum(current.values())
    initializing = not BASELINE.exists() and not baseline
    if initializing and new_total != 0:
        print("Refusing to initialize non-zero baseline; refactor direct DB access first.")
        return 1
    if not initializing and new_total >= old_total:
        print(
            "Refusing baseline update: count did not decrease "
            f"({old_total} -> {new_total})."
        )
        return 1

    BASELINE.write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Baseline updated after verified reduction: {old_total} -> {new_total}. "
        "Review diff."
    )
    return 0


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    try:
        current = scan()
        baseline = load_baseline()
    except (OSError, json.JSONDecodeError, ValueError, SyntaxError) as exc:
        print(f"Layering check could not run: {exc}", file=sys.stderr)
        return 2

    if "--update-baseline" in argv:
        return update_baseline(current, baseline)

    if "--report" in argv:
        print(f"{'route file':40} {'now':>5} {'baseline':>9}")
        for name, count in sorted(current.items(), key=lambda item: -item[1]):
            print(
                f"{name:40} {count:5d} "
                f"{baseline_count(name, baseline):9d}"
            )
        print(
            f"{'TOTAL':40} {sum(current.values()):5d} "
            f"{sum(baseline.values()):9d}"
        )
        return 0

    regressions = {
        name: (count, baseline_count(name, baseline))
        for name, count in current.items()
        if count > baseline_count(name, baseline)
    }
    if regressions:
        print("Layering regression: routes gained direct DB access.")
        for name, (count, old) in sorted(regressions.items()):
            print(f"  {name}: current {count}, baseline {old}")
        return 1

    if not quiet:
        print(
            f"Layering OK ({sum(current.values())} direct DB calls; "
            f"baseline {sum(baseline.values())})."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

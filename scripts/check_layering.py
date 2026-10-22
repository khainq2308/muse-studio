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
    "execute", "query", "add", "add_all", "commit", "rollback", "delete",
    "refresh", "get", "flush", "scalar", "scalars",
}


def attribute_path(node: ast.AST) -> tuple[str | None, list[str]]:
    """Resolve a dotted AST expression to its root name and attribute components."""
    attributes: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None, []
    attributes.reverse()
    return current.id, attributes


def _annotation_tail(node: ast.AST) -> str | None:
    """Get the final name of an annotation, including qualified/subscripted types."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_tail(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _annotation_tail(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return None
    return None


def _depends_on_get_db(node: ast.AST) -> bool:
    """Identify Depends(get_db) even when the parameter itself has no type annotation."""
    return any(
        isinstance(child, ast.Name) and child.id == "get_db"
        for child in ast.walk(node)
    )


def count_violations(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    # Respect renamed imports such as:
    # from sqlalchemy.ext.asyncio import AsyncSession as DatabaseSession
    session_type_names = {"AsyncSession"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy.ext.asyncio":
            for alias in node.names:
                if alias.name == "AsyncSession":
                    session_type_names.add(alias.asname or alias.name)

    # Start with historical names, then identify session objects by type/dependency,
    # and propagate simple aliases (e.g. store = database). This prevents a mere
    # local-variable rename from bypassing the layering ratchet.
    db_objects = set(DB_OBJECTS)
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            if _annotation_tail(node.annotation) in session_type_names:
                db_objects.add(node.arg)
        elif isinstance(node, (ast.AnnAssign, ast.Assign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if isinstance(node, ast.AnnAssign) and _annotation_tail(node.annotation) in session_type_names:
                for target in targets:
                    if isinstance(target, ast.Name):
                        db_objects.add(target.id)
            if isinstance(value, ast.Name) and value.id in db_objects:
                for target in targets:
                    if isinstance(target, ast.Name):
                        db_objects.add(target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            positional = list(node.args.posonlyargs) + list(node.args.args)
            defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
            for arg, default in zip(positional, defaults):
                if default is not None and _depends_on_get_db(default):
                    db_objects.add(arg.arg)
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if default is not None and _depends_on_get_db(default):
                    db_objects.add(arg.arg)

    # Alias chains can span multiple assignment statements. Resolve them to a fixpoint.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Name) or value.id not in db_objects:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in db_objects:
                    db_objects.add(target.id)
                    changed = True

    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            root, attributes = attribute_path(func)
            if not attributes or attributes[-1] not in DB_METHODS:
                continue
            # Detect direct, typed-renamed and aliased forms (db.execute, store.commit).
            # Also detect attribute chains that reach a session through another object,
            # e.g. repository.session.get(...) or self.db.execute(...). Passing the
            # session itself as an argument (db=repository.session) is not a call on it
            # and is intentionally not counted.
            reaches_session = any(part in DB_OBJECTS for part in attributes[:-1])
            if root in db_objects or reaches_session:
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

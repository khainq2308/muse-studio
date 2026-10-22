"""Layering ratchet for direct database access in API routes.

Two metrics are tracked per route module and may only go down:

* ``direct_db`` (violations): SQLAlchemy work performed in the API layer —
  ``select(...)``, session method calls (``db.execute``, ``repository.session.get``,
  ``s = repository.session; await s.commit()``), and route parameters that inject a
  database session directly (``db: AsyncSession = Depends(get_db)``).
* ``session_passthrough`` (tolerated debt, ratcheted separately): a session object
  handed from a route into another layer as a call argument
  (``GenerationQueueService.step_task(repository.session, ...)``). This is a
  deliberate interim pattern today, but it still couples routes to the session and
  must not grow.

``--report`` lists every site with its line number.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "apps" / "orchestrator" / "muse_orchestrator" / "api"
BASELINE = Path(__file__).resolve().parent / "layering_baseline.json"
EXEMPT = {"__init__.py", "dependencies.py"}
DB_OBJECTS = {"db", "session"}
DB_METHODS = {
    "execute", "query", "add", "add_all", "commit", "rollback", "delete",
    "refresh", "get", "flush", "scalar", "scalars",
}

DIRECT_DB = "direct_db"
SESSION_PASSTHROUGH = "session_passthrough"
METRICS = (DIRECT_DB, SESSION_PASSTHROUGH)

KIND_DB_CALL = "db_call"
KIND_SELECT = "select"
KIND_SESSION_INJECTION = "session_injection"
KIND_SESSION_PASSTHROUGH = "session_passthrough"
VIOLATION_KINDS = frozenset({KIND_DB_CALL, KIND_SELECT, KIND_SESSION_INJECTION})

Baseline = dict[str, dict[str, int]]


class Site(NamedTuple):
    """One detected layering site inside a route module."""

    kind: str
    line: int
    detail: str

    @property
    def metric(self) -> str:
        return DIRECT_DB if self.kind in VIOLATION_KINDS else SESSION_PASSTHROUGH


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


def _annotation_tail(node: ast.AST | None) -> str | None:
    """Get the final name of an annotation, including qualified/subscripted types.

    ``Annotated[AsyncSession, ...]`` resolves to the wrapped type.
    """
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        outer = _annotation_tail(node.value)
        if outer == "Annotated":
            inner = node.slice
            if isinstance(inner, ast.Tuple) and inner.elts:
                return _annotation_tail(inner.elts[0])
            return _annotation_tail(inner)
        return outer
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _annotation_tail(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return None
    return None


def _mentions_name(node: ast.AST | None, names: set[str]) -> bool:
    if node is None:
        return False
    return any(isinstance(child, ast.Name) and child.id in names for child in ast.walk(node))


def _source_line(lines: list[str], lineno: int) -> str:
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()[:120]
    return ""


class _ModuleAnalyzer:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        # Respect renamed imports such as
        # `from sqlalchemy.ext.asyncio import AsyncSession as DatabaseSession` and
        # `from ...core.database import get_db as provide_db`.
        self.session_type_names: set[str] = {"AsyncSession"}
        self.get_db_names: set[str] = {"get_db"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "AsyncSession":
                        self.session_type_names.add(alias.asname or alias.name)
                    elif alias.name == "get_db":
                        self.get_db_names.add(alias.asname or alias.name)
        # Module-level type aliases: DbSession = Annotated[AsyncSession, Depends(get_db)].
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                if _annotation_tail(node.value) in self.session_type_names or _mentions_name(
                    node.value, self.get_db_names
                ):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            self.session_type_names.add(target.id)
        self.db_objects: set[str] = set(DB_OBJECTS)
        self.injected_args: list[ast.arg] = []
        self._collect_session_names()

    # -- session identification -------------------------------------------------
    def is_session_expr(self, node: ast.AST | None) -> bool:
        """True when the expression evaluates to a session object itself."""
        if isinstance(node, ast.Name):
            return node.id in self.db_objects
        if isinstance(node, ast.Attribute):
            root, attributes = attribute_path(node)
            return root is not None and bool(attributes) and attributes[-1] in DB_OBJECTS
        return False

    def _is_injected(self, arg: ast.arg, default: ast.AST | None) -> bool:
        if _annotation_tail(arg.annotation) in self.session_type_names:
            return True
        if _mentions_name(arg.annotation, self.get_db_names):
            return True
        return default is not None and _mentions_name(default, self.get_db_names)

    def _collect_session_names(self) -> None:
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            positional = list(node.args.posonlyargs) + list(node.args.args)
            defaults: list[ast.AST | None] = [None] * (
                len(positional) - len(node.args.defaults)
            ) + list(node.args.defaults)
            pairs = list(zip(positional, defaults)) + list(
                zip(node.args.kwonlyargs, node.args.kw_defaults)
            )
            for arg, default in pairs:
                if self._is_injected(arg, default):
                    self.db_objects.add(arg.arg)
                    self.injected_args.append(arg)

        # Alias chains can span multiple statements: resolve to a fixpoint. Covers
        # `storage = db`, `s = repository.session`, `s: AsyncSession = ...`,
        # `(s := repository.session)` and `async with repository.session as s`.
        changed = True
        while changed:
            changed = False
            for node in ast.walk(self.tree):
                # (target, value, typed_as_session)
                bindings: list[tuple[ast.AST, ast.AST | None, bool]] = []
                if isinstance(node, ast.Assign):
                    bindings = [(target, node.value, False) for target in node.targets]
                elif isinstance(node, ast.AnnAssign):
                    typed = _annotation_tail(node.annotation) in self.session_type_names
                    bindings = [(node.target, node.value, typed)]
                elif isinstance(node, ast.NamedExpr):
                    bindings = [(node.target, node.value, False)]
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    bindings = [
                        (item.optional_vars, item.context_expr, False)
                        for item in node.items
                        if item.optional_vars is not None
                    ]
                for target, value, typed in bindings:
                    if not isinstance(target, ast.Name) or target.id in self.db_objects:
                        continue
                    if typed or (value is not None and self.is_session_expr(value)):
                        self.db_objects.add(target.id)
                        changed = True

    # -- site detection ---------------------------------------------------------
    def sites(self, lines: list[str]) -> list[Site]:
        found: list[Site] = []
        for arg in self.injected_args:
            found.append(Site(KIND_SESSION_INJECTION, arg.lineno, f"parameter '{arg.arg}' injects a DB session"))
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                root, attributes = attribute_path(func)
                if attributes and attributes[-1] in DB_METHODS:
                    # Direct, typed-renamed and aliased forms (db.execute, s.commit) and
                    # attribute chains reaching a session (repository.session.get).
                    reaches_session = any(part in DB_OBJECTS for part in attributes[:-1])
                    if root in self.db_objects or reaches_session:
                        found.append(Site(KIND_DB_CALL, node.lineno, _source_line(lines, node.lineno)))
            elif isinstance(func, ast.Name) and func.id == "select":
                found.append(Site(KIND_SELECT, node.lineno, _source_line(lines, node.lineno)))

            arguments = [
                arg.value if isinstance(arg, ast.Starred) else arg for arg in node.args
            ] + [keyword.value for keyword in node.keywords]
            for argument in arguments:
                if self.is_session_expr(argument):
                    found.append(
                        Site(
                            KIND_SESSION_PASSTHROUGH,
                            argument.lineno,
                            f"session '{ast.unparse(argument)}' passed to {ast.unparse(func)}()",
                        )
                    )
        return sorted(found, key=lambda site: (site.line, site.kind, site.detail))


def analyze_file(path: Path) -> list[Site]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    return _ModuleAnalyzer(tree).sites(source.splitlines())


def count_violations(path: Path) -> int:
    """Number of direct-DB violation sites (calls, select, session injection)."""
    return sum(1 for site in analyze_file(path) if site.metric == DIRECT_DB)


def count_passthrough(path: Path) -> int:
    """Number of sites passing a session object from a route into another layer."""
    return sum(1 for site in analyze_file(path) if site.metric == SESSION_PASSTHROUGH)


def scan_sites() -> dict[str, list[Site]]:
    if not API_DIR.is_dir():
        raise FileNotFoundError(f"API route directory does not exist: {API_DIR}")
    paths = [
        path for path in sorted(API_DIR.rglob("*.py"))
        if path.name not in EXEMPT
    ]
    if not paths:
        raise FileNotFoundError(f"No Python route files found under: {API_DIR}")
    return {path.relative_to(API_DIR).as_posix(): analyze_file(path) for path in paths}


def counts_from_sites(sites: dict[str, list[Site]]) -> Baseline:
    return {
        metric: {
            name: sum(1 for site in file_sites if site.metric == metric)
            for name, file_sites in sites.items()
        }
        for metric in METRICS
    }


def scan() -> Baseline:
    return counts_from_sites(scan_sites())


def _validate_counts(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in raw.values()
    ):
        raise ValueError("Baseline counts must be a JSON object of non-negative integers.")
    return raw


def load_baseline() -> Baseline:
    """Load the ratchet baseline.

    The legacy flat format ``{"file.py": n}`` is read as the direct_db metric with an
    empty (all-zero) session_passthrough baseline.
    """
    if not BASELINE.exists():
        return {metric: {} for metric in METRICS}
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Baseline must be a JSON object.")
    if raw and all(isinstance(value, int) for value in raw.values()):
        return {DIRECT_DB: _validate_counts(raw), SESSION_PASSTHROUGH: {}}
    unknown = set(raw) - set(METRICS)
    if unknown:
        raise ValueError(f"Unknown baseline metrics: {sorted(unknown)}")
    return {metric: _validate_counts(raw.get(metric, {})) for metric in METRICS}


def baseline_count(name: str, baseline: dict[str, int]) -> int:
    return baseline.get(name, baseline.get(Path(name).name, 0))


def find_regressions(current: Baseline, baseline: Baseline) -> dict[tuple[str, str], tuple[int, int]]:
    regressions: dict[tuple[str, str], tuple[int, int]] = {}
    for metric in METRICS:
        for name, count in current.get(metric, {}).items():
            old = baseline_count(name, baseline.get(metric, {}))
            if count > old:
                regressions[(metric, name)] = (count, old)
    return regressions


def _total(counts: Baseline) -> int:
    return sum(sum(per_file.values()) for per_file in counts.values())


def _print_regressions(
    regressions: dict[tuple[str, str], tuple[int, int]],
    sites: dict[str, list[Site]] | None = None,
) -> None:
    for (metric, name), (count, old) in sorted(regressions.items()):
        print(f"  [{metric}] {name}: current {count}, baseline {old}")
        for site in (sites or {}).get(name, []):
            if site.metric == metric:
                print(f"      {name}:{site.line} {site.kind}: {site.detail}")


def update_baseline(current: Baseline, baseline: Baseline) -> int:
    regressions = find_regressions(current, baseline)
    if regressions:
        print("Refusing to update baseline: fix layering regressions first.")
        _print_regressions(regressions)
        return 1

    old_total = _total(baseline)
    new_total = _total(current)
    initializing = not BASELINE.exists() and old_total == 0
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


def print_report(sites: dict[str, list[Site]], current: Baseline, baseline: Baseline) -> None:
    print(
        f"{'route file':32} {'direct_db':>9} {'baseline':>9} "
        f"{'passthru':>9} {'baseline':>9}"
    )
    direct, passthrough = current[DIRECT_DB], current[SESSION_PASSTHROUGH]
    ordered = sorted(direct, key=lambda name: (-direct[name], -passthrough[name], name))
    for name in ordered:
        print(
            f"{name:32} {direct[name]:9d} {baseline_count(name, baseline[DIRECT_DB]):9d} "
            f"{passthrough[name]:9d} {baseline_count(name, baseline[SESSION_PASSTHROUGH]):9d}"
        )
    print(
        f"{'TOTAL':32} {sum(direct.values()):9d} {sum(baseline[DIRECT_DB].values()):9d} "
        f"{sum(passthrough.values()):9d} {sum(baseline[SESSION_PASSTHROUGH].values()):9d}"
    )
    for metric, title in (
        (DIRECT_DB, "Direct DB access sites (violations)"),
        (SESSION_PASSTHROUGH, "Session pass-through sites (ratcheted debt)"),
    ):
        listed = [
            (name, site)
            for name in sorted(sites)
            for site in sites[name]
            if site.metric == metric
        ]
        print(f"\n{title}: {len(listed)}")
        for name, site in listed:
            print(f"  {name}:{site.line} {site.kind}: {site.detail}")


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    try:
        sites = scan_sites()
        current = counts_from_sites(sites)
        baseline = load_baseline()
    except (OSError, json.JSONDecodeError, ValueError, SyntaxError) as exc:
        print(f"Layering check could not run: {exc}", file=sys.stderr)
        return 2

    if "--update-baseline" in argv:
        return update_baseline(current, baseline)

    if "--report" in argv:
        print_report(sites, current, baseline)
        return 0

    regressions = find_regressions(current, baseline)
    if regressions:
        print("Layering regression: routes gained direct DB access or session pass-through.")
        _print_regressions(regressions, sites)
        return 1

    if not quiet:
        print(
            f"Layering OK ({sum(current[DIRECT_DB].values())} direct DB sites, "
            f"baseline {sum(baseline[DIRECT_DB].values())}; "
            f"{sum(current[SESSION_PASSTHROUGH].values())} session pass-through sites, "
            f"baseline {sum(baseline[SESSION_PASSTHROUGH].values())})."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

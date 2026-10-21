#!/usr/bin/env python3
"""Audit FastAPI router endpoints for explicit response_model declarations."""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "apps" / "orchestrator" / "muse_orchestrator" / "api"
ROUTER_MODULES = (
    "projects",
    "connections",
    "assets",
    "pipeline",
    "workflows",
    "queue",
    "batch",
    "timeline",
    "connection_accounts",
    "metrics",
    "maintenance",
    "backups",
    "system",
)
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
DIRECT_RESPONSE_TYPES = {"Response", "FileResponse", "StreamingResponse", "PlainTextResponse"}


@dataclass(frozen=True)
class RouteFinding:
    module: str
    method: str
    path: str
    function: str
    line: int
    response_model: bool
    disposition: str
    return_annotation: str

    @property
    def is_contract_gap(self) -> bool:
        return not self.response_model and self.disposition == "missing response_model"


def _name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ast.unparse(node)


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _router_prefix(tree: ast.Module) -> str:
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Call) and _name(value.func) == "APIRouter":
                prefix = _literal_string(_keyword(value, "prefix"))
                return prefix or ""
    return ""


def _full_path(prefix: str, local_path: str) -> str:
    if not local_path:
        return prefix or "/"
    if not prefix:
        return local_path if local_path.startswith("/") else f"/{local_path}"
    return f"{prefix.rstrip('/')}/{local_path.lstrip('/')}"


def _status_is_no_content(value: ast.AST | None) -> bool:
    if value is None:
        return False
    source = ast.unparse(value)
    return source in {"204", "status.HTTP_204_NO_CONTENT", "HTTP_204_NO_CONTENT"}


def _response_exception(call: ast.Call, return_annotation: str) -> str | None:
    if _status_is_no_content(_keyword(call, "status_code")):
        return "204 no-content response"
    response_class = _name(_keyword(call, "response_class"))
    if response_class in DIRECT_RESPONSE_TYPES:
        return f"direct {response_class} response"
    annotation_name = return_annotation.rsplit(".", 1)[-1]
    if annotation_name in DIRECT_RESPONSE_TYPES:
        return f"direct {annotation_name} response"
    return None


def audit_module(module_name: str) -> list[RouteFinding]:
    path = API_DIR / f"{module_name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    prefix = _router_prefix(tree)
    findings: list[RouteFinding] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "router":
                continue
            method = decorator.func.attr.lower()
            if method not in ROUTE_METHODS:
                continue

            local_path = _literal_string(decorator.args[0]) if decorator.args else None
            response_model = _keyword(decorator, "response_model") is not None
            return_annotation = ast.unparse(node.returns) if node.returns else "unannotated"
            exception = _response_exception(decorator, return_annotation)
            disposition = "typed response_model" if response_model else (exception or "missing response_model")
            findings.append(
                RouteFinding(
                    module=module_name,
                    method=method.upper(),
                    path=_full_path(prefix, local_path or "<dynamic>"),
                    function=node.name,
                    line=node.lineno,
                    response_model=response_model,
                    disposition=disposition,
                    return_annotation=return_annotation,
                )
            )
    return sorted(findings, key=lambda item: (item.path, item.method, item.function))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="print every discovered route and its contract status")
    parser.add_argument(
        "--require-typed",
        action="append",
        choices=ROUTER_MODULES,
        default=[],
        metavar="MODULE",
        help="fail on any JSON route in this router lacking response_model; may be repeated",
    )
    args = parser.parse_args()

    findings = [finding for module in ROUTER_MODULES for finding in audit_module(module)]
    typed = [finding for finding in findings if finding.response_model]
    exempt = [finding for finding in findings if not finding.response_model and finding.disposition != "missing response_model"]
    missing = [finding for finding in findings if finding.is_contract_gap]

    print(
        f"API response-model audit: {len(findings)} routes; {len(typed)} explicit response_model; "
        f"{len(exempt)} direct/no-content responses; {len(missing)} JSON contract gaps."
    )
    if args.report:
        for finding in findings:
            print(
                f"{finding.module}:{finding.line}: {finding.method:7} {finding.path} "
                f"-> {finding.function}: {finding.disposition} (return: {finding.return_annotation})"
            )

    failures = [
        finding
        for finding in findings
        if finding.module in set(args.require_typed) and finding.is_contract_gap
    ]
    if failures:
        print("\nRequired router(s) still have untyped JSON responses:", flush=True)
        for finding in failures:
            print(f"  {finding.module}:{finding.line} {finding.method} {finding.path} -> {finding.function}")
        return 1
    if args.require_typed:
        print(f"Required routers are typed: {', '.join(dict.fromkeys(args.require_typed))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

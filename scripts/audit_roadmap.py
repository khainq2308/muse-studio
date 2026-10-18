"""Heuristic implementation-gap signals; these are not behavioral acceptance tests."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def exists_any(*rels: str) -> bool:
    return any((ROOT / rel).exists() for rel in rels)


def web_files() -> list[Path]:
    base = ROOT / "apps" / "studio-web" / "src"
    if not base.exists():
        return []
    return [
        path
        for path in base.rglob("*")
        if path.is_file() and path.name != ".gitkeep"
    ]


def web_has(pattern: str) -> bool:
    regex = re.compile(pattern, re.IGNORECASE)
    base = ROOT / "apps" / "studio-web" / "src"
    return any(
        regex.search(path.relative_to(base).as_posix())
        for path in web_files()
        if path.suffix in {".ts", ".tsx"} and path.name != "App.tsx"
    )


def contracts_used() -> bool:
    return any(
        "@muse-studio/contracts" in path.read_text(encoding="utf-8", errors="ignore")
        for path in web_files()
        if path.suffix in {".ts", ".tsx"}
    )


def has_flow_lib() -> bool:
    package_json = read("apps/studio-web/package.json")
    return "reactflow" in package_json or "@xyflow" in package_json


def worker_in_compose() -> bool:
    compose = read("docker-compose.yml")
    return bool(re.search(r"arq\s+muse_orchestrator", compose) or "WorkerSettings" in compose)


def matrix_has_verified() -> bool:
    matrix = read("docs/MUSE2API_CAPABILITY_MATRIX.md")
    return bool(re.search(r"\|\s*verified\s*\|", matrix, re.IGNORECASE))


# These checks are navigation hints only, not behavioral acceptance tests.
RULES = [
    (
        "M3 Workflow Canvas (React Flow)",
        "flow-library dependency signal",
        has_flow_lib,
    ),
    (
        "M6 Timeline Editor UI",
        "separate timeline TypeScript module signal",
        lambda: web_has(r"timeline"),
    ),
    (
        "M2 Shot Board / Script Studio UI",
        "separate shot/script module signal",
        lambda: web_has(r"shotboard|shot-board|script ?studio"),
    ),
    (
        "M4 polling worker is runnable",
        "compose worker command signal",
        worker_in_compose,
    ),
    (
        "DB migrations (Alembic)",
        "migration directory/config signal",
        lambda: exists_any(
            "apps/orchestrator/migrations",
            "apps/orchestrator/alembic",
            "apps/orchestrator/alembic.ini",
        ),
    ),
    (
        "Frame Chaining uses media-engine",
        "generation_queue imports muse_media signal",
        lambda: "muse_media"
        in read("apps/orchestrator/muse_orchestrator/services/generation_queue.py"),
    ),
    (
        "packages/contracts consumed by web",
        "web imports shared contracts signal",
        contracts_used,
    ),
    (
        "Web banner updated",
        "old Milestone 1 banner absent signal",
        lambda: "Milestone 1" not in read("apps/studio-web/src/App.tsx"),
    ),
    (
        "muse2api capability matrix has a verified row",
        "one row does not verify every endpoint",
        matrix_has_verified,
    ),
]


def evaluate() -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for name, context, check in RULES:
        try:
            present, error = bool(check()), None
        except Exception as exc:
            present, error = False, str(exc)
        results.append(
            {
                "signal": name,
                "context": context,
                "present": present,
                "error": error,
            }
        )
    return results


def main() -> int:
    results = evaluate()
    if "--json" in sys.argv:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for item in results:
            if item["error"]:
                state = "ERROR"
            else:
                state = "present" if item["present"] else "GAP?"
            print(f"[{state:<7}] {item['signal']} - {item['context']}")
        missing = sum(not bool(item["present"]) for item in results)
        print(f"\n{missing} missing/failed heuristic signal(s) / {len(results)} checked.")
        print("Signals are navigation leads, not behavioral proof.")
    return 1 if any(not bool(item["present"]) for item in results) else 0


if __name__ == "__main__":
    sys.exit(main())

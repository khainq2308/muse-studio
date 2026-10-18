#!/usr/bin/env python3
"""Compare what the docs claim ("Completed") with what actually exists in the repo.

Exit code 1 if any claim is unsupported. Add rules to RULES when new milestones are declared done.
  python3 scripts/check_docs_sync.py [--json]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""


def exists_any(*rels: str) -> bool:
    return any((ROOT / r).exists() for r in rels)


def web_src_files() -> list[Path]:
    base = ROOT / "apps" / "studio-web" / "src"
    if not base.exists():
        return []
    return [p for p in base.rglob("*") if p.is_file() and p.name != ".gitkeep"]


def web_has(pattern: str) -> bool:
    """True if a real UI module/file exists whose path matches (App.tsx roadmap text is ignored)."""
    rx = re.compile(pattern, re.I)
    base = ROOT / "apps" / "studio-web" / "src"
    return any(
        rx.search(p.relative_to(base).as_posix())
        for p in web_src_files()
        if p.suffix in {".ts", ".tsx"} and p.name != "App.tsx"
    )


def contracts_used() -> bool:
    return any(
        "@muse-studio/contracts" in p.read_text(errors="ignore")
        for p in web_src_files()
        if p.suffix in {".ts", ".tsx"}
    )


def has_flow_lib() -> bool:
    pkg = read("apps/studio-web/package.json")
    return "reactflow" in pkg or "@xyflow" in pkg


def worker_in_compose() -> bool:
    compose = read("docker-compose.yml")
    return bool(re.search(r"arq\s+muse_orchestrator", compose) or "WorkerSettings" in compose)


def matrix_has_verified() -> bool:
    return bool(re.search(r"\|\s*`?verified`?\s*\|", read("docs/MUSE2API_CAPABILITY_MATRIX.md")))


# (claim, context, check)
RULES = [
    ("M3 Workflow Canvas (React Flow)", "IMPLEMENTATION_PLAN.md marks M3 completed", has_flow_lib),
    ("M6 Timeline Editor UI", "IMPLEMENTATION_PLAN.md marks M6 completed", lambda: web_has(r"timeline")),
    ("M2 Shot Board / Script Studio UI", "IMPLEMENTATION_PLAN.md marks M2 completed",
     lambda: web_has(r"shotboard|shot-board|script ?studio")),
    ("M4 polling worker is runnable", "queue_worker.py exists; compose must run it", worker_in_compose),
    ("DB migrations (Alembic)", "alembic is a dependency; schema should be versioned",
     lambda: exists_any("apps/orchestrator/migrations", "apps/orchestrator/alembic", "apps/orchestrator/alembic.ini")),
    ("ADR-005 Frame Chaining uses media-engine", "boundary frames must be real PNGs produced by FFmpeg",
     lambda: "muse_media" in read("apps/orchestrator/muse_orchestrator/services/generation_queue.py")),
    ("packages/contracts consumed by web", "shared contracts should not be dead code", contracts_used),
    ("Web banner matches roadmap", "App.tsx still says Milestone 1 while docs say M7",
     lambda: "Milestone 1" not in read("apps/studio-web/src/App.tsx")),
    ("muse2api capability matrix verified", "matrix has no endpoint marked verified", matrix_has_verified),
]


def main() -> int:
    results = []
    for name, why, check in RULES:
        try:
            ok = bool(check())
        except Exception as exc:  # a broken rule must not hide the others
            ok, why = False, f"rule error: {exc}"
        results.append({"claim": name, "context": why, "supported": ok})

    if "--json" in sys.argv:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        width = max(len(r["claim"]) for r in results)
        for r in results:
            tag = "ok " if r["supported"] else "GAP"
            print(f"[{tag}] {r['claim']:<{width}}  - {r['context']}")
        gaps = sum(not r["supported"] for r in results)
        print(f"\n{gaps} unsupported claim(s) / {len(results)} checked.")
    return 0 if all(r["supported"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

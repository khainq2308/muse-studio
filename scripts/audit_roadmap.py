#!/usr/bin/env python3
"""Heuristic implementation-gap signals; these are not behavioral acceptance tests."""
from __future__ import annotations
import json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
def read(rel):
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
def exists_any(*rels):
    return any((ROOT / rel).exists() for rel in rels)
def web_files():
    base = ROOT / "apps/studio-web/src"
    return [p for p in base.rglob("*") if p.is_file() and p.name != ".gitkeep"] if base.exists() else []
def web_has(pattern):
    rx = re.compile(pattern, re.I)
    base = ROOT / "apps/studio-web/src"
    return any(rx.search(p.relative_to(base).as_posix()) for p in web_files()
               if p.suffix in {".ts", ".tsx"} and p.name != "App.tsx")
def contracts_used():
    return any("@muse-studio/contracts" in p.read_text(errors="ignore") for p in web_files() if p.suffix in {".ts", ".tsx"})
def has_flow_lib():
    pkg = read("apps/studio-web/package.json")
    return "reactflow" in pkg or "@xyflow" in pkg
def worker_in_compose():
    compose = read("docker-compose.yml")
    return bool(re.search(r"arq\s+muse_orchestrator", compose) or "WorkerSettings" in compose)
def matrix_has_verified():
    return bool(re.search(r"\|\s*verified\s*\|", read("docs/MUSE2API_CAPABILITY_MATRIX.md")))
RULES = [
    ("M3 Workflow Canvas (React Flow)", "flow-library dependency signal", has_flow_lib),
    ("M6 Timeline Editor UI", "separate timeline TypeScript module signal", lambda: web_has(r"timeline")),
    ("M2 Shot Board / Script Studio UI", "separate shot/script module signal", lambda: web_has(r"shotboard|shot-board|script ?studio")),
    ("M4 polling worker is runnable", "compose worker command signal", worker_in_compose),
    ("DB migrations (Alembic)", "migration directory/config signal", lambda: exists_any("apps/orchestrator/migrations", "apps/orchestrator/alembic", "apps/orchestrator/alembic.ini")),
    ("Frame Chaining uses media-engine", "generation_queue imports muse_media signal", lambda: "muse_media" in read("apps/orchestrator/muse_orchestrator/services/generation_queue.py")),
    ("packages/contracts consumed by web", "web imports shared contracts signal", contracts_used),
    ("Web banner updated", "old Milestone 1 banner absent signal", lambda: "Milestone 1" not in read("apps/studio-web/src/App.tsx")),
    ("muse2api capability matrix has a verified row", "one row does not verify every endpoint", matrix_has_verified),
]
def evaluate():
    results = []
    for name, context, check in RULES:
        try: present, error = bool(check()), None
        except Exception as exc: present, error = False, str(exc)
        results.append({"signal": name, "context": context, "present": present, "error": error})
    return results
def main():
    results = evaluate()
    if "--json" in sys.argv:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for item in results:
            state = "ERROR" if item["error"] else ("present" if item["present"] else "GAP?")
            print(f"[{state:<7}] {item['signal']} - {item['context']}")
        print(f"\n{sum(not item['present'] for item in results)} missing/failed heuristic signal(s) / {len(results)} checked.")
        print("Signals are navigation leads, not behavioral proof.")
    return 1 if any(not item["present"] for item in results) else 0
if __name__ == "__main__":
    sys.exit(main())

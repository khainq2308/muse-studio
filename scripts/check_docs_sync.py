#!/usr/bin/env python3
"""Compare explicit roadmap statuses with heuristic implementation signals, not behavior."""
from __future__ import annotations
import json, re, sys
from pathlib import Path
from audit_roadmap import evaluate
ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs/IMPLEMENTATION_PLAN.md"
MILESTONE_SIGNAL = {
    "M2": "M2 Shot Board / Script Studio UI",
    "M3": "M3 Workflow Canvas (React Flow)",
    "M4": "M4 polling worker is runnable",
    "M5": "Frame Chaining uses media-engine",
    "M6": "M6 Timeline Editor UI",
}
def is_complete(status):
    return bool(re.search(r"\b(completed|complete|done)\b", status, re.I))
def main():
    if not PLAN.is_file():
        print("[FAIL] Missing docs/IMPLEMENTATION_PLAN.md")
        return 1
    plan = PLAN.read_text(encoding="utf-8")
    signals = {item["signal"]: item for item in evaluate()}
    results = []
    for milestone, signal_name in MILESTONE_SIGNAL.items():
        match = re.search(rf"\b{milestone}\s*\(([^)]+)\)", plan, re.I)
        if not match:
            results.append({"milestone": milestone, "status": "MISSING", "consistent": False})
            continue
        status = match.group(1).strip()
        done, signal_present = is_complete(status), bool(signals[signal_name]["present"])
        consistent = done == signal_present
        results.append({"milestone": milestone, "status": status, "signal": signal_name,
                        "signal_present": signal_present, "consistent": consistent})
    if "--json" in sys.argv:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for item in results:
            note = "heuristic agreement only; acceptance evidence still required" if item["consistent"] else "status and heuristic signal differ"
            print(f"[{'OK' if item['consistent'] else 'DOC-DRIFT'}] {item['milestone']} ({item['status']}) - {note}")
        print(f"\n{sum(not item['consistent'] for item in results)} status mismatch(es) / {len(results)} milestones checked.")
    return 0 if all(item["consistent"] for item in results) else 1
if __name__ == "__main__":
    sys.exit(main())

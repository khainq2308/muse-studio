"""Compare explicit roadmap statuses with heuristic signals, not feature behavior."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from audit_roadmap import evaluate

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs" / "IMPLEMENTATION_PLAN.md"
MILESTONE_SIGNAL = {
    "M2": "M2 Shot Board / Script Studio UI",
    "M3": "M3 Workflow Canvas (React Flow)",
    "M4": "M4 polling worker is runnable",
    "M5": "Frame Chaining uses media-engine",
    "M6": "M6 Timeline Editor UI",
}


def is_complete(status: str) -> bool:
    return bool(
        re.search(r"\b(completed|complete|done)\b", status, re.IGNORECASE)
    )


def main() -> int:
    if not PLAN.is_file():
        print("[FAIL] Missing docs/IMPLEMENTATION_PLAN.md")
        return 1

    plan = PLAN.read_text(encoding="utf-8")
    signals = {item["signal"]: item for item in evaluate()}
    results: list[dict[str, object]] = []

    for milestone, signal_name in MILESTONE_SIGNAL.items():
        match = re.search(
            rf"\b{milestone}\s*\(([^)]+)\)", plan, re.IGNORECASE
        )
        if not match:
            results.append(
                {
                    "milestone": milestone,
                    "status": "MISSING",
                    "consistent": False,
                }
            )
            continue

        status = match.group(1).strip()
        done = is_complete(status)
        signal_present = bool(signals[signal_name]["present"])
        consistent = done == signal_present
        results.append(
            {
                "milestone": milestone,
                "status": status,
                "signal": signal_name,
                "signal_present": signal_present,
                "consistent": consistent,
            }
        )

    if "--json" in sys.argv:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for item in results:
            if item["consistent"]:
                note = "heuristic agreement only; acceptance evidence still required"
            else:
                note = "status and heuristic signal differ"
            print(
                f"[{'OK' if item['consistent'] else 'DOC-DRIFT'}] "
                f"{item['milestone']} ({item['status']}) - {note}"
            )
        mismatches = sum(not bool(item["consistent"]) for item in results)
        print(
            f"\n{mismatches} status mismatch(es) / "
            f"{len(results)} milestones checked."
        )
    return 0 if all(bool(item["consistent"]) for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())

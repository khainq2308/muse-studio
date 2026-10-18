"""Validate roadmap status labels and capability vocabulary; do not infer feature completion."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs" / "IMPLEMENTATION_PLAN.md"
MATRIX = ROOT / "docs" / "MUSE2API_CAPABILITY_MATRIX.md"
EXPECTED_MILESTONES = tuple(f"M{number}" for number in range(8))
ALLOWED_MILESTONE_STATUSES = {
    "completed",
    "partial",
    "planned",
    "in progress",
    "blocked",
    "not started",
    "deferred",
    "cancelled",
}
ALLOWED_CAPABILITY_STATUSES = {
    "verified",
    "unverified",
    "mock-only",
    "unsupported",
}
MILESTONE_LINE = re.compile(r"^\s*[*-]\s+\*\*M(\d+)\s+\(([^)]+)\):\*\*", re.IGNORECASE)


def validate_plan(plan_text: str) -> list[str]:
    errors: list[str] = []
    found: dict[str, list[str]] = {milestone: [] for milestone in EXPECTED_MILESTONES}

    for line_number, line in enumerate(plan_text.splitlines(), start=1):
        match = MILESTONE_LINE.match(line)
        if not match:
            continue
        milestone = f"M{int(match.group(1))}"
        status = " ".join(match.group(2).strip().lower().split())
        if milestone not in found:
            errors.append(f"line {line_number}: unknown milestone {milestone}")
            continue
        found[milestone].append(status)
        if status not in ALLOWED_MILESTONE_STATUSES:
            errors.append(
                f"line {line_number}: {milestone} has unknown status {status!r}; "
                f"allowed: {', '.join(sorted(ALLOWED_MILESTONE_STATUSES))}"
            )

    for milestone, statuses in found.items():
        if not statuses:
            errors.append(f"{milestone}: explicit status is missing")
        elif len(statuses) > 1:
            errors.append(f"{milestone}: status is declared {len(statuses)} times")
    return errors


def validate_capability_matrix(matrix_text: str) -> list[str]:
    errors: list[str] = []
    for line_number, line in enumerate(matrix_text.splitlines(), start=1):
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0].lower() in {"capability / endpoint", "---"} or set(cells[2]) <= {"-", ":"}:
            continue
        status = cells[2].strip(chr(96)).lower()
        if status not in ALLOWED_CAPABILITY_STATUSES:
            errors.append(
                f"line {line_number}: capability status {status!r} is invalid; "
                f"allowed: {', '.join(sorted(ALLOWED_CAPABILITY_STATUSES))}"
            )
            continue

        if status == "verified":
            if len(cells) < 5:
                errors.append(
                    f"line {line_number}: verified capability row must include evidence and fallback columns"
                )
                continue
            evidence = cells[3]
            date_match = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", evidence)
            path_match = re.search(r"docs/evidence/[^\s|)]+", evidence)
            if not date_match or not path_match:
                errors.append(
                    f"line {line_number}: verified capability needs an ISO date and a docs/evidence/ artifact path"
                )
                continue
            evidence_path = ROOT / path_match.group(0).rstrip(".,;:")
            if not evidence_path.is_file():
                errors.append(
                    f"line {line_number}: verified capability evidence artifact does not exist: "
                    f"{path_match.group(0)}"
                )
    return errors


def validate_documents(plan_text: str, matrix_text: str) -> list[str]:
    return validate_plan(plan_text) + validate_capability_matrix(matrix_text)


def main() -> int:
    missing = [path for path in (PLAN, MATRIX) if not path.is_file()]
    if missing:
        for path in missing:
            print(f"[FAIL] Missing required document: {path.relative_to(ROOT)}")
        return 1

    errors = validate_documents(
        PLAN.read_text(encoding="utf-8"),
        MATRIX.read_text(encoding="utf-8"),
    )
    if errors:
        for error in errors:
            print(f"[DOC-INVALID] {error}")
        print(f"Documentation metadata validation failed: {len(errors)} issue(s).")
        return 1

    print(
        "[OK] milestone statuses are explicit and use the allowed vocabulary; "
        "capability matrix statuses are valid."
    )
    print(
        "This check does not prove feature behavior or infer completion from filenames/dependencies. "
        "Use audit_roadmap.py as a heuristic inventory and verify code/tests/runtime evidence manually."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

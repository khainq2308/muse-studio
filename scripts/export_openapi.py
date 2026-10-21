#!/usr/bin/env python3
"""Export the FastAPI app's OpenAPI document without starting service dependencies.

The script intentionally selects the development settings profile before importing
FastAPI settings. Importing the app constructs routes and schemas only; its lifespan
(which connects to the database and Redis) is never entered.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR_ROOT = ROOT / "apps" / "orchestrator"
DEFAULT_OUTPUT = ORCHESTRATOR_ROOT / "openapi.json"


def build_schema() -> dict[str, Any]:
    # Set this explicitly before importing Settings/FastAPI app so that a local
    # .env file for production cannot make schema export fail or use prod mode.
    os.environ["ENVIRONMENT"] = "development"
    sys.path.insert(0, str(ORCHESTRATOR_ROOT))

    from muse_orchestrator.main import app

    return app.openapi()


def serialize_schema(schema: dict[str, Any]) -> str:
    """Return canonical JSON so repeated exports produce a byte-for-byte stable file."""
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"snapshot path (default: {DEFAULT_OUTPUT.relative_to(ROOT)})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the tracked snapshot differs; do not write the file",
    )
    args = parser.parse_args(argv)

    output = args.output if args.output.is_absolute() else Path.cwd() / args.output
    expected = serialize_schema(build_schema())

    if args.check:
        if not output.is_file():
            print(f"[OPENAPI-DRIFT] Snapshot is missing: {output}", file=sys.stderr)
            print("Run scripts/export_openapi.py to create it.", file=sys.stderr)
            return 1
        actual = output.read_text(encoding="utf-8")
        if actual != expected:
            print(f"[OPENAPI-DRIFT] Snapshot is stale: {output}", file=sys.stderr)
            print("Run scripts/export_openapi.py and review the resulting diff.", file=sys.stderr)
            return 1
        print(f"[OK] OpenAPI snapshot is current: {output}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(expected, encoding="utf-8", newline="\n")
    print(f"[OK] Exported OpenAPI snapshot: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

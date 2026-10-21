from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXPORTER = ROOT / "scripts" / "export_openapi.py"


def run_export(output: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # The exporter must override this so a developer's production shell cannot
    # accidentally make an offline schema export require production secrets.
    env["ENVIRONMENT"] = "production"
    return subprocess.run(
        [sys.executable, str(EXPORTER), "--output", str(output), *arguments],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def test_openapi_export_is_reproducible_and_does_not_need_service_lifespan(tmp_path: Path) -> None:
    snapshot = tmp_path / "openapi.json"

    first = run_export(snapshot)
    assert first.returncode == 0, first.stderr
    first_bytes = snapshot.read_bytes()

    schema = json.loads(first_bytes)
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == "MuseStudio Orchestrator API"
    assert "/healthz" in schema["paths"]
    assert "/api/v1/projects" in schema["paths"]

    check = run_export(snapshot, "--check")
    assert check.returncode == 0, check.stderr
    assert snapshot.read_bytes() == first_bytes


def test_openapi_check_detects_drift_without_rewriting_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "openapi.json"
    snapshot.write_text("{}\n", encoding="utf-8")

    check = run_export(snapshot, "--check")

    assert check.returncode == 1
    assert "[OPENAPI-DRIFT] Snapshot is stale" in check.stderr
    assert snapshot.read_text(encoding="utf-8") == "{}\n"

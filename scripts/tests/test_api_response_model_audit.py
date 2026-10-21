from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDITOR = ROOT / "scripts" / "audit_api_response_models.py"


def test_empty_router_path_is_reported_as_the_router_prefix_not_dynamic() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(AUDITOR),
            "--report",
            "--require-typed",
            "workflows",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(
        r"workflows:\d+: POST\s+/api/v1/projects/\{project_id\}/workflows -> "
        r"create_workflow: typed response_model",
        result.stdout,
    )
    assert "create_workflow: typed response_model (return: WorkflowCreateOut)" in result.stdout
    assert "POST    /api/v1/projects/{project_id}/workflows/<dynamic> -> create_workflow" not in result.stdout

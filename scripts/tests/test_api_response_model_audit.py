from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDITOR = ROOT / "scripts" / "audit_api_response_models.py"


class ApiResponseModelAuditTests(unittest.TestCase):
    def test_empty_router_path_is_reported_as_the_router_prefix_not_dynamic(self) -> None:
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

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(
            result.stdout,
            r"workflows:\d+: POST\s+/api/v1/projects/\{project_id\}/workflows -> "
            r"create_workflow: typed response_model",
        )
        self.assertIn(
            "create_workflow: typed response_model (return: WorkflowCreateOut)",
            result.stdout,
        )
        self.assertNotIn(
            "POST    /api/v1/projects/{project_id}/workflows/<dynamic> -> create_workflow",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()

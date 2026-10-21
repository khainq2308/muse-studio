from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUDITOR = ROOT / "scripts" / "audit_openapi_responses.py"


def test_response_audit_reports_generic_objects_and_array_items_but_not_typed_nullable_fields(
    tmp_path: Path,
) -> None:
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/typed": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/TypedOut"}
                                }
                            }
                        }
                    }
                }
            },
            "/generic-object": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            }
                        }
                    }
                }
            },
            "/generic-list": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"type": "object", "additionalProperties": True},
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/empty": {
                "delete": {"responses": {"204": {"description": "No Content"}}}
            },
        },
        "components": {
            "schemas": {
                "TypedOut": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                        },
                    },
                    "required": ["id", "label"],
                }
            }
        },
    }
    schema_path = tmp_path / "openapi.json"
    schema_path.write_text(json.dumps(spec), encoding="utf-8")

    report = subprocess.run(
        [sys.executable, str(AUDITOR), "--schema", str(schema_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert report.returncode == 0, report.stderr
    assert "OpenAPI operations audited: 4" in report.stdout
    assert "Generic success response schemas: 2" in report.stdout
    assert "/generic-object [200 application/json]" in report.stdout
    assert "/generic-list [200 application/json]" in report.stdout
    assert "/typed" not in report.stdout
    assert "/empty" not in report.stdout

    strict = subprocess.run(
        [sys.executable, str(AUDITOR), "--schema", str(schema_path), "--strict"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert strict.returncode == 1, strict.stdout

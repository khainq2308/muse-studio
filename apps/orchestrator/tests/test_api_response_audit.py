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
    assert "Generic or missing success response schemas: 2" in report.stdout
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


def test_response_audit_flags_missing_content_and_schema_but_exempts_bodyless_statuses(
    tmp_path: Path,
) -> None:
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/missing-content": {
                "get": {"responses": {"200": {"description": "Success"}}}
            },
            "/empty-content": {
                "get": {"responses": {"201": {"description": "Created", "content": {}}}}
            },
            "/missing-schema": {
                "get": {
                    "responses": {
                        "202": {"description": "Accepted", "content": {"application/json": {}}}
                    }
                }
            },
            "/referenced-missing-content": {
                "get": {"responses": {"200": {"$ref": "#/components/responses/NoBodyDocumentation"}}}
            },
            "/typed-response-ref": {
                "get": {"responses": {"200": {"$ref": "#/components/responses/TypedResponse"}}}
            },
            "/no-content": {
                "delete": {"responses": {"204": {"description": "No Content"}}}
            },
            "/reset-content": {
                "post": {"responses": {"205": {"description": "Reset Content"}}}
            },
        },
        "components": {
            "schemas": {
                "TypedOut": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                }
            },
            "responses": {
                "NoBodyDocumentation": {"description": "Missing content must be reported"},
                "TypedResponse": {
                    "description": "Typed response",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/TypedOut"}
                        }
                    },
                },
            },
        },
    }
    schema_path = tmp_path / "openapi-missing-content.json"
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
    assert "OpenAPI operations audited: 7" in report.stdout
    assert "Generic or missing success response schemas: 4" in report.stdout
    assert "/missing-content [200 (missing content)]" in report.stdout
    assert "/empty-content [201 (missing content)]" in report.stdout
    assert "/missing-schema [202 application/json]" in report.stdout
    assert "/referenced-missing-content [200 (missing content)]" in report.stdout
    assert "/typed-response-ref" not in report.stdout
    assert "/no-content" not in report.stdout
    assert "/reset-content" not in report.stdout

    strict = subprocess.run(
        [sys.executable, str(AUDITOR), "--schema", str(schema_path), "--strict"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert strict.returncode == 1, strict.stdout


def test_response_audit_fails_closed_for_unresolvable_response_reference(tmp_path: Path) -> None:
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/bad-ref": {
                "get": {
                    "responses": {
                        "200": {"$ref": "#/components/responses/DoesNotExist"}
                    }
                }
            }
        },
        "components": {"responses": {}},
    }
    schema_path = tmp_path / "openapi-bad-ref.json"
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
    assert "Generic or missing success response schemas: 1" in report.stdout
    assert "/bad-ref [200 (unresolved response reference)]" in report.stdout

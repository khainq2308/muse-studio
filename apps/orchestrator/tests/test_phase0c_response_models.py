from __future__ import annotations

import os
import tempfile

import pytest
from pydantic import ValidationError

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="muse_phase0c_contracts_"))

from muse_orchestrator.api.workflows import WorkflowRunRequestIn  # noqa: E402
from muse_orchestrator.main import app  # noqa: E402


def _success_response_schema(operation: dict) -> dict:
    for status_code, response in operation["responses"].items():
        if status_code.startswith("2") and "application/json" in response.get("content", {}):
            return response["content"]["application/json"]["schema"]
    raise AssertionError(f"No JSON success response found: {operation['responses']}")


def _assert_schema_ref(schema: dict, expected_name: str, *, array: bool = False) -> None:
    target = schema.get("items", {}) if array else schema
    assert target.get("$ref") == f"#/components/schemas/{expected_name}", target


@pytest.mark.parametrize(
    ("method", "path", "schema_name", "array"),
    [
        ("post", "/api/v1/queue/tasks", "EnqueueTaskOut", False),
        ("get", "/api/v1/queue/tasks", "QueueTaskOut", True),
        ("post", "/api/v1/queue/tasks/{task_id}/step", "QueueTaskOut", False),
        ("post", "/api/v1/queue/tasks/{task_id}/pause", "QueueTaskOut", False),
        ("post", "/api/v1/queue/tasks/{task_id}/resume", "QueueTaskOut", False),
        ("post", "/api/v1/queue/tasks/{task_id}/cancel", "QueueTaskOut", False),
        ("get", "/api/v1/queue/shots/{shot_id}/takes", "ShotTakeOut", True),
        ("post", "/api/v1/queue/shots/{shot_id}/takes/{take_id}/select", "SelectShotTakeOut", False),
        ("post", "/api/v1/projects/{project_id}/batch/launch", "BatchLaunchOut", False),
        ("post", "/api/v1/projects/{project_id}/batch/tick", "BatchTickOut", False),
        ("post", "/api/v1/projects/{project_id}/batch/retry-failed", "RetryFailedOut", False),
        ("post", "/api/v1/projects/{project_id}/shots/{shot_id}/takes/{take_id}/flag-continuity", "ContinuityFlagOut", False),
        ("get", "/api/v1/projects/{project_id}/shots/{shot_id}/compare-takes", "CompareTakesOut", False),
    ],
)
def test_queue_and_batch_responses_have_explicit_openapi_contracts(method, path, schema_name, array):
    schema = app.openapi()["paths"][path][method]
    _assert_schema_ref(_success_response_schema(schema), schema_name, array=array)


@pytest.mark.parametrize("run_mode", ["from_here", "downstream"])
def test_partial_workflow_run_requires_start_node_id(run_mode: str) -> None:
    with pytest.raises(ValidationError, match="start_node_id is required"):
        WorkflowRunRequestIn(run_mode=run_mode)


@pytest.mark.parametrize("run_mode", ["full", "dry_run"])
def test_full_and_dry_run_workflow_modes_do_not_require_start_node_id(run_mode: str) -> None:
    request = WorkflowRunRequestIn(run_mode=run_mode)
    assert request.start_node_id is None


def test_workflow_run_mode_is_an_openapi_enum() -> None:
    schema = app.openapi()["components"]["schemas"]["WorkflowRunRequestIn"]["properties"]["run_mode"]
    assert schema["enum"] == ["full", "dry_run", "from_here", "downstream"]

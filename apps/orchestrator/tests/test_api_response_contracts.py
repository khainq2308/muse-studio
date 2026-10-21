from __future__ import annotations

from typing import Any

from muse_orchestrator.main import app


def _response_schema(
    openapi: dict[str, Any],
    method: str,
    path: str,
    status_code: str,
) -> dict[str, Any]:
    return openapi["paths"][path][method]["responses"][status_code]["content"]["application/json"]["schema"]


def _model_name(schema: dict[str, Any]) -> str:
    ref = schema.get("$ref", "")
    assert ref.startswith("#/components/schemas/"), f"Expected a concrete response model ref, got {schema}"
    return ref.rsplit("/", 1)[-1]


def test_queue_task_and_take_routes_publish_explicit_response_models() -> None:
    openapi = app.openapi()

    expected = {
        ("post", "/api/v1/queue/tasks", "201"): "EnqueuedQueueTaskOut",
        ("get", "/api/v1/queue/tasks", "200"): "QueueTaskOut",
        ("post", "/api/v1/queue/tasks/{task_id}/step", "200"): "QueueTaskOut",
        ("post", "/api/v1/queue/tasks/{task_id}/pause", "200"): "QueueTaskOut",
        ("post", "/api/v1/queue/tasks/{task_id}/resume", "200"): "QueueTaskOut",
        ("post", "/api/v1/queue/tasks/{task_id}/cancel", "200"): "QueueTaskOut",
        ("get", "/api/v1/queue/shots/{shot_id}/takes", "200"): "ShotTakeOut",
        ("post", "/api/v1/queue/shots/{shot_id}/takes/{take_id}/select", "200"): "ShotTakeSelectionOut",
    }

    for (method, path, status_code), expected_model in expected.items():
        schema = _response_schema(openapi, method, path, status_code)
        if schema.get("type") == "array":
            schema = schema["items"]
        assert _model_name(schema) == expected_model, f"{method.upper()} {path}"


def test_queue_task_state_and_enqueue_task_type_are_openapi_enums() -> None:
    openapi = app.openapi()
    components = openapi["components"]["schemas"]

    state_schema = components["QueueTaskOut"]["properties"]["state"]
    assert _model_name(state_schema) == "QueueTaskState"
    assert set(components["QueueTaskState"]["enum"]) == {
        "queued",
        "paused",
        "running",
        "succeeded",
        "failed",
        "poll_timeout",
        "canceled",
    }

    task_type_schema = components["EnqueueTaskIn"]["properties"]["task_type"]
    assert set(task_type_schema["enum"]) == {"image", "video"}

    state_parameters = openapi["paths"]["/api/v1/queue/tasks"]["get"]["parameters"]
    state_parameter = next(parameter for parameter in state_parameters if parameter["name"] == "state")
    assert _model_name(state_parameter["schema"]) == "QueueTaskState"

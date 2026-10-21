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
        ("post", "/api/v1/projects/{project_id}/scripts/import", "201"): "ScriptImportOut",
        ("get", "/api/v1/projects/{project_id}/story-bible", "200"): "StoryBibleEntryOut",
        ("post", "/api/v1/projects/{project_id}/story-bible", "201"): "StoryBibleUpsertOut",
        ("get", "/api/v1/projects/{project_id}/shots", "200"): "ProjectShotOut",
        ("get", "/api/v1/projects/{project_id}/shots/export", "200"): "ProjectShotsExportOut",
        ("patch", "/api/v1/projects/{project_id}/shots/{shot_id}", "200"): "ShotReviewOut",
        ("post", "/api/v1/projects/{project_id}/batch/launch", "201"): "BatchLaunchOut",
        ("post", "/api/v1/projects/{project_id}/batch/tick", "200"): "BatchTickOut",
        ("post", "/api/v1/projects/{project_id}/batch/retry-failed", "200"): "RetryFailedTasksOut",
        ("post", "/api/v1/projects/{project_id}/shots/{shot_id}/takes/{take_id}/flag-continuity", "200"): "ContinuityFlagOut",
        ("get", "/api/v1/projects/{project_id}/shots/{shot_id}/compare-takes", "200"): "CompareShotTakesOut",
    }

    for (method, path, status_code), expected_model in expected.items():
        schema = _response_schema(openapi, method, path, status_code)
        if schema.get("type") == "array":
            schema = schema["items"]
        assert _model_name(schema) == expected_model, f"{method.upper()} {path}"



def test_shot_export_documents_json_and_csv_media_types() -> None:
    openapi = app.openapi()
    content = openapi["paths"]["/api/v1/projects/{project_id}/shots/export"]["get"]["responses"]["200"]["content"]

    assert _model_name(content["application/json"]["schema"]) == "ProjectShotsExportOut"
    assert content["text/csv"]["schema"]["type"] == "string"


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
    state_schema = state_parameter["schema"]
    state_refs = {item.get("$ref") for item in state_schema.get("anyOf", [])}
    state_refs.add(state_schema.get("$ref"))
    assert "#/components/schemas/QueueTaskState" in state_refs

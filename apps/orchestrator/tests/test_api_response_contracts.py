from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from muse_orchestrator.api.workflows import WorkflowRunRequestIn
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
        ("get", "/api/v1/queue/tasks", "200"): "QueueTaskPageOut",
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
        "awaiting_input",
    }

    task_type_schema = components["EnqueueTaskIn"]["properties"]["task_type"]
    assert set(task_type_schema["enum"]) == {"image", "video"}

    state_parameters = openapi["paths"]["/api/v1/queue/tasks"]["get"]["parameters"]
    state_parameter = next(parameter for parameter in state_parameters if parameter["name"] == "state")
    state_schema = state_parameter["schema"]
    state_refs = {item.get("$ref") for item in state_schema.get("anyOf", [])}
    state_refs.add(state_schema.get("$ref"))
    assert "#/components/schemas/QueueTaskState" in state_refs


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


def test_workflow_routes_publish_explicit_response_models() -> None:
    openapi = app.openapi()
    expected = {
        ("post", "/api/v1/projects/{project_id}/workflows", "201"): "WorkflowCreateOut",
        ("put", "/api/v1/projects/{project_id}/workflows/{workflow_id}/graph", "200"): "WorkflowGraphUpdateOut",
        ("patch", "/api/v1/projects/{project_id}/workflows/{workflow_id}/layout", "200"): "WorkflowLayoutSaveOut",
        ("post", "/api/v1/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/nodes/{node_id}/approve", "200"): "WorkflowApprovalOut",
    }
    for (method, path, status_code), expected_model in expected.items():
        schema = _response_schema(openapi, method, path, status_code)
        assert _model_name(schema) == expected_model, f"{method.upper()} {path}"

    run_schema = _response_schema(
        openapi,
        "post",
        "/api/v1/projects/{project_id}/workflows/{workflow_id}/runs",
        "201",
    )
    model_refs = {
        item.get("$ref", "").rsplit("/", 1)[-1]
        for item in run_schema.get("anyOf", run_schema.get("oneOf", []))
    }
    assert model_refs == {"WorkflowDryRunOut", "WorkflowExecutionOut"}

    components = openapi["components"]["schemas"]
    assert _model_name(components["WorkflowExecutionOut"]["properties"]["node_executions"]["items"]) == "WorkflowNodeExecutionOut"
    assert _model_name(components["WorkflowNodeExecutionOut"]["properties"]["state"]) == "NodeExecutionState"


def test_timeline_and_export_routes_publish_explicit_response_models() -> None:
    openapi = app.openapi()
    expected = {
        ("post", "/api/v1/projects/{project_id}/timeline/assemble", "201"): "TimelineAssembleOut",
        ("get", "/api/v1/projects/{project_id}/timeline", "200"): "TimelineSequenceOut",
        ("post", "/api/v1/projects/{project_id}/timeline/exports", "202"): "ExportLaunchOut",
        ("get", "/api/v1/projects/{project_id}/timeline/exports/{export_id}", "200"): "ExportJobStatusOut",
    }
    for (method, path, status_code), expected_model in expected.items():
        schema = _response_schema(openapi, method, path, status_code)
        assert _model_name(schema) == expected_model, f"{method.upper()} {path}"

    components = openapi["components"]["schemas"]
    manifest = components["TimelineManifestOut"]["properties"]
    assert _model_name(manifest["video_clips"]["items"]) == "TimelineClipOut"
    assert _model_name(manifest["audio_tracks"]["items"]) == "TimelineAudioTrackOut"
    assert _model_name(components["TimelineAssembleOut"]["properties"]["manifest"]) == "TimelineManifestOut"
    assert _model_name(components["TimelineSequenceOut"]["properties"]["manifest"]) == "TimelineManifestOut"

    export_status = components["ExportJobStatusOut"]["properties"]
    assert _model_name(export_status["state"]) == "ExportJobState"
    assert set(components["ExportJobState"]["enum"]) == {
        "queued", "running", "succeeded", "failed", "canceled",
    }
    launch_state = components["ExportLaunchOut"]["properties"]["state"]
    assert launch_state.get("enum") == ["queued"] or launch_state.get("const") == "queued"


def test_operations_routes_publish_explicit_response_models() -> None:
    openapi = app.openapi()
    expected = {
        ("post", "/api/v1/connections", "201"): "ConnectionOut",
        ("get", "/api/v1/connections", "200"): "ConnectionOut",
        ("post", "/api/v1/connections/{connection_id}/test", "200"): "ConnectionOut",
        ("post", "/api/v1/projects/{project_id}/backup", "201"): "BackupOut",
        ("get", "/api/v1/projects/{project_id}/backups", "200"): "BackupOut",
        ("post", "/api/v1/backups/restore", "201"): "BackupRestoreOut",
        ("post", "/api/v1/maintenance/orphans/scan", "200"): "OrphanScanOut",
        ("post", "/api/v1/maintenance/orphans/cleanup", "200"): "OrphanCleanupOut",
        ("get", "/api/v1/metrics/summary", "200"): "MetricsSummaryOut",
        ("post", "/api/v1/system/logs", "201"): "StructuredLogOut",
        ("get", "/api/v1/system/logs", "200"): "StructuredLogOut",
        ("get", "/api/v1/system/diagnostics", "200"): "SystemDiagnosticsOut",
    }
    for (method, path, status_code), expected_model in expected.items():
        schema = _response_schema(openapi, method, path, status_code)
        if schema.get("type") == "array":
            schema = schema["items"]
        assert _model_name(schema) == expected_model, f"{method.upper()} {path}"

    components = openapi["components"]["schemas"]
    assert _model_name(components["BackupOut"]["properties"]["manifest_json"]) == "BackupManifestOut"
    assert _model_name(components["BackupRestoreOut"]["properties"]["report"]) == "BackupRestoreReportOut"
    assert _model_name(components["OrphanScanOut"]["properties"]["orphans"]["items"]) == "OrphanFileOut"
    assert _model_name(components["OrphanCleanupOut"]["properties"]["orphans"]["items"]) == "OrphanFileOut"

    metrics = components["MetricsSummaryOut"]["properties"]
    assert _model_name(metrics["service"]) == "ServiceMetricsOut"
    assert _model_name(metrics["connections"]) == "ConnectionMetricsOut"
    assert _model_name(metrics["queue"]) == "QueueMetricsOut"
    assert _model_name(metrics["storage"]) == "StorageMetricsOut"

    diagnostics = components["SystemDiagnosticsOut"]["properties"]
    assert _model_name(diagnostics["summary"]) == "MetricsSummaryOut"
    assert _model_name(diagnostics["accounts"]["items"]) == "DiagnosticAccountOut"
    assert _model_name(diagnostics["recent_audit_events"]["items"]) == "DiagnosticAuditEventOut"
    assert _model_name(diagnostics["recent_logs"]["items"]) == "DiagnosticRecentLogOut"
    assert _model_name(components["ConnectionOut"]["properties"]["health_state"]) == "ConnectionHealthState"


def test_binary_downloads_and_revision_snapshots_have_concrete_schemas() -> None:
    openapi = app.openapi()

    asset_content = openapi["paths"]["/api/v1/assets/{asset_id}/download"]["get"]["responses"]["200"]["content"]
    asset_binary = asset_content["application/octet-stream"]["schema"]
    assert asset_binary == {"type": "string", "format": "binary"}

    backup_content = openapi["paths"]["/api/v1/backups/{backup_id}/download"]["get"]["responses"]["200"]["content"]
    backup_binary = backup_content["application/zip"]["schema"]
    assert backup_binary == {"type": "string", "format": "binary"}

    revision_items = _response_schema(
        openapi,
        "get",
        "/api/v1/projects/{project_id}/revisions",
        "200",
    )["items"]
    assert _model_name(revision_items) == "RevisionOut"

    revision_snapshot = openapi["components"]["schemas"]["RevisionOut"]["properties"]["snapshot_json"]
    assert revision_snapshot["type"] == "object"
    assert _model_name(revision_snapshot["additionalProperties"]).endswith("response_types__JsonValue-Output")

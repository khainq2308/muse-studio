from __future__ import annotations
import json
from typing import Any, Literal
from typing_extensions import TypeAliasType
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.api.dependencies import get_studio_repository
from muse_orchestrator.application.workflow_service import WorkflowService
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import NodeExecutionState
from muse_orchestrator.services.workflow_engine import (
    MAX_WORKFLOW_EDGES,
    MAX_WORKFLOW_NODES,
)

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/workflows", tags=["M3 Workflow Canvas"]
)

MAX_GRAPH_JSON_CHARS = 1_000_000
MAX_UI_LAYOUT_JSON_CHARS = 256_000


def get_workflow_service(
    repository: StudioRepository = Depends(get_studio_repository),
) -> WorkflowService:
    return WorkflowService(repository)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _check_graph_size(nodes: Any, edges: Any, ui_layout: Any) -> None:
    if _json_size(nodes) + _json_size(edges) > MAX_GRAPH_JSON_CHARS:
        raise ValueError(
            f"nodes/edges exceed {MAX_GRAPH_JSON_CHARS} characters of JSON"
        )
    if _json_size(ui_layout) > MAX_UI_LAYOUT_JSON_CHARS:
        raise ValueError(
            f"ui_layout exceeds {MAX_UI_LAYOUT_JSON_CHARS} characters of JSON"
        )


class WorkflowCreateIn(BaseModel):
    name: str = Field(
        default="Script to Batch Film Pipeline", min_length=1, max_length=255
    )
    description: str = Field(default="M3 Default Typed DAG Workflow", max_length=4000)
    use_default_m3_template: bool = Field(default=True)
    nodes: list[dict[str, Any]] = Field(
        default_factory=list, max_length=MAX_WORKFLOW_NODES
    )
    edges: list[dict[str, Any]] = Field(
        default_factory=list, max_length=MAX_WORKFLOW_EDGES
    )
    ui_layout: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _bound_size(self) -> "WorkflowCreateIn":
        _check_graph_size(self.nodes, self.edges, self.ui_layout)
        return self


class WorkflowGraphUpdateIn(BaseModel):
    nodes: list[dict[str, Any]] = Field(..., max_length=MAX_WORKFLOW_NODES)
    edges: list[dict[str, Any]] = Field(..., max_length=MAX_WORKFLOW_EDGES)
    ui_layout: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _bound_size(self) -> "WorkflowGraphUpdateIn":
        _check_graph_size(self.nodes, self.edges, self.ui_layout)
        return self


class WorkflowLayoutOnlyIn(BaseModel):
    """Chỉ lưu vị trí kéo thả node trên Canvas, không tạo version logic mới và không xoá cache."""

    ui_layout: dict[str, Any]

    @model_validator(mode="after")
    def _bound_size(self) -> "WorkflowLayoutOnlyIn":
        _check_graph_size([], [], self.ui_layout)
        return self


JsonValue = TypeAliasType(
    "JsonValue",
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"],
)


class WorkflowNodeOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    node_id: str
    node_type: str
    config: dict[str, JsonValue] = Field(default_factory=dict)
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    outputs: dict[str, JsonValue] = Field(default_factory=dict)


class WorkflowEdgeOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    edge_id: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str


class WorkflowCreateOut(BaseModel):
    id: str
    project_id: str
    name: str
    current_version: int
    execution_order: list[str]
    cache_keys: dict[str, str]
    nodes: list[WorkflowNodeOut]
    edges: list[WorkflowEdgeOut]
    ui_layout: dict[str, JsonValue]


class WorkflowGraphUpdateOut(BaseModel):
    id: str
    current_version: int
    execution_order: list[str]
    cache_keys: dict[str, str]


class WorkflowLayoutSaveOut(BaseModel):
    workflow_id: str
    version_number: int
    ui_layout_saved: bool


class WorkflowDryRunOut(BaseModel):
    run_id: str
    run_mode: Literal["dry_run"]
    status: Literal["succeeded"]
    execution_mode: Literal["planning_only"]
    execution_plan_order: list[str]
    cache_keys: dict[str, str]


class WorkflowNodeExecutionOut(BaseModel):
    node_id: str
    node_type: str
    state: NodeExecutionState
    cache_key: str
    cache_hit: bool
    logs: list[str]
    output_refs: dict[str, JsonValue]


class WorkflowExecutionOut(BaseModel):
    run_id: str
    status: Literal["running", "succeeded", "waiting_approval", "failed"]
    execution_mode: Literal["simulation"]
    execution_plan_order: list[str]
    node_executions: list[WorkflowNodeExecutionOut]


class WorkflowApprovalNodeOut(BaseModel):
    node_id: str
    state: NodeExecutionState
    cache_hit: bool
    output_refs: dict[str, JsonValue]


class WorkflowApprovalOut(BaseModel):
    run_id: str
    status: Literal["succeeded", "waiting_approval"]
    execution_mode: Literal["simulation"]
    node_executions: list[WorkflowApprovalNodeOut]


WorkflowRunOut = WorkflowDryRunOut | WorkflowExecutionOut


class WorkflowRunRequestIn(BaseModel):
    run_mode: Literal["full", "dry_run", "from_here", "downstream"] = "full"
    start_node_id: str | None = None
    force_rerun: bool = False

    @model_validator(mode="after")
    def require_start_node_for_partial_runs(self) -> "WorkflowRunRequestIn":
        if self.run_mode in ("from_here", "downstream") and not self.start_node_id:
            raise ValueError(
                "start_node_id is required for from_here and downstream run modes."
            )
        return self


@router.get("", response_model=WorkflowCreateOut)
async def get_latest_project_workflow(
    project_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    actor: str = Depends(verify_single_admin),
) -> WorkflowCreateOut:
    return await service.get_latest_workflow(project_id)


@router.get("/{workflow_id}", response_model=WorkflowCreateOut)
async def get_project_workflow(
    project_id: str,
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    actor: str = Depends(verify_single_admin),
) -> WorkflowCreateOut:
    return await service.get_workflow(project_id, workflow_id)


@router.post("", response_model=WorkflowCreateOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    project_id: str,
    payload: WorkflowCreateIn,
    service: WorkflowService = Depends(get_workflow_service),
    actor: str = Depends(verify_single_admin),
) -> WorkflowCreateOut:
    return await service.create_workflow(project_id, payload.model_dump())


@router.put("/{workflow_id}/graph", response_model=WorkflowGraphUpdateOut)
async def update_workflow_graph(
    project_id: str,
    workflow_id: str,
    payload: WorkflowGraphUpdateIn,
    service: WorkflowService = Depends(get_workflow_service),
    actor: str = Depends(verify_single_admin),
) -> WorkflowGraphUpdateOut:
    return await service.update_graph(project_id, workflow_id, payload.model_dump())


@router.patch("/{workflow_id}/layout", response_model=WorkflowLayoutSaveOut)
async def autosave_canvas_layout(
    project_id: str,
    workflow_id: str,
    payload: WorkflowLayoutOnlyIn,
    service: WorkflowService = Depends(get_workflow_service),
    actor: str = Depends(verify_single_admin),
) -> WorkflowLayoutSaveOut:
    """Lưu vị trí toạ độ node trên Canvas mà không làm thay đổi Merkle Cache Key của logic."""
    return await service.save_layout(project_id, workflow_id, payload.ui_layout)


@router.post(
    "/{workflow_id}/runs",
    response_model=WorkflowRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def execute_workflow(
    project_id: str,
    workflow_id: str,
    payload: WorkflowRunRequestIn,
    service: WorkflowService = Depends(get_workflow_service),
    actor: str = Depends(verify_single_admin),
) -> WorkflowRunOut:
    return await service.execute(project_id, workflow_id, payload.model_dump())


@router.post(
    "/{workflow_id}/runs/{run_id}/nodes/{node_id}/approve",
    response_model=WorkflowApprovalOut,
)
async def approve_manual_gate_and_resume(
    project_id: str,
    workflow_id: str,
    run_id: str,
    node_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    actor: str = Depends(verify_single_admin),
) -> WorkflowApprovalOut:
    """Duyệt cổng ManualApproval và chạy tiếp tới cổng ManualApproval kế tiếp (nếu có)."""
    return await service.approve_gate(project_id, workflow_id, run_id, node_id, actor)

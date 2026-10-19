from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.api.dependencies import get_studio_repository
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import (
    Project, WorkflowDefinition, WorkflowVersion, WorkflowRun,
    NodeExecution, NodeExecutionState, WorkflowCacheEntry
)
from muse_orchestrator.services.workflow_engine import (
    WorkflowEngine, WorkflowValidationError
)

router = APIRouter(prefix="/api/v1/projects/{project_id}/workflows", tags=["M3 Workflow Canvas"])


class WorkflowCreateIn(BaseModel):
    name: str = Field(default="Script to Batch Film Pipeline")
    description: str = Field(default="M3 Default Typed DAG Workflow")
    use_default_m3_template: bool = Field(default=True)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    ui_layout: dict[str, Any] = Field(default_factory=dict)


class WorkflowGraphUpdateIn(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    ui_layout: dict[str, Any] = Field(default_factory=dict)


class WorkflowLayoutOnlyIn(BaseModel):
    """Chỉ lưu vị trí kéo thả node trên Canvas, không tạo version logic mới và không xoá cache."""
    ui_layout: dict[str, Any]


class WorkflowRunRequestIn(BaseModel):
    run_mode: str = Field(default="full", pattern=r"^(full|dry_run|from_here|downstream)$")
    start_node_id: str | None = None
    force_rerun: bool = False


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    project_id: str,
    payload: WorkflowCreateIn,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    proj = await repository.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

    if payload.use_default_m3_template and not payload.nodes:
        nodes_json, edges_json, ui_layout = WorkflowEngine.build_m3_default_template()
    else:
        nodes_json, edges_json, ui_layout = payload.nodes, payload.edges, payload.ui_layout

    try:
        nodes_def, edges_def = WorkflowEngine.parse_graph_json(nodes_json, edges_json)
        execution_order, cache_keys = WorkflowEngine.validate_and_plan(nodes_def, edges_def)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    wf_id = f"wf_{uuid.uuid4().hex[:10]}"
    wf = WorkflowDefinition(
        id=wf_id,
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        current_version=1,
    )
    ver = WorkflowVersion(
        id=f"wfv_{uuid.uuid4().hex[:10]}",
        workflow_id=wf_id,
        version_number=1,
        schema_version="1.0",
        nodes_json=nodes_json,
        edges_json=edges_json,
        ui_layout_json=ui_layout,
    )
    repository.stage_workflow_entities([wf, ver])
    await repository.commit_workflow_changes()

    return {
        "id": wf.id,
        "project_id": project_id,
        "name": wf.name,
        "current_version": 1,
        "execution_order": execution_order,
        "cache_keys": cache_keys,
        "nodes": nodes_json,
        "edges": edges_json,
        "ui_layout": ui_layout,
    }


@router.put("/{workflow_id}/graph")
async def update_workflow_graph(
    project_id: str,
    workflow_id: str,
    payload: WorkflowGraphUpdateIn,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    wf = await repository.get_workflow_definition(workflow_id)
    if not wf or wf.project_id != project_id:
        raise HTTPException(status_code=404, detail="Workflow không tồn tại.")

    try:
        nodes_def, edges_def = WorkflowEngine.parse_graph_json(payload.nodes, payload.edges)
        execution_order, cache_keys = WorkflowEngine.validate_and_plan(nodes_def, edges_def)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    wf.current_version += 1
    ver = WorkflowVersion(
        id=f"wfv_{uuid.uuid4().hex[:10]}",
        workflow_id=workflow_id,
        version_number=wf.current_version,
        schema_version="1.0",
        nodes_json=payload.nodes,
        edges_json=payload.edges,
        ui_layout_json=payload.ui_layout,
    )
    repository.stage_workflow_entity(ver)
    await repository.commit_workflow_changes()

    return {
        "id": wf.id,
        "current_version": wf.current_version,
        "execution_order": execution_order,
        "cache_keys": cache_keys,
    }


@router.patch("/{workflow_id}/layout")
async def autosave_canvas_layout(
    project_id: str,
    workflow_id: str,
    payload: WorkflowLayoutOnlyIn,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """Lưu vị trí toạ độ node trên Canvas mà không làm thay đổi Merkle Cache Key của logic."""
    wf = await repository.get_workflow_definition(workflow_id)
    if not wf or wf.project_id != project_id:
        raise HTTPException(status_code=404, detail="Workflow không tồn tại.")

    ver = await repository.get_workflow_version(workflow_id, wf.current_version)
    ver.ui_layout_json = payload.ui_layout
    await repository.commit_workflow_changes()
    return {"workflow_id": workflow_id, "version_number": ver.version_number, "ui_layout_saved": True}


@router.post("/{workflow_id}/runs", status_code=status.HTTP_201_CREATED)
async def execute_workflow(
    project_id: str,
    workflow_id: str,
    payload: WorkflowRunRequestIn,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    wf = await repository.get_workflow_definition(workflow_id)
    if not wf or wf.project_id != project_id:
        raise HTTPException(status_code=404, detail="Workflow không tồn tại.")

    ver = await repository.get_workflow_version(workflow_id, wf.current_version)

    nodes_def, edges_def = WorkflowEngine.parse_graph_json(ver.nodes_json, ver.edges_json)
    full_order, cache_keys = WorkflowEngine.validate_and_plan(nodes_def, edges_def)
    node_map = {n.node_id: n for n in nodes_def}

    if payload.run_mode in ("from_here", "downstream") and payload.start_node_id:
        if payload.start_node_id not in node_map:
            raise HTTPException(status_code=400, detail=f"start_node_id '{payload.start_node_id}' không tồn tại.")
        plan_order = WorkflowEngine.get_downstream_subplan(payload.start_node_id, full_order, edges_def)
    else:
        plan_order = full_order

    run_id = f"run_{uuid.uuid4().hex[:10]}"
    wf_run = WorkflowRun(
        id=run_id,
        project_id=project_id,
        workflow_id=workflow_id,
        workflow_version=ver.version_number,
        run_mode=payload.run_mode,
        status="running",
        execution_plan_order=plan_order,
    )
    repository.stage_workflow_entity(wf_run)

    # Nếu là dry_run: chỉ trả về kế hoạch chạy và kiểm tra cache mà không thực thi
    if payload.run_mode == "dry_run":
        wf_run.status = "succeeded"
        wf_run.finished_at = datetime.now(timezone.utc)
        await repository.commit_workflow_changes()
        return {
            "run_id": run_id,
            "run_mode": "dry_run",
            "status": "succeeded",
            "execution_mode": "planning_only",
            "execution_plan_order": plan_order,
            "cache_keys": {nid: cache_keys[nid] for nid in plan_order},
        }

    node_records: list[NodeExecution] = []
    paused_at_approval = False

    for nid in plan_order:
        nd = node_map[nid]
        ckey = cache_keys[nid]
        now = datetime.now(timezone.utc)

        if paused_at_approval:
            # Các node nằm sau cổng ManualApproval đang chờ duyệt sẽ ở trạng thái blocked
            n_exec = NodeExecution(
                id=f"nexec_{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                node_id=nid,
                node_type=nd.node_type,
                state=NodeExecutionState.BLOCKED,
                cache_key=ckey,
                cache_hit=False,
                input_snapshot=nd.config,
                output_refs={},
                logs=["Blocked waiting for upstream ManualApproval gate."],
            )
            repository.stage_workflow_entity(n_exec)
            node_records.append(n_exec)
            continue

        # Kiểm tra Cache Entry nếu không ép chạy lại (force_rerun)
        cached = None if payload.force_rerun else await repository.get_workflow_cache_entry(ckey)
        # The original M3 prototype stored placeholder node refs as "verified". Do not
        # trust legacy cache rows without an explicit execution provenance marker.
        if cached is not None and cached.output_refs.get("execution_kind") not in {
            "simulation", "approval", "deterministic", "provider_artifact"
        }:
            cached = None
        if cached is not None:
            cached_kind = cached.output_refs.get("execution_kind")
            cache_label = {
                "simulation": "simulated",
                "approval": "approval",
                "deterministic": "deterministic",
                "provider_artifact": "provider artifact",
            }.get(cached_kind, "cached")
            n_exec = NodeExecution(
                id=f"nexec_{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                node_id=nid,
                node_type=nd.node_type,
                state=NodeExecutionState.SKIPPED,
                cache_key=ckey,
                cache_hit=True,
                input_snapshot=nd.config,
                output_refs=cached.output_refs,
                logs=[f"Cache hit ({ckey[:10]}): reused {cache_label} output."],
                started_at=now,
                ended_at=now,
            )
            repository.stage_workflow_entity(n_exec)
            node_records.append(n_exec)
            continue

        # Nếu gặp cổng ManualApproval chưa được cache -> Dừng chờ người dùng duyệt
        if nd.node_type == "ManualApproval":
            paused_at_approval = True
            wf_run.status = "waiting_approval"
            n_exec = NodeExecution(
                id=f"nexec_{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                node_id=nid,
                node_type=nd.node_type,
                state=NodeExecutionState.WAITING_APPROVAL,
                cache_key=ckey,
                cache_hit=False,
                input_snapshot=nd.config,
                output_refs={},
                logs=["Paused at ManualApproval gate. Waiting for operator approval."],
                started_at=now,
            )
            repository.stage_workflow_entity(n_exec)
            node_records.append(n_exec)
            continue

        # M3 does not yet register production executors for arbitrary node types.
        # Keep the planning/cache state machine testable, but never label placeholders as real outputs.
        out_ref = {
            "node_id": nid,
            "node_type": nd.node_type,
            "cache_key": ckey[:12],
            "status": "simulated",
            "execution_kind": "simulation",
            "placeholder": True,
            "reason": "No production node executor is registered for this node type.",
        }
        n_exec = NodeExecution(
            id=f"nexec_{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            node_id=nid,
            node_type=nd.node_type,
            state=NodeExecutionState.SUCCEEDED,
            cache_key=ckey,
            cache_hit=False,
            input_snapshot=nd.config,
            output_refs=out_ref,
            logs=[f"Simulated {nd.node_type}; no production executor or media artifact was created."],
            started_at=now,
            ended_at=now,
        )
        repository.stage_workflow_entity(n_exec)
        node_records.append(n_exec)

        existing_cache = await repository.get_workflow_cache_entry(ckey)
        if not existing_cache:
            repository.stage_workflow_entity(WorkflowCacheEntry(
                cache_key=ckey,
                project_id=project_id,
                node_type=nd.node_type,
                output_refs=out_ref,
            ))

    if not paused_at_approval:
        wf_run.status = "succeeded"
        wf_run.finished_at = datetime.now(timezone.utc)

    await repository.commit_workflow_changes()

    return {
        "run_id": wf_run.id,
        "status": wf_run.status,
        "execution_mode": "simulation",
        "execution_plan_order": plan_order,
        "node_executions": [
            {
                "node_id": nr.node_id,
                "node_type": nr.node_type,
                "state": nr.state.value,
                "cache_key": nr.cache_key,
                "cache_hit": nr.cache_hit,
                "logs": nr.logs,
                "output_refs": nr.output_refs,
            }
            for nr in node_records
        ],
    }


@router.post("/{workflow_id}/runs/{run_id}/nodes/{node_id}/approve")
async def approve_manual_gate_and_resume(
    project_id: str,
    workflow_id: str,
    run_id: str,
    node_id: str,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    """Duyệt cổng ManualApproval và tự động chạy tiếp các node phía sau đang bị blocked."""
    wf_run = await repository.get_workflow_run(run_id)
    if not wf_run or wf_run.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="Workflow run không tồn tại.")

    node_execs = await repository.list_node_executions_for_run(run_id)
    exec_by_nid = {ne.node_id: ne for ne in node_execs}

    target = exec_by_nid.get(node_id)
    if not target or target.state != NodeExecutionState.WAITING_APPROVAL:
        raise HTTPException(status_code=400, detail="Node này không ở trạng thái waiting_approval.")

    now = datetime.now(timezone.utc)
    target.state = NodeExecutionState.SUCCEEDED
    target.ended_at = now
    target.output_refs = {
        "approved_by": actor,
        "approved_at": now.isoformat(),
        "status": "approved",
        "execution_kind": "approval",
    }
    target.logs = list(target.logs or []) + [f"Approved by {actor}."]

    if not await repository.get_workflow_cache_entry(target.cache_key):
        repository.stage_workflow_entity(WorkflowCacheEntry(
            cache_key=target.cache_key,
            project_id=project_id,
            node_type=target.node_type,
            output_refs=target.output_refs,
        ))

    # Mở khoá và chạy tiếp các node bị blocked phía sau theo thứ tự kế hoạch
    for nid in wf_run.execution_plan_order:
        ne = exec_by_nid[nid]
        if ne.state == NodeExecutionState.BLOCKED:
            ne.state = NodeExecutionState.SUCCEEDED
            ne.started_at = now
            ne.ended_at = now
            ne.output_refs = {
                "node_id": nid,
                "status": "simulated",
                "execution_kind": "simulation",
                "placeholder": True,
                "reason": "No production node executor is registered for this node type.",
            }
            ne.logs = list(ne.logs or []) + [
                "Resumed state-machine simulation after ManualApproval; no media was generated."
            ]
            if not await repository.get_workflow_cache_entry(ne.cache_key):
                repository.stage_workflow_entity(WorkflowCacheEntry(
                    cache_key=ne.cache_key,
                    project_id=project_id,
                    node_type=ne.node_type,
                    output_refs=ne.output_refs,
                ))

    wf_run.status = "succeeded"
    wf_run.finished_at = now
    await repository.commit_workflow_changes()

    return {
        "run_id": wf_run.id,
        "status": wf_run.status,
        "execution_mode": "simulation",
        "node_executions": [
            {
                "node_id": exec_by_nid[nid].node_id,
                "state": exec_by_nid[nid].state.value,
                "cache_hit": exec_by_nid[nid].cache_hit,
                "output_refs": exec_by_nid[nid].output_refs,
            }
            for nid in wf_run.execution_plan_order
        ],
    }

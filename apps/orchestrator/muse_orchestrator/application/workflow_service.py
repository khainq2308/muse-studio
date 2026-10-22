"""M3 workflow canvas use cases: graph versions, (simulated) runs and approval gates."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import set_committed_value

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import (
    NodeExecution,
    NodeExecutionState,
    WorkflowCacheEntry,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowVersion,
)
from muse_orchestrator.services.workflow_engine import WorkflowEngine, WorkflowValidationError

_TRUSTED_CACHE_KINDS = {"simulation", "approval", "deterministic", "provider_artifact"}
_CACHE_LABELS = {
    "simulation": "simulated",
    "approval": "approval",
    "deterministic": "deterministic",
    "provider_artifact": "provider artifact",
}
_SIMULATION_REASON = "No production node executor is registered for this node type."


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


class WorkflowService:
    def __init__(self, repository: StudioRepository) -> None:
        self.repository = repository

    async def _require_workflow(self, project_id: str, workflow_id: str) -> WorkflowDefinition:
        wf = await self.repository.get_workflow_definition(workflow_id)
        if not wf or wf.project_id != project_id:
            raise ApplicationError(404, "Workflow không tồn tại.")
        return wf

    async def external_input_hashes(self, project_id: str) -> dict[str, str]:
        """Content hashes of project data read by ScriptInput / StoryBibleBuild nodes."""
        revisions = await self.repository.list_script_revisions(project_id)
        latest = max(revisions, key=lambda rev: rev.version_number, default=None)
        script_hash = (
            hashlib.sha256(latest.raw_content.encode("utf-8")).hexdigest() if latest else ""
        )
        bible = sorted(
            (
                {
                    "code_name": entry.code_name,
                    "entry_type": entry.entry_type,
                    "display_name": entry.display_name,
                    "immutable_traits": entry.immutable_traits,
                    "mutable_attributes": entry.mutable_attributes,
                    "reference_asset_ids": entry.reference_asset_ids,
                    "requires_reference_lock": entry.requires_reference_lock,
                }
                for entry in await self.repository.list_story_bible_entries(project_id)
            ),
            key=lambda item: (item["entry_type"], item["code_name"]),
        )
        return {
            "ScriptInput": script_hash,
            "StoryBibleBuild": _sha256_json({"script": script_hash, "bible": bible}),
        }

    async def _plan(
        self, project_id: str, nodes_json: list[dict[str, Any]], edges_json: list[dict[str, Any]]
    ) -> tuple[list[Any], list[Any], list[str], dict[str, str]]:
        try:
            nodes_def, edges_def = WorkflowEngine.parse_graph_json(nodes_json, edges_json)
            order, cache_keys = WorkflowEngine.validate_and_plan(
                nodes_def,
                edges_def,
                cache_scope=project_id,
                external_input_hashes=await self.external_input_hashes(project_id),
            )
        except WorkflowValidationError as exc:
            raise ApplicationError(422, str(exc), category="validation") from exc
        return nodes_def, edges_def, order, cache_keys

    async def create_workflow(self, project_id: str, values: dict[str, Any]) -> dict[str, Any]:
        if await self.repository.get_project(project_id) is None:
            raise ApplicationError(404, "Project không tồn tại.")
        if values["use_default_m3_template"] and not values["nodes"]:
            nodes_json, edges_json, ui_layout = WorkflowEngine.build_m3_default_template()
        else:
            nodes_json, edges_json, ui_layout = values["nodes"], values["edges"], values["ui_layout"]
        _, _, order, cache_keys = await self._plan(project_id, nodes_json, edges_json)

        wf = WorkflowDefinition(
            id=f"wf_{uuid.uuid4().hex[:10]}",
            project_id=project_id,
            name=values["name"],
            description=values["description"],
            current_version=1,
        )
        ver = WorkflowVersion(
            id=f"wfv_{uuid.uuid4().hex[:10]}",
            workflow_id=wf.id,
            version_number=1,
            schema_version="1.0",
            nodes_json=nodes_json,
            edges_json=edges_json,
            ui_layout_json=ui_layout,
        )
        # No relationship(): persist the workflow before its FK child version.
        self.repository.stage_workflow_entity(wf)
        await self.repository.flush_changes()
        self.repository.stage_workflow_entity(ver)
        await self.repository.commit_workflow_changes()
        return {
            "id": wf.id,
            "project_id": project_id,
            "name": wf.name,
            "current_version": 1,
            "execution_order": order,
            "cache_keys": cache_keys,
            "nodes": nodes_json,
            "edges": edges_json,
            "ui_layout": ui_layout,
        }

    async def update_graph(
        self, project_id: str, workflow_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        wf = await self._require_workflow(project_id, workflow_id)
        _, _, order, cache_keys = await self._plan(project_id, values["nodes"], values["edges"])
        session = self.repository.session
        try:
            # Atomic allocation: concurrent saves serialise on the row update and each
            # receives a distinct version number (no read-modify-write race).
            new_version = (
                await session.execute(
                    update(WorkflowDefinition)
                    .where(WorkflowDefinition.id == workflow_id)
                    .values(current_version=WorkflowDefinition.current_version + 1)
                    .returning(WorkflowDefinition.current_version)
                    .execution_options(synchronize_session=False)
                )
            ).scalar_one()
            set_committed_value(wf, "current_version", new_version)
            self.repository.stage_workflow_entity(
                WorkflowVersion(
                    id=f"wfv_{uuid.uuid4().hex[:10]}",
                    workflow_id=workflow_id,
                    version_number=new_version,
                    schema_version="1.0",
                    nodes_json=values["nodes"],
                    edges_json=values["edges"],
                    ui_layout_json=values["ui_layout"],
                )
            )
            await self.repository.commit_workflow_changes()
        except IntegrityError as exc:
            await session.rollback()
            raise ApplicationError(
                409,
                "The workflow graph was saved concurrently; reload and retry.",
                category="conflict",
                retryable=True,
            ) from exc
        return {
            "id": workflow_id,
            "current_version": new_version,
            "execution_order": order,
            "cache_keys": cache_keys,
        }

    async def save_layout(
        self, project_id: str, workflow_id: str, ui_layout: dict[str, Any]
    ) -> dict[str, Any]:
        wf = await self._require_workflow(project_id, workflow_id)
        ver = await self.repository.get_workflow_version(workflow_id, wf.current_version)
        if ver is None:
            raise ApplicationError(404, "Workflow version không tồn tại.")
        ver.ui_layout_json = ui_layout
        await self.repository.commit_workflow_changes()
        return {"workflow_id": workflow_id, "version_number": ver.version_number, "ui_layout_saved": True}

    async def _gate_was_approved(self, workflow_id: str, version_number: int, node_id: str) -> bool:
        result = await self.repository.session.execute(
            select(NodeExecution.id)
            .join(WorkflowRun, NodeExecution.run_id == WorkflowRun.id)
            .where(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.workflow_version == version_number,
                NodeExecution.node_id == node_id,
                NodeExecution.node_type == "ManualApproval",
                NodeExecution.state == NodeExecutionState.SUCCEEDED,
            )
            .limit(1)
        )
        return result.first() is not None

    async def execute(self, project_id: str, workflow_id: str, values: dict[str, Any]) -> dict[str, Any]:
        wf = await self._require_workflow(project_id, workflow_id)
        ver = await self.repository.get_workflow_version(workflow_id, wf.current_version)
        if ver is None:
            raise ApplicationError(404, "Workflow version không tồn tại.")

        nodes_def, edges_def, full_order, cache_keys = await self._plan(
            project_id, ver.nodes_json, ver.edges_json
        )
        node_map = {n.node_id: n for n in nodes_def}
        run_mode = values["run_mode"]
        start_node_id = values.get("start_node_id")

        if run_mode in ("from_here", "downstream") and start_node_id:
            if start_node_id not in node_map:
                raise ApplicationError(400, f"start_node_id '{start_node_id}' không tồn tại.")
            # A partial run skips upstream nodes, so it must not bypass their approval
            # gates: each upstream ManualApproval needs a prior approval on this version.
            missing = [
                nid
                for nid in sorted(WorkflowEngine.get_ancestors(start_node_id, edges_def))
                if node_map[nid].node_type == "ManualApproval"
                and not await self._gate_was_approved(workflow_id, ver.version_number, nid)
            ]
            if missing:
                raise ApplicationError(
                    409,
                    "Upstream ManualApproval gate(s) have not been approved for workflow "
                    f"version {ver.version_number}: {', '.join(missing)}. Run the workflow "
                    "and approve them first.",
                )
            plan_order = WorkflowEngine.get_downstream_subplan(start_node_id, full_order, edges_def)
        else:
            plan_order = full_order

        run_id = f"run_{uuid.uuid4().hex[:10]}"
        wf_run = WorkflowRun(
            id=run_id,
            project_id=project_id,
            workflow_id=workflow_id,
            workflow_version=ver.version_number,
            run_mode=run_mode,
            status="running",
            execution_plan_order=plan_order,
        )
        self.repository.stage_workflow_entity(wf_run)
        # Node executions reference the run by FK without an ORM relationship, so the unit
        # of work cannot order the INSERTs; persist the run row first.
        await self.repository.flush_changes()

        if run_mode == "dry_run":
            wf_run.status = "succeeded"
            wf_run.finished_at = datetime.now(timezone.utc)
            await self.repository.commit_workflow_changes()
            return {
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "succeeded",
                "execution_mode": "planning_only",
                "execution_plan_order": plan_order,
                "cache_keys": {nid: cache_keys[nid] for nid in plan_order},
            }

        node_records: list[NodeExecution] = []
        staged_cache_keys: set[str] = set()
        paused_at_approval = False

        def record(nid: str, **fields: Any) -> None:
            nd = node_map[nid]
            n_exec = NodeExecution(
                id=f"nexec_{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                node_id=nid,
                node_type=nd.node_type,
                cache_key=cache_keys[nid],
                input_snapshot=nd.config,
                **fields,
            )
            self.repository.stage_workflow_entity(n_exec)
            node_records.append(n_exec)

        for nid in plan_order:
            nd = node_map[nid]
            ckey = cache_keys[nid]
            now = datetime.now(timezone.utc)

            if paused_at_approval:
                record(
                    nid,
                    state=NodeExecutionState.BLOCKED,
                    cache_hit=False,
                    output_refs={},
                    logs=["Blocked waiting for upstream ManualApproval gate."],
                )
                continue

            # ManualApproval gates are never served from cache: every run requires a fresh
            # operator decision. Cache rows are only reused when they belong to this project
            # and carry an explicit execution provenance marker.
            cached = (
                None
                if values.get("force_rerun") or nd.node_type == "ManualApproval"
                else await self.repository.get_workflow_cache_entry(ckey)
            )
            if cached is not None and (
                cached.project_id != project_id
                or cached.output_refs.get("execution_kind") not in _TRUSTED_CACHE_KINDS
            ):
                cached = None
            if cached is not None:
                label = _CACHE_LABELS.get(cached.output_refs.get("execution_kind"), "cached")
                record(
                    nid,
                    state=NodeExecutionState.SKIPPED,
                    cache_hit=True,
                    output_refs=cached.output_refs,
                    logs=[f"Cache hit ({ckey[:10]}): reused {label} output."],
                    started_at=now,
                    ended_at=now,
                )
                continue

            if nd.node_type == "ManualApproval":
                paused_at_approval = True
                wf_run.status = "waiting_approval"
                record(
                    nid,
                    state=NodeExecutionState.WAITING_APPROVAL,
                    cache_hit=False,
                    output_refs={},
                    logs=["Paused at ManualApproval gate. Waiting for operator approval."],
                    started_at=now,
                )
                continue

            # M3 does not yet register production executors for arbitrary node types; never
            # label placeholders as real outputs.
            out_ref = {
                "node_id": nid,
                "node_type": nd.node_type,
                "cache_key": ckey[:12],
                "status": "simulated",
                "execution_kind": "simulation",
                "placeholder": True,
                "reason": _SIMULATION_REASON,
            }
            record(
                nid,
                state=NodeExecutionState.SUCCEEDED,
                cache_hit=False,
                output_refs=out_ref,
                logs=[f"Simulated {nd.node_type}; no production executor or media artifact was created."],
                started_at=now,
                ended_at=now,
            )
            await self._stage_cache_entry(project_id, ckey, nd.node_type, out_ref, staged_cache_keys)

        if not paused_at_approval:
            wf_run.status = "succeeded"
            wf_run.finished_at = datetime.now(timezone.utc)
        await self.repository.commit_workflow_changes()

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

    async def _stage_cache_entry(
        self,
        project_id: str,
        cache_key: str,
        node_type: str,
        output_refs: dict[str, Any],
        staged: set[str],
    ) -> None:
        # Two nodes may legitimately share a key (identical deterministic subgraphs); stage
        # the row once per key so the commit never violates the primary key.
        if cache_key in staged:
            return
        staged.add(cache_key)
        if await self.repository.get_workflow_cache_entry(cache_key) is None:
            self.repository.stage_workflow_entity(
                WorkflowCacheEntry(
                    cache_key=cache_key,
                    project_id=project_id,
                    node_type=node_type,
                    output_refs=output_refs,
                )
            )

    async def approve_gate(
        self, project_id: str, workflow_id: str, run_id: str, node_id: str, actor: str
    ) -> dict[str, Any]:
        """Approve a ManualApproval gate and resume until the next gate (if any)."""
        await self._require_workflow(project_id, workflow_id)
        wf_run = await self.repository.get_workflow_run(run_id)
        if not wf_run or wf_run.workflow_id != workflow_id or wf_run.project_id != project_id:
            raise ApplicationError(404, "Workflow run không tồn tại.")

        exec_by_nid = {ne.node_id: ne for ne in await self.repository.list_node_executions_for_run(run_id)}
        target = exec_by_nid.get(node_id)
        if not target or target.state != NodeExecutionState.WAITING_APPROVAL:
            raise ApplicationError(400, "Node này không ở trạng thái waiting_approval.")

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
        # Approvals are deliberately NOT written to the workflow cache: a later run must
        # stop at the gate again instead of inheriting this decision.

        staged_cache_keys: set[str] = set()
        paused_at_next_gate = False
        for nid in wf_run.execution_plan_order:
            ne = exec_by_nid.get(nid)
            if ne is None or ne.state != NodeExecutionState.BLOCKED or paused_at_next_gate:
                continue
            if ne.node_type == "ManualApproval":
                paused_at_next_gate = True
                ne.state = NodeExecutionState.WAITING_APPROVAL
                ne.started_at = now
                ne.logs = list(ne.logs or []) + ["Paused at ManualApproval gate. Waiting for operator approval."]
                continue
            ne.state = NodeExecutionState.SUCCEEDED
            ne.started_at = now
            ne.ended_at = now
            ne.output_refs = {
                "node_id": nid,
                "status": "simulated",
                "execution_kind": "simulation",
                "placeholder": True,
                "reason": _SIMULATION_REASON,
            }
            ne.logs = list(ne.logs or []) + [
                "Resumed state-machine simulation after ManualApproval; no media was generated."
            ]
            await self._stage_cache_entry(project_id, ne.cache_key, ne.node_type, ne.output_refs, staged_cache_keys)

        if paused_at_next_gate:
            wf_run.status = "waiting_approval"
        else:
            wf_run.status = "succeeded"
            wf_run.finished_at = now
        await self.repository.commit_workflow_changes()

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
                if nid in exec_by_nid
            ],
        }

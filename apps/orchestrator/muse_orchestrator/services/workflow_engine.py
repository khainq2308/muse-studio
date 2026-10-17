from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PortDef:
    name: str
    port_type: str


@dataclass
class WorkflowNodeDef:
    node_id: str
    node_type: str
    config: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, PortDef] = field(default_factory=dict)
    outputs: dict[str, PortDef] = field(default_factory=dict)


@dataclass
class WorkflowEdgeDef:
    edge_id: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str


class WorkflowValidationError(Exception):
    pass


class WorkflowEngine:
    @staticmethod
    def validate_and_plan(
        nodes: list[WorkflowNodeDef],
        edges: list[WorkflowEdgeDef],
    ) -> tuple[list[str], dict[str, str]]:
        node_map = {n.node_id: n for n in nodes}
        adj: dict[str, list[str]] = {n.node_id: [] for n in nodes}
        in_degree: dict[str, int] = {n.node_id: 0 for n in nodes}
        incoming_edges: dict[str, list[WorkflowEdgeDef]] = {n.node_id: [] for n in nodes}

        for edge in edges:
            if edge.source_node_id not in node_map:
                raise WorkflowValidationError(f"Source node '{edge.source_node_id}' not found.")
            if edge.target_node_id not in node_map:
                raise WorkflowValidationError(f"Target node '{edge.target_node_id}' not found.")

            src_node = node_map[edge.source_node_id]
            tgt_node = node_map[edge.target_node_id]

            if edge.source_port not in src_node.outputs:
                raise WorkflowValidationError(
                    f"Port '{edge.source_port}' không tồn tại trong outputs của node '{edge.source_node_id}'."
                )
            if edge.target_port not in tgt_node.inputs:
                raise WorkflowValidationError(
                    f"Port '{edge.target_port}' không tồn tại trong inputs của node '{edge.target_node_id}'."
                )

            src_type = src_node.outputs[edge.source_port].port_type
            tgt_type = tgt_node.inputs[edge.target_port].port_type
            if src_type != tgt_type:
                raise WorkflowValidationError(f"Type mismatch: {src_type} != {tgt_type}")

            adj[edge.source_node_id].append(edge.target_node_id)
            in_degree[edge.target_node_id] += 1
            incoming_edges[edge.target_node_id].append(edge)

        queue = deque(sorted([nid for nid, deg in in_degree.items() if deg == 0]))
        execution_order: list[str] = []
        while queue:
            current = queue.popleft()
            execution_order.append(current)
            for neighbor in sorted(adj[current]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(execution_order) != len(nodes):
            raise WorkflowValidationError("Phát hiện chu trình (cycle) trong Workflow Graph!")

        cache_keys: dict[str, str] = {}
        for nid in execution_order:
            node = node_map[nid]
            parent_hashes = [cache_keys[e.source_node_id] for e in incoming_edges[nid]]
            payload = json.dumps({"t": node.node_type, "c": node.config, "p": parent_hashes}, sort_keys=True)
            cache_keys[nid] = hashlib.sha256(payload.encode()).hexdigest()

        return execution_order, cache_keys

from __future__ import annotations
import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

VALID_PORT_TYPES = {
    "Text", "Script", "StoryBible", "Scene[]", "Shot[]", "Prompt",
    "ImageAsset", "VideoAsset", "TaskRef", "TimelineManifest", "Boolean", "Error"
}

# Danh mục chuẩn 22 loại Node và Cổng (Ports) mặc định theo Mục 3.6 PRD
NODE_CATALOG: dict[str, dict[str, dict[str, str]]] = {
    "ScriptInput": {"inputs": {}, "outputs": {"script": "Script"}},
    "ScriptAnalyze": {"inputs": {"script": "Script"}, "outputs": {"script": "Script"}},
    "ManualApproval": {"inputs": {"in": "Prompt"}, "outputs": {"out": "Prompt"}},
    "StoryBibleBuild": {"inputs": {"script": "Script"}, "outputs": {"bible": "StoryBible"}},
    "SceneBreakdown": {"inputs": {"bible": "StoryBible"}, "outputs": {"scenes": "Scene[]"}},
    "ShotBreakdown": {"inputs": {"scenes": "Scene[]"}, "outputs": {"shots": "Shot[]"}},
    "PromptTemplate": {"inputs": {"shots": "Shot[]"}, "outputs": {"prompts": "Prompt"}},
    "PromptRefine": {"inputs": {"prompts": "Prompt"}, "outputs": {"prompts": "Prompt"}},
    "ReferenceImageInput": {"inputs": {}, "outputs": {"image": "ImageAsset"}},
    "ImageGenerate": {"inputs": {"prompt": "Prompt"}, "outputs": {"image": "ImageAsset"}},
    "VideoGenerate": {"inputs": {"prompt": "Prompt"}, "outputs": {"task": "TaskRef"}},
    "Batch/FanOut": {"inputs": {"shots": "Shot[]"}, "outputs": {"shots": "Shot[]"}},
    "Merge/Sequence": {"inputs": {"videos": "VideoAsset"}, "outputs": {"timeline": "TimelineManifest"}},
    "Condition/Filter": {"inputs": {"in": "Prompt"}, "outputs": {"out": "Prompt"}},
    "WaitForTask": {"inputs": {"task": "TaskRef"}, "outputs": {"video": "VideoAsset"}},
    "QualityGate": {"inputs": {"video": "VideoAsset"}, "outputs": {"video": "VideoAsset"}},
    "RetryPolicy": {"inputs": {"task": "TaskRef"}, "outputs": {"task": "TaskRef"}},
    "AssetSave": {"inputs": {"video": "VideoAsset"}, "outputs": {"video": "VideoAsset"}},
    "TimelineAssemble": {"inputs": {"video": "VideoAsset"}, "outputs": {"timeline": "TimelineManifest"}},
    "VideoExport": {"inputs": {"timeline": "TimelineManifest"}, "outputs": {"video": "VideoAsset"}},
    "Webhook/Notify": {"inputs": {"video": "VideoAsset"}, "outputs": {"done": "Boolean"}},
    "Note/Group": {"inputs": {}, "outputs": {}},
}

# Node types whose output is not a pure function of (type, config, parent outputs): they
# talk to providers/operators or read external inputs. Their cache keys include node_id
# so two identical-looking nodes never share (or collide on) one cache entry.
NODE_IDENTITY_KEYED_TYPES = frozenset(
    {
        "ScriptInput",
        "ReferenceImageInput",
        "PromptRefine",
        "ImageGenerate",
        "VideoGenerate",
        "WaitForTask",
        "RetryPolicy",
        "QualityGate",
        "VideoExport",
        "Webhook/Notify",
        "ManualApproval",
    }
)
# Node types that read project data (script / story bible) outside the graph. The caller
# supplies a content hash of that data so edits invalidate their cache entries.
EXTERNAL_INPUT_NODE_TYPES = frozenset({"ScriptInput", "StoryBibleBuild"})

MAX_WORKFLOW_NODES = 200
MAX_WORKFLOW_EDGES = 1_000


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
    """
    Bộ kiểm định DAG, kiểm tra kiểu dữ liệu Port, phát hiện chu trình (Cycle),
    tính toán Merkle Cache Key và tìm tập hợp các node downstream khi chạy từng phần.
    """

    @staticmethod
    def build_m3_default_template() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """
        Tạo mẫu Workflow chuẩn của M3 (Mục 10 PRD):
        ScriptInput -> ScriptAnalyze -> StoryBibleBuild -> SceneBreakdown -> ShotBreakdown -> PromptTemplate -> ManualApproval
        """
        nodes = [
            {
                "node_id": "n_script_in",
                "node_type": "ScriptInput",
                "config": {"source": "project_latest_script"},
                "inputs": {},
                "outputs": {"script": "Script"},
            },
            {
                "node_id": "n_analyze",
                "node_type": "ScriptAnalyze",
                "config": {"mode": "museflow_deterministic"},
                "inputs": {"script": "Script"},
                "outputs": {"script": "Script"},
            },
            {
                "node_id": "n_bible",
                "node_type": "StoryBibleBuild",
                "config": {"lock_immutable_traits": True},
                "inputs": {"script": "Script"},
                "outputs": {"bible": "StoryBible"},
            },
            {
                "node_id": "n_scenes",
                "node_type": "SceneBreakdown",
                "config": {"inherit_lighting": True},
                "inputs": {"bible": "StoryBible"},
                "outputs": {"scenes": "Scene[]"},
            },
            {
                "node_id": "n_shots",
                "node_type": "ShotBreakdown",
                "config": {"default_duration": 5.0},
                "inputs": {"scenes": "Scene[]"},
                "outputs": {"shots": "Shot[]"},
            },
            {
                "node_id": "n_prompt",
                "node_type": "PromptTemplate",
                "config": {"enforce_linter": True},
                "inputs": {"shots": "Shot[]"},
                "outputs": {"prompts": "Prompt"},
            },
            {
                "node_id": "n_approval",
                "node_type": "ManualApproval",
                "config": {"gate_name": "Director Review Before Render"},
                "inputs": {"in": "Prompt"},
                "outputs": {"out": "Prompt"},
            },
        ]
        edges = [
            {"edge_id": "e1", "source_node_id": "n_script_in", "source_port": "script", "target_node_id": "n_analyze", "target_port": "script"},
            {"edge_id": "e2", "source_node_id": "n_analyze", "source_port": "script", "target_node_id": "n_bible", "target_port": "script"},
            {"edge_id": "e3", "source_node_id": "n_bible", "source_port": "bible", "target_node_id": "n_scenes", "target_port": "bible"},
            {"edge_id": "e4", "source_node_id": "n_scenes", "source_port": "scenes", "target_node_id": "n_shots", "target_port": "scenes"},
            {"edge_id": "e5", "source_node_id": "n_shots", "source_port": "shots", "target_node_id": "n_prompt", "target_port": "shots"},
            {"edge_id": "e6", "source_node_id": "n_prompt", "source_port": "prompts", "target_node_id": "n_approval", "target_port": "in"},
        ]
        ui_layout = {
            nid["node_id"]: {"x": idx * 240, "y": 120}
            for idx, nid in enumerate(nodes)
        }
        return nodes, edges, ui_layout

    @classmethod
    def parse_graph_json(
        cls,
        nodes_json: list[dict[str, Any]],
        edges_json: list[dict[str, Any]],
    ) -> tuple[list[WorkflowNodeDef], list[WorkflowEdgeDef]]:
        if not isinstance(nodes_json, list) or not isinstance(edges_json, list):
            raise WorkflowValidationError("'nodes' và 'edges' phải là danh sách.")
        nodes: list[WorkflowNodeDef] = []
        for raw_n in nodes_json:
            if not isinstance(raw_n, dict):
                raise WorkflowValidationError("Mỗi node phải là một object.")
            node_id = raw_n.get("node_id")
            ntype = raw_n.get("node_type")
            if not isinstance(node_id, str) or not node_id.strip():
                raise WorkflowValidationError("Mỗi node phải có 'node_id' là chuỗi không rỗng.")
            if not isinstance(ntype, str) or not ntype.strip():
                raise WorkflowValidationError(f"Node '{node_id}' phải có 'node_type' là chuỗi không rỗng.")
            if ntype not in NODE_CATALOG:
                raise WorkflowValidationError(f"Node type '{ntype}' không nằm trong danh mục hỗ trợ.")
            config = raw_n.get("config")
            if config is None:
                config = {}
            if not isinstance(config, dict):
                raise WorkflowValidationError(f"Node '{node_id}': 'config' phải là object.")
            catalog = NODE_CATALOG[ntype]
            port_defs: dict[str, dict[str, PortDef]] = {}
            for direction in ("inputs", "outputs"):
                supplied = raw_n.get(direction)
                expected = catalog[direction]
                if supplied is not None:
                    cls._require_port_mapping(node_id, direction, supplied)
                    # Catalog nodes have fixed, typed ports. An empty mapping is treated as
                    # "use catalog"; anything else must match the catalog exactly so a client
                    # cannot retype a port to bypass edge type checking.
                    if supplied and supplied != expected:
                        raise WorkflowValidationError(
                            f"Node '{node_id}' ({ntype}): '{direction}' phải khớp danh mục port chuẩn."
                        )
                port_defs[direction] = {k: PortDef(k, v) for k, v in expected.items()}
            nodes.append(
                WorkflowNodeDef(
                    node_id=node_id,
                    node_type=ntype,
                    config=config,
                    inputs=port_defs["inputs"],
                    outputs=port_defs["outputs"],
                )
            )
        edges: list[WorkflowEdgeDef] = []
        for raw_e in edges_json:
            if not isinstance(raw_e, dict) or not all(
                raw_e.get(k) for k in ("edge_id", "source_node_id", "source_port", "target_node_id", "target_port")
            ):
                raise WorkflowValidationError("Mỗi edge phải có đủ edge_id, source_node_id, source_port, target_node_id, target_port.")
            if not all(
                isinstance(raw_e[k], str)
                for k in ("edge_id", "source_node_id", "source_port", "target_node_id", "target_port")
            ):
                raise WorkflowValidationError("Các trường của edge phải là chuỗi.")
            edges.append(
                WorkflowEdgeDef(
                    edge_id=str(raw_e["edge_id"]),
                    source_node_id=str(raw_e["source_node_id"]),
                    source_port=str(raw_e["source_port"]),
                    target_node_id=str(raw_e["target_node_id"]),
                    target_port=str(raw_e["target_port"]),
                )
            )
        return nodes, edges

    @staticmethod
    def _require_port_mapping(node_id: str, direction: str, value: Any) -> None:
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise WorkflowValidationError(
                f"Node '{node_id}': '{direction}' phải là object dạng {{port_name: port_type}}."
            )

    @staticmethod
    def validate_and_plan(
        nodes: list[WorkflowNodeDef],
        edges: list[WorkflowEdgeDef],
        cache_scope: str | None = None,
        external_input_hashes: dict[str, str] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """Validate the DAG and compute Merkle cache keys.

        ``cache_scope`` (e.g. the project id) is folded into every key so that cache
        entries can never be shared between projects. ``external_input_hashes`` maps a
        node type in EXTERNAL_INPUT_NODE_TYPES to a content hash of the project data it
        reads (script, story bible); node types in NODE_IDENTITY_KEYED_TYPES also fold in
        their node_id.
        """
        if len(nodes) > MAX_WORKFLOW_NODES:
            raise WorkflowValidationError(f"Workflow vượt quá {MAX_WORKFLOW_NODES} node.")
        if len(edges) > MAX_WORKFLOW_EDGES:
            raise WorkflowValidationError(f"Workflow vượt quá {MAX_WORKFLOW_EDGES} edge.")
        node_map = {n.node_id: n for n in nodes}
        if len(node_map) != len(nodes):
            raise WorkflowValidationError("Phát hiện node_id bị trùng lặp trong Workflow Graph.")
        adj: dict[str, list[str]] = {n.node_id: [] for n in nodes}
        in_degree: dict[str, int] = {n.node_id: 0 for n in nodes}
        incoming_edges: dict[str, list[WorkflowEdgeDef]] = {n.node_id: [] for n in nodes}
        seen_edge_ids: set[str] = set()
        bound_inputs: set[tuple[str, str]] = set()

        for edge in edges:
            if edge.edge_id in seen_edge_ids:
                raise WorkflowValidationError(f"Phát hiện edge_id bị trùng lặp: '{edge.edge_id}'.")
            seen_edge_ids.add(edge.edge_id)
            if edge.source_node_id not in node_map or edge.target_node_id not in node_map:
                raise WorkflowValidationError(f"Cạnh '{edge.edge_id}' trỏ tới node không tồn tại.")
            src_node = node_map[edge.source_node_id]
            tgt_node = node_map[edge.target_node_id]
            if edge.source_port not in src_node.outputs:
                raise WorkflowValidationError(f"Port nguồn '{edge.source_port}' không tồn tại trên '{src_node.node_id}'.")
            if edge.target_port not in tgt_node.inputs:
                raise WorkflowValidationError(f"Port đích '{edge.target_port}' không tồn tại trên '{tgt_node.node_id}'.")

            src_type = src_node.outputs[edge.source_port].port_type
            tgt_type = tgt_node.inputs[edge.target_port].port_type
            if src_type not in VALID_PORT_TYPES or tgt_type not in VALID_PORT_TYPES:
                raise WorkflowValidationError(f"Port type không hợp lệ: {src_type} -> {tgt_type}")
            if src_type != tgt_type:
                raise WorkflowValidationError(f"Type mismatch: {src_type} != {tgt_type}")
            input_slot = (edge.target_node_id, edge.target_port)
            if input_slot in bound_inputs:
                raise WorkflowValidationError(
                    f"Port đích '{edge.target_port}' trên '{edge.target_node_id}' đã có edge khác nối vào."
                )
            bound_inputs.add(input_slot)

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
            parent_hashes = [
                f"{e.source_node_id}:{e.source_port}->{e.target_port}:{cache_keys[e.source_node_id]}"
                for e in sorted(incoming_edges[nid], key=lambda x: (x.source_node_id, x.source_port, x.target_port))
            ]
            key_material: dict[str, Any] = {"t": node.node_type, "c": node.config, "p": parent_hashes}
            if cache_scope is not None:
                key_material["s"] = cache_scope
            if node.node_type in NODE_IDENTITY_KEYED_TYPES:
                key_material["id"] = node.node_id
            if node.node_type in EXTERNAL_INPUT_NODE_TYPES:
                key_material["x"] = (external_input_hashes or {}).get(node.node_type, "")
            payload = json.dumps(
                key_material,
                sort_keys=True,
                ensure_ascii=False,
            )
            cache_keys[nid] = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        return execution_order, cache_keys

    @staticmethod
    def get_downstream_subplan(
        start_node_id: str,
        execution_order: list[str],
        edges: list[WorkflowEdgeDef],
    ) -> list[str]:
        """Trả về danh sách các node bị ảnh hưởng tính từ start_node_id trở về sau."""
        adj: dict[str, list[str]] = {}
        for e in edges:
            adj.setdefault(e.source_node_id, []).append(e.target_node_id)

        reachable: set[str] = {start_node_id}
        q = deque([start_node_id])
        while q:
            curr = q.popleft()
            for nxt in adj.get(curr, []):
                if nxt not in reachable:
                    reachable.add(nxt)
                    q.append(nxt)

        return [nid for nid in execution_order if nid in reachable]

    @staticmethod
    def get_ancestors(node_id: str, edges: list[WorkflowEdgeDef]) -> set[str]:
        """Return every node that (transitively) feeds ``node_id``."""
        parents: dict[str, list[str]] = {}
        for e in edges:
            parents.setdefault(e.target_node_id, []).append(e.source_node_id)
        ancestors: set[str] = set()
        q = deque(parents.get(node_id, []))
        while q:
            curr = q.popleft()
            if curr in ancestors:
                continue
            ancestors.add(curr)
            q.extend(parents.get(curr, []))
        return ancestors

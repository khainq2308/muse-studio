import { MarkerType, type Edge, type Node, type XYPosition } from "@xyflow/react";
import type { components } from "@/lib/api/schema";

export type WorkflowNodeOut = components["schemas"]["WorkflowNodeOut"];
export type WorkflowEdgeOut = components["schemas"]["WorkflowEdgeOut"];
export type WorkflowCreateOut = components["schemas"]["WorkflowCreateOut"];
export type NodeExecutionState = components["schemas"]["NodeExecutionState"];
export type WorkflowExecutionOut = components["schemas"]["WorkflowExecutionOut"];
export type WorkflowRunStatus = WorkflowExecutionOut["status"];

export interface NodeExecutionSummary {
  state: NodeExecutionState;
  cacheHit: boolean;
}

export type WorkflowNodeData = {
  node: WorkflowNodeOut;
  orderIndex: number;
  cacheKey: string;
  execution: NodeExecutionSummary | null;
  isSelected: boolean;
};

export type WorkflowFlowNode = Node<WorkflowNodeData, "workflowNode">;
export type WorkflowFlowEdge = Edge;

const GRID_COLUMNS = 3;
const GRID_X_SPACING = 280;
const GRID_Y_SPACING = 170;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Reads `{x, y}` from a server `ui_layout` entry; anything else is treated as unknown. */
export function readLayoutPosition(value: unknown): XYPosition | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const { x, y } = value as { x?: unknown; y?: unknown };
  return isFiniteNumber(x) && isFiniteNumber(y) ? { x, y } : null;
}

function fallbackPosition(index: number): XYPosition {
  return {
    x: (index % GRID_COLUMNS) * GRID_X_SPACING,
    y: Math.floor(index / GRID_COLUMNS) * GRID_Y_SPACING,
  };
}

/** Seeds React Flow nodes from the created workflow, preferring the persisted `ui_layout`. */
export function buildFlowNodes(workflow: WorkflowCreateOut): WorkflowFlowNode[] {
  const orderIndexById = new Map(workflow.execution_order.map((id, index) => [id, index]));
  return workflow.nodes.map((node, index) => ({
    id: node.node_id,
    type: "workflowNode",
    position: readLayoutPosition(workflow.ui_layout[node.node_id]) ?? fallbackPosition(index),
    data: {
      node,
      orderIndex: orderIndexById.get(node.node_id) ?? index,
      cacheKey: workflow.cache_keys[node.node_id] ?? "",
      execution: null,
      isSelected: false,
    },
  }));
}

export function buildFlowEdges(edges: WorkflowEdgeOut[]): WorkflowFlowEdge[] {
  return edges.map((edge) => ({
    id: edge.edge_id,
    source: edge.source_node_id,
    target: edge.target_node_id,
    label: `${edge.source_port} → ${edge.target_port}`,
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
}

/** Serializes node positions to the `ui_layout` shape (`{node_id: {x, y}}`) the backend stores. */
export function layoutFromNodes(
  nodes: Pick<WorkflowFlowNode, "id" | "position">[],
): Record<string, XYPosition> {
  return Object.fromEntries(
    nodes.map((node) => [
      node.id,
      { x: Math.round(node.position.x), y: Math.round(node.position.y) },
    ]),
  );
}

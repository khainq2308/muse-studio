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

/** Rendered card size (`w-56` ≈ 224px wide; ~112px tall with the cache line). */
export const NODE_WIDTH = 224;
export const NODE_HEIGHT = 112;
const GRID_X_SPACING = 280;
const GRID_Y_SPACING = 160;
const DEFAULT_GRID_COLUMNS = 3;
const MAX_GRID_COLUMNS = 4;
/**
 * Viewport padding (px) kept around the graph when fitting it into the canvas. The horizontal
 * gutter is wider so the bottom-left zoom controls never cover a node.
 */
export const VIEWPORT_PADDING = 24;
export const VIEWPORT_PADDING_X = 48;
/** Below this zoom node text is unreadable, so fitting the whole graph is abandoned. */
export const MIN_READABLE_ZOOM = 0.7;
export const MAX_FIT_ZOOM = 1;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Reads `{x, y}` from a server `ui_layout` entry; anything else is treated as unknown. */
export function readLayoutPosition(value: unknown): XYPosition | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const { x, y } = value as { x?: unknown; y?: unknown };
  return isFiniteNumber(x) && isFiniteNumber(y) ? { x, y } : null;
}

/** Row-major wrapped grid slot for the node at `index`. */
export function gridPosition(index: number, columns: number = DEFAULT_GRID_COLUMNS): XYPosition {
  const safeColumns = Math.max(1, Math.floor(columns));
  return {
    x: (index % safeColumns) * GRID_X_SPACING,
    y: Math.floor(index / safeColumns) * GRID_Y_SPACING,
  };
}

/**
 * True when `ui_layout` holds a layout worth honoring. An empty layout, or one where every saved
 * position shares a single `y` (the backend M3 template seeds `{x: idx * 240, y: 120}`), is a
 * single unreadable row, so the canvas lays the nodes out on a wrapped grid instead.
 */
export function hasCustomLayout(workflow: Pick<WorkflowCreateOut, "nodes" | "ui_layout">): boolean {
  const positions = workflow.nodes
    .map((node) => readLayoutPosition(workflow.ui_layout[node.node_id]))
    .filter((position): position is XYPosition => position !== null);
  if (positions.length === 0) return false;
  if (positions.length === 1) return true;
  return positions.some((position) => position.y !== positions[0]?.y);
}

export interface AutoLayoutPlan {
  columns: number;
  /** `fit`: the whole graph fits readably; `top`: anchor the first rows and let the user pan. */
  mode: "fit" | "top";
}

function gridSize(nodeCount: number, columns: number) {
  const usedColumns = Math.max(1, Math.min(columns, nodeCount));
  const rows = Math.max(1, Math.ceil(nodeCount / usedColumns));
  return {
    width: (usedColumns - 1) * GRID_X_SPACING + NODE_WIDTH,
    height: (rows - 1) * GRID_Y_SPACING + NODE_HEIGHT,
  };
}

function fitZoom(graph: { width: number; height: number }, width: number, height: number): number {
  const availableWidth = Math.max(1, width - VIEWPORT_PADDING_X * 2);
  const availableHeight = Math.max(1, height - VIEWPORT_PADDING * 2);
  return Math.min(MAX_FIT_ZOOM, availableWidth / graph.width, availableHeight / graph.height);
}

/**
 * Picks the grid column count that shows the whole graph at the largest zoom for a canvas of
 * `width` x `height`. If even the best fit is unreadable (narrow phones), falls back to the widest
 * grid that still fits horizontally at a readable zoom and anchors it at the top.
 */
export function planAutoLayout(nodeCount: number, width: number, height: number): AutoLayoutPlan {
  if (nodeCount <= 0 || width <= 0 || height <= 0) {
    return { columns: DEFAULT_GRID_COLUMNS, mode: "fit" };
  }
  let best: { columns: number; zoom: number } = { columns: 1, zoom: 0 };
  for (let columns = 1; columns <= Math.min(MAX_GRID_COLUMNS, nodeCount); columns += 1) {
    const zoom = fitZoom(gridSize(nodeCount, columns), width, height);
    if (zoom > best.zoom + 0.001) best = { columns, zoom };
  }
  if (best.zoom >= MIN_READABLE_ZOOM) return { columns: best.columns, mode: "fit" };

  let columns = 1;
  while (
    columns < Math.min(MAX_GRID_COLUMNS, nodeCount) &&
    (width - VIEWPORT_PADDING_X * 2) / gridSize(nodeCount, columns + 1).width >= MIN_READABLE_ZOOM
  ) {
    columns += 1;
  }
  return { columns, mode: "top" };
}

/** Bounds of the given nodes, using measured sizes when React Flow has them. */
export function flowNodesBounds(nodes: Pick<WorkflowFlowNode, "position" | "measured">[]) {
  if (nodes.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.position.x);
    minY = Math.min(minY, node.position.y);
    maxX = Math.max(maxX, node.position.x + (node.measured?.width ?? NODE_WIDTH));
    maxY = Math.max(maxY, node.position.y + (node.measured?.height ?? NODE_HEIGHT));
  }
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

/**
 * Viewport that frames `nodes` in a `width` x `height` canvas. `fit` centers the whole graph;
 * `top` keeps a readable zoom (fitting the width only) and anchors the first row at the top.
 */
export function viewportForNodes(
  nodes: Pick<WorkflowFlowNode, "position" | "measured">[],
  width: number,
  height: number,
  mode: AutoLayoutPlan["mode"],
  minZoom: number,
): { x: number; y: number; zoom: number } | null {
  const bounds = flowNodesBounds(nodes);
  if (!bounds || width <= 0 || height <= 0) return null;
  const availableWidth = Math.max(1, width - VIEWPORT_PADDING_X * 2);
  const widthZoom = Math.min(MAX_FIT_ZOOM, availableWidth / bounds.width);
  const rawZoom = mode === "top" ? widthZoom : fitZoom(bounds, width, height);
  const zoom = Math.max(minZoom, rawZoom);
  const x = (width - bounds.width * zoom) / 2 - bounds.x * zoom;
  const fitsVertically = bounds.height * zoom <= height - VIEWPORT_PADDING * 2;
  const y =
    mode === "top" && !fitsVertically
      ? VIEWPORT_PADDING - bounds.y * zoom
      : (height - bounds.height * zoom) / 2 - bounds.y * zoom;
  return { x, y, zoom };
}

/**
 * Seeds React Flow nodes from the created workflow. A custom persisted `ui_layout` is honored
 * (missing entries fall back to the grid slot); the backend default single row or an empty layout
 * is replaced by a wrapped grid with `columns` columns.
 */
export function buildFlowNodes(
  workflow: WorkflowCreateOut,
  columns: number = DEFAULT_GRID_COLUMNS,
): WorkflowFlowNode[] {
  const orderIndexById = new Map(workflow.execution_order.map((id, index) => [id, index]));
  const useSaved = hasCustomLayout(workflow);
  return workflow.nodes.map((node, index) => {
    const orderIndex = orderIndexById.get(node.node_id) ?? index;
    const saved = useSaved ? readLayoutPosition(workflow.ui_layout[node.node_id]) : null;
    return {
      id: node.node_id,
      type: "workflowNode",
      position: saved ?? gridPosition(useSaved ? index : orderIndex, columns),
      data: {
        node,
        orderIndex,
        cacheKey: workflow.cache_keys[node.node_id] ?? "",
        execution: null,
        isSelected: false,
      },
    };
  });
}

/** Re-slots nodes on a wrapped grid in execution order (used while the layout is automatic). */
export function regridFlowNodes(nodes: WorkflowFlowNode[], columns: number): WorkflowFlowNode[] {
  return nodes.map((node) => ({ ...node, position: gridPosition(node.data.orderIndex, columns) }));
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

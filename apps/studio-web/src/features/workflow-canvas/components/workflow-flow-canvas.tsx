import "@xyflow/react/dist/style.css";
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  type NodeTypes,
  type OnNodeDrag,
  type OnNodesChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  viewportForNodes,
  type AutoLayoutPlan,
  type WorkflowFlowEdge,
  type WorkflowFlowNode,
} from "@/features/workflow-canvas/components/flow-model";
import { WorkflowNode } from "@/features/workflow-canvas/components/workflow-node";

const nodeTypes: NodeTypes = { workflowNode: WorkflowNode };
const MIN_ZOOM = 0.3;

export interface CanvasSize {
  width: number;
  height: number;
}

interface WorkflowFlowCanvasProps {
  nodes: WorkflowFlowNode[];
  edges: WorkflowFlowEdge[];
  onNodesChange: OnNodesChange<WorkflowFlowNode>;
  onNodeSelect: (nodeId: string) => void;
  onNodeDragStop: OnNodeDrag<WorkflowFlowNode>;
  /** Reports the canvas size so the page can choose a grid that fits it. */
  onSizeChange?: (size: CanvasSize) => void;
  /** Changing this key re-frames the viewport (new workflow or re-gridded layout). */
  viewportKey: string;
  viewportMode: AutoLayoutPlan["mode"];
}

/** Read-only topology (no connect/delete): nodes can only be moved, and moves persist as layout. */
export function WorkflowFlowCanvas({
  nodes,
  edges,
  onNodesChange,
  onNodeSelect,
  onNodeDragStop,
  onSizeChange,
  viewportKey,
  viewportMode,
}: WorkflowFlowCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const nodesRef = useRef(nodes);
  const onSizeChangeRef = useRef(onSizeChange);
  const [instance, setInstance] = useState<ReactFlowInstance<
    WorkflowFlowNode,
    WorkflowFlowEdge
  > | null>(null);

  useLayoutEffect(() => {
    nodesRef.current = nodes;
    onSizeChangeRef.current = onSizeChange;
  });

  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect && rect.width > 0 && rect.height > 0) {
        onSizeChangeRef.current?.({ width: rect.width, height: rect.height });
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Frame the graph ourselves: React Flow's fitView centers it, which leaves nodes unreadably
  // small (or cut off at both ends) on narrow canvases; `top` mode anchors the first row instead.
  useEffect(() => {
    const element = containerRef.current;
    if (!instance || !element) return;
    const frame = requestAnimationFrame(() => {
      const viewport = viewportForNodes(
        nodesRef.current,
        element.clientWidth,
        element.clientHeight,
        viewportMode,
        MIN_ZOOM,
      );
      if (viewport) void instance.setViewport(viewport);
    });
    return () => cancelAnimationFrame(frame);
  }, [instance, viewportKey, viewportMode]);

  return (
    <div
      ref={containerRef}
      aria-label="Workflow DAG canvas"
      role="region"
      className="h-[420px] overflow-hidden rounded-2xl border border-border bg-secondary/20 sm:h-[460px] lg:h-[500px]"
    >
      <ReactFlow<WorkflowFlowNode, WorkflowFlowEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={(_event, node) => onNodeSelect(node.id)}
        onNodeDragStop={onNodeDragStop}
        onInit={setInstance}
        nodesConnectable={false}
        edgesFocusable={false}
        deleteKeyCode={null}
        fitView
        fitViewOptions={{ padding: 0.12, minZoom: MIN_ZOOM, maxZoom: 1 }}
        minZoom={MIN_ZOOM}
        maxZoom={1.5}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls showInteractive={false} fitViewOptions={{ padding: 0.12, maxZoom: 1 }} />
      </ReactFlow>
    </div>
  );
}

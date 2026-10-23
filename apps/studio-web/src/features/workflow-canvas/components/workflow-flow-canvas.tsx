import "@xyflow/react/dist/style.css";
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  type NodeTypes,
  type OnNodeDrag,
  type OnNodesChange,
} from "@xyflow/react";
import type {
  WorkflowFlowEdge,
  WorkflowFlowNode,
} from "@/features/workflow-canvas/components/flow-model";
import { WorkflowNode } from "@/features/workflow-canvas/components/workflow-node";

const nodeTypes: NodeTypes = { workflowNode: WorkflowNode };

interface WorkflowFlowCanvasProps {
  nodes: WorkflowFlowNode[];
  edges: WorkflowFlowEdge[];
  onNodesChange: OnNodesChange<WorkflowFlowNode>;
  onNodeSelect: (nodeId: string) => void;
  onNodeDragStop: OnNodeDrag<WorkflowFlowNode>;
}

/** Read-only topology (no connect/delete): nodes can only be moved, and moves persist as layout. */
export function WorkflowFlowCanvas({
  nodes,
  edges,
  onNodesChange,
  onNodeSelect,
  onNodeDragStop,
}: WorkflowFlowCanvasProps) {
  return (
    <div
      aria-label="Workflow DAG canvas"
      role="region"
      className="h-[460px] overflow-hidden rounded-2xl border border-border bg-secondary/20"
    >
      <ReactFlow<WorkflowFlowNode, WorkflowFlowEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={(_event, node) => onNodeSelect(node.id)}
        onNodeDragStop={onNodeDragStop}
        nodesConnectable={false}
        edgesFocusable={false}
        deleteKeyCode={null}
        fitView
        minZoom={0.3}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

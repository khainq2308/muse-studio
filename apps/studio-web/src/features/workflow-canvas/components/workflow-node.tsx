import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Cpu, ShieldCheck } from "lucide-react";
import { memo } from "react";
import type { WorkflowFlowNode } from "@/features/workflow-canvas/components/flow-model";
import {
  nodeStateBadgeClass,
  nodeStateBorderClass,
  nodeStateLabel,
} from "@/features/workflow-canvas/components/node-state";
import { cn } from "@/lib/utils";

function WorkflowNodeCard({ data }: NodeProps<WorkflowFlowNode>) {
  const { node, orderIndex, cacheKey, execution, isSelected } = data;
  const state = execution?.state;
  const isGate = node.node_type === "ManualApproval";

  return (
    <div
      data-testid={`workflow-node-${node.node_id}`}
      data-state={state ?? "not_run"}
      className={cn(
        "w-56 rounded-xl border-2 bg-card p-3 text-left shadow-sm transition-shadow",
        nodeStateBorderClass(state),
        isSelected && "ring-2 ring-ring/40 shadow-md",
      )}
    >
      <Handle type="target" position={Position.Left} className="bg-muted-foreground!" />
      <div className="flex items-start justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] font-bold text-muted-foreground">
            #{orderIndex + 1}
          </span>
          <span className="truncate font-mono text-xs font-semibold text-foreground">
            {node.node_id}
          </span>
        </span>
        <span
          className={cn(
            "shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold",
            nodeStateBadgeClass(state),
          )}
        >
          {nodeStateLabel(state)}
        </span>
      </div>
      <p className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-primary">
        {isGate ? (
          <ShieldCheck className="size-3.5" aria-hidden="true" />
        ) : (
          <Cpu className="size-3.5" aria-hidden="true" />
        )}
        {node.node_type}
      </p>
      <p className="mt-2 truncate border-t border-border pt-1.5 font-mono text-[10px] text-muted-foreground">
        Merkle: {cacheKey ? `${cacheKey.slice(0, 14)}…` : "—"}
      </p>
      {execution?.cacheHit && (
        <p className="mt-0.5 text-[10px] font-medium text-success">Merkle cache hit</p>
      )}
      <Handle type="source" position={Position.Right} className="bg-muted-foreground!" />
    </div>
  );
}

export const WorkflowNode = memo(WorkflowNodeCard);

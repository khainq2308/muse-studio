import type { NodeExecutionState } from "@/features/workflow-canvas/components/flow-model";

/** Theme-token classes for a node execution state badge (text label is always rendered too). */
export function nodeStateBadgeClass(state: NodeExecutionState | undefined): string {
  switch (state) {
    case "succeeded":
      return "border-success/30 bg-success/10 text-success";
    case "waiting_approval":
      return "border-warning/40 bg-warning/15 text-warning";
    case "running":
    case "queued":
      return "border-primary/30 bg-primary/10 text-primary";
    case "failed":
    case "blocked":
      return "border-destructive/30 bg-destructive/10 text-destructive";
    default:
      return "border-border bg-secondary text-muted-foreground";
  }
}

/** Border accent of the node card on the canvas, keyed by execution state. */
export function nodeStateBorderClass(state: NodeExecutionState | undefined): string {
  switch (state) {
    case "succeeded":
      return "border-success/50";
    case "waiting_approval":
      return "border-warning/60";
    case "running":
    case "queued":
      return "border-primary/50";
    case "failed":
    case "blocked":
      return "border-destructive/60";
    default:
      return "border-border";
  }
}

export function nodeStateLabel(state: NodeExecutionState | undefined): string {
  return state ? state.replace("_", " ") : "not run";
}

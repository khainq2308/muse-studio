import { useNodesState, type OnNodeDrag, type XYPosition } from "@xyflow/react";
import {
  AlertTriangle,
  CheckCircle2,
  GitBranch,
  Info,
  LoaderCircle,
  Play,
  Plus,
  Save,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { ConfirmActionDialog } from "@/components/shared/confirm-action-dialog";
import { Button } from "@/components/ui/button";
import {
  useApproveWorkflowGate,
  useCreateWorkflow,
  useExecuteWorkflow,
  useSaveWorkflowLayout,
  type WorkflowApprovalResponse,
  type WorkflowCreateResponse,
  type WorkflowExecutionResponse,
  type WorkflowRunRequest,
  type WorkflowRunResponse,
} from "@/features/workflow-canvas/api";
import {
  buildFlowEdges,
  buildFlowNodes,
  hasCustomLayout,
  layoutFromNodes,
  planAutoLayout,
  regridFlowNodes,
  type AutoLayoutPlan,
  type NodeExecutionSummary,
  type WorkflowFlowNode,
  type WorkflowRunStatus,
} from "@/features/workflow-canvas/components/flow-model";
import {
  nodeStateBadgeClass,
  nodeStateLabel,
} from "@/features/workflow-canvas/components/node-state";
import {
  WorkflowFlowCanvas,
  type CanvasSize,
} from "@/features/workflow-canvas/components/workflow-flow-canvas";
import { actionErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

interface WorkflowCanvasPageProps {
  projectId: string;
}

function isExecutionResponse(run: WorkflowRunResponse): run is WorkflowExecutionResponse {
  return "node_executions" in run;
}

function RunStatusIcon({ status }: { status: WorkflowRunStatus }) {
  switch (status) {
    case "succeeded":
      return <CheckCircle2 className="size-4 text-success" aria-hidden="true" />;
    case "waiting_approval":
      return <AlertTriangle className="size-4 text-warning" aria-hidden="true" />;
    case "running":
      return <LoaderCircle className="size-4 animate-spin text-primary" aria-hidden="true" />;
    case "failed":
      return <XCircle className="size-4 text-destructive" aria-hidden="true" />;
  }
}

/**
 * Applies an approval response onto the previous run: keeps its plan order and logs, and only
 * replaces state/cache_hit/output_refs for nodes the server reported.
 */
function mergeApproval(
  previous: WorkflowExecutionResponse,
  approved: WorkflowApprovalResponse,
): WorkflowExecutionResponse {
  const updates = new Map(approved.node_executions.map((item) => [item.node_id, item]));
  return {
    ...previous,
    run_id: approved.run_id,
    status: approved.status,
    execution_mode: approved.execution_mode,
    node_executions: previous.node_executions.map((item) => {
      const update = updates.get(item.node_id);
      return update
        ? {
            ...item,
            state: update.state,
            cache_hit: update.cache_hit,
            output_refs: update.output_refs,
          }
        : item;
    }),
  };
}

const RUN_MODE_HINTS: Record<WorkflowRunRequest["run_mode"], string> = {
  dry_run:
    "Planning only: computes execution order and cache hits without running nodes. Safe default.",
  from_here: "Partial run that starts from the selected node.",
  downstream: "Partial run of the selected node and the nodes downstream of it.",
  full: "Runs the entire DAG simulation; stops at ManualApproval gates.",
};

/** Automatic grid layout state; `null` once the server layout is custom or the user dragged. */
interface AutoLayoutState extends AutoLayoutPlan {
  workflowId: string;
}

interface QueuedLayout {
  workflowId: string;
  layout: Record<string, XYPosition>;
}

export function WorkflowCanvasPage({ projectId }: WorkflowCanvasPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const createWorkflow = useCreateWorkflow(projectId);
  const saveLayout = useSaveWorkflowLayout(projectId);
  const executeWorkflow = useExecuteWorkflow(projectId);
  const approveGate = useApproveWorkflowGate(projectId);

  const [workflow, setWorkflow] = useState<WorkflowCreateResponse | null>(null);
  const [flowNodes, setFlowNodes, onFlowNodesChange] = useNodesState<WorkflowFlowNode>([]);
  const [workflowName, setWorkflowName] = useState("Script to Batch Film Pipeline");
  const [workflowDesc, setWorkflowDesc] = useState(
    "Typed DAG pipeline with Merkle cache keys and human-in-the-loop approval gates",
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [runMode, setRunMode] = useState<WorkflowRunRequest["run_mode"]>("dry_run");
  const [forceRerun, setForceRerun] = useState(false);
  const [confirmFullRun, setConfirmFullRun] = useState(false);
  const [lastRun, setLastRun] = useState<WorkflowRunResponse | null>(null);
  const [layoutSavedMessage, setLayoutSavedMessage] = useState<string | null>(null);
  const [confirmingReplace, setConfirmingReplace] = useState(false);
  const [pendingApprovalNodeId, setPendingApprovalNodeId] = useState<string | null>(null);
  const [autoLayout, setAutoLayout] = useState<AutoLayoutState | null>(null);
  const [viewportMode, setViewportMode] = useState<AutoLayoutPlan["mode"]>("fit");
  const [viewportRevision, setViewportRevision] = useState(0);

  // Layout saves are serialized: a drop during an in-flight PATCH queues only the latest layout,
  // so an older request can never overwrite a newer position on the server.
  const layoutInFlight = useRef(false);
  const queuedLayout = useRef<QueuedLayout | null>(null);
  const canvasSize = useRef<CanvasSize | null>(null);

  const nodes = useMemo(() => workflow?.nodes ?? [], [workflow]);
  const flowEdges = useMemo(() => buildFlowEdges(workflow?.edges ?? []), [workflow]);
  const selectedNode = useMemo(
    () => nodes.find((node) => node.node_id === selectedNodeId) ?? nodes[0] ?? null,
    [nodes, selectedNodeId],
  );
  const executionRun = lastRun && isExecutionResponse(lastRun) ? lastRun : null;

  const nodeExecutionById = useMemo(() => {
    const map = new Map<string, NodeExecutionSummary>();
    for (const item of executionRun?.node_executions ?? []) {
      map.set(item.node_id, { state: item.state, cacheHit: item.cache_hit });
    }
    return map;
  }, [executionRun]);

  const displayNodes = useMemo(
    () =>
      flowNodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          execution: nodeExecutionById.get(node.id) ?? null,
          isSelected: node.id === selectedNode?.node_id,
        },
      })),
    [flowNodes, nodeExecutionById, selectedNode],
  );

  const waitingGateIds = useMemo(
    () =>
      (executionRun?.node_executions ?? [])
        .filter((item) => item.state === "waiting_approval")
        .map((item) => item.node_id),
    [executionRun],
  );
  const failedExecutions = useMemo(
    () => (executionRun?.node_executions ?? []).filter((item) => item.state === "failed"),
    [executionRun],
  );
  const selectedExecution =
    executionRun?.node_executions.find((item) => item.node_id === selectedNode?.node_id) ?? null;

  const needsStartNode = runMode === "from_here" || runMode === "downstream";
  const effectiveStartNodeId = selectedNode?.node_id ?? null;
  const canExecute =
    Boolean(workflow) &&
    !executeWorkflow.isPending &&
    (!needsStartNode || Boolean(effectiveStartNodeId)) &&
    (runMode !== "full" || confirmFullRun);

  function submitCreate() {
    setLayoutSavedMessage(null);
    createWorkflow.mutate(
      {
        name: workflowName.trim() || "Script to Batch Film Pipeline",
        description: workflowDesc.trim() || "M3 Default Typed DAG Workflow",
        use_default_m3_template: true,
      },
      {
        onSuccess: (created) => {
          setConfirmingReplace(false);
          setPendingApprovalNodeId(null);
          executeWorkflow.reset();
          approveGate.reset();
          saveLayout.reset();
          // A canvas that is already mounted will not report its size again, so reuse it.
          const plan = canvasSize.current
            ? planAutoLayout(
                created.nodes.length,
                canvasSize.current.width,
                canvasSize.current.height,
              )
            : { columns: 3, mode: "fit" as const };
          const isAuto = !hasCustomLayout(created);
          setWorkflow(created);
          setFlowNodes(buildFlowNodes(created, plan.columns));
          setAutoLayout(isAuto ? { workflowId: created.id, ...plan } : null);
          setViewportMode(isAuto ? plan.mode : "fit");
          setViewportRevision((revision) => revision + 1);
          setSelectedNodeId(created.nodes[0]?.node_id ?? null);
          setLastRun(null);
        },
      },
    );
  }

  function sendLayout(next: QueuedLayout) {
    if (layoutInFlight.current) {
      queuedLayout.current = next;
      return;
    }
    layoutInFlight.current = true;
    setLayoutSavedMessage(null);
    const settle = () => {
      layoutInFlight.current = false;
      const queued = queuedLayout.current;
      queuedLayout.current = null;
      if (queued) sendLayout(queued);
    };
    saveLayout.mutate(next.workflowId, next.layout, {
      onSuccess: (saved) => {
        setLayoutSavedMessage(
          `Canvas layout saved (v${saved.version_number}); Merkle cache keys are unchanged.`,
        );
        settle();
      },
      onError: settle,
    });
  }

  // While the layout is automatic, re-grid it to whatever column count fits the canvas best.
  function handleCanvasSize(size: CanvasSize) {
    canvasSize.current = size;
    if (!workflow || !autoLayout || autoLayout.workflowId !== workflow.id) return;
    const plan = planAutoLayout(workflow.nodes.length, size.width, size.height);
    if (plan.columns !== autoLayout.columns) {
      setFlowNodes((current) => regridFlowNodes(current, plan.columns));
    }
    if (plan.columns !== autoLayout.columns || plan.mode !== autoLayout.mode) {
      setAutoLayout({ ...autoLayout, ...plan });
      setViewportMode(plan.mode);
      setViewportRevision((revision) => revision + 1);
    }
  }

  const handleNodeDragStop: OnNodeDrag<WorkflowFlowNode> = (_event, _node, draggedNodes) => {
    if (!workflow) return;
    setAutoLayout(null);
    const dropped = new Map(draggedNodes.map((node) => [node.id, node.position]));
    const layout = layoutFromNodes(
      flowNodes.map((node) => ({ id: node.id, position: dropped.get(node.id) ?? node.position })),
    );
    sendLayout({ workflowId: workflow.id, layout });
  };

  function runWorkflow(request: WorkflowRunRequest) {
    if (!workflow) return;
    executeWorkflow.mutate(workflow.id, request, {
      onSuccess: (result) => {
        setPendingApprovalNodeId(null);
        approveGate.reset();
        setLastRun(result);
      },
    });
  }

  function requestApproval(nodeId: string) {
    approveGate.reset();
    setSelectedNodeId(nodeId);
    setPendingApprovalNodeId(nodeId);
  }

  function confirmApproval() {
    if (!workflow || !executionRun || !pendingApprovalNodeId) return;
    approveGate.mutate(workflow.id, executionRun.run_id, pendingApprovalNodeId, {
      onSuccess: (approved) => {
        setPendingApprovalNodeId(null);
        setLastRun((previous) =>
          previous && isExecutionResponse(previous) ? mergeApproval(previous, approved) : previous,
        );
      },
      onError: () => setPendingApprovalNodeId(null),
    });
  }

  return (
    <div data-testid="workflow-canvas-page" data-project-id={projectId} className="space-y-6">
      <div className="flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <GitBranch className="size-5 text-primary" aria-hidden="true" />
            <h2 className="text-xl font-semibold tracking-tight">
              Workflow DAG Canvas & Merkle Execution
            </h2>
            <span className="rounded-full border border-warning/35 bg-warning/10 px-2.5 py-0.5 text-xs font-medium text-warning">
              Simulation / Planning Engine
            </span>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Inspect typed DAG nodes, verify deterministic Merkle cache keys, plan execution order
            with Dry Run, or step through Manual Approval gates. Real provider renders run in Shot
            Board & Queue Monitor.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant={workflow ? "outline" : "default"}
            size="sm"
            disabled={createWorkflow.isPending || confirmingReplace}
            onClick={() => {
              if (workflow) {
                setConfirmingReplace(true);
                return;
              }
              submitCreate();
            }}
          >
            {createWorkflow.isPending ? (
              <>
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                Initializing DAG...
              </>
            ) : (
              <>
                <Plus className="size-4" aria-hidden="true" />
                {workflow ? "Create new M3 DAG" : "Initialize M3 Pipeline DAG"}
              </>
            )}
          </Button>

          {workflow && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={saveLayout.isPending}
              onClick={() =>
                sendLayout({ workflowId: workflow.id, layout: layoutFromNodes(flowNodes) })
              }
            >
              {saveLayout.isPending ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Save className="size-4" aria-hidden="true" />
              )}
              Save canvas layout
            </Button>
          )}
        </div>
      </div>

      <p
        role="note"
        className="flex items-start gap-2 rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs text-muted-foreground"
      >
        <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span>
          The Orchestrator does not yet expose an endpoint to read a workflow back, so this canvas
          only keeps the workflow created in this tab. Reloading or leaving the page loses access to
          it; the workflow itself remains stored on the server.
        </span>
      </p>

      {confirmingReplace && workflow && (
        <ConfirmActionDialog
          title="Create a new workflow and leave the current one?"
          confirmLabel="Create new DAG"
          isSubmitting={createWorkflow.isPending}
          onConfirm={submitCreate}
          onCancel={() => setConfirmingReplace(false)}
        >
          <p>
            <span className="font-mono">{workflow.name}</span> (
            <span className="font-mono">{workflow.id}</span>) stays on the server, but this page
            cannot reopen it afterwards. Its latest run result will also be cleared from view.
          </p>
        </ConfirmActionDialog>
      )}

      {createWorkflow.isError && (
        <p role="alert" className="text-xs text-destructive">
          {actionErrorMessage(createWorkflow.error, activeAdminToken, "create the workflow")}
        </p>
      )}

      {saveLayout.isError && (
        <p role="alert" className="text-xs text-destructive">
          {actionErrorMessage(saveLayout.error, activeAdminToken, "save the canvas layout")}
        </p>
      )}

      {layoutSavedMessage && (
        <p
          role="status"
          className="rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-xs text-success"
        >
          {layoutSavedMessage}
        </p>
      )}

      {!workflow ? (
        <div className="rounded-2xl border border-dashed border-border bg-secondary/25 p-8">
          <div className="mx-auto max-w-xl space-y-4 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <GitBranch className="size-6" aria-hidden="true" />
            </div>
            <h3 className="text-base font-semibold">
              Initialize the M3 Typed DAG Workflow Template
            </h3>
            <p className="text-sm text-muted-foreground">
              Create a project workflow instance to inspect topological execution order, node port
              contracts, Merkle cache keys, and interactive approval gates.
            </p>

            <div className="grid gap-3 text-left sm:grid-cols-2">
              <label className="space-y-1 text-xs font-medium">
                <span>Workflow name</span>
                <input
                  value={workflowName}
                  onChange={(event) => setWorkflowName(event.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </label>
              <label className="space-y-1 text-xs font-medium">
                <span>Description</span>
                <input
                  value={workflowDesc}
                  onChange={(event) => setWorkflowDesc(event.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </label>
            </div>
          </div>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-12">
          <div className="space-y-4 lg:col-span-8">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-secondary/35 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">{workflow.name}</span>
                <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs font-medium text-primary">
                  v{workflow.current_version}
                </span>
                <span className="font-mono text-xs text-muted-foreground">
                  {nodes.length} nodes · {flowEdges.length} edges
                </span>
              </div>
              <span className="font-mono text-xs text-muted-foreground">ID: {workflow.id}</span>
            </div>

            <WorkflowFlowCanvas
              nodes={displayNodes}
              edges={flowEdges}
              onNodesChange={onFlowNodesChange}
              onNodeSelect={setSelectedNodeId}
              onNodeDragStop={handleNodeDragStop}
              onSizeChange={handleCanvasSize}
              viewportKey={`${workflow.id}:${viewportRevision}`}
              viewportMode={viewportMode}
            />
            <p className="text-xs text-muted-foreground">
              Drag a node to move it; the new layout is saved when you drop it and never changes
              Merkle cache keys. Use the node list for keyboard selection and node actions.
            </p>

            <section
              aria-label="Workflow nodes"
              className="rounded-xl border border-border bg-card p-4"
            >
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Nodes in execution order
              </h3>
              <ul className="mt-2.5 grid gap-1.5 sm:grid-cols-2">
                {workflow.execution_order.map((nodeId, orderIndex) => {
                  const node = nodes.find((item) => item.node_id === nodeId);
                  if (!node) return null;
                  const state = nodeExecutionById.get(nodeId)?.state;
                  const isSelected = selectedNode?.node_id === nodeId;
                  return (
                    <li key={nodeId}>
                      <button
                        type="button"
                        aria-pressed={isSelected}
                        onClick={() => setSelectedNodeId(nodeId)}
                        className={cn(
                          "flex w-full items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-left text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          isSelected
                            ? "border-primary bg-primary/5"
                            : "border-border hover:border-primary/40",
                        )}
                      >
                        <span className="min-w-0 truncate">
                          <span className="font-mono text-muted-foreground">{orderIndex + 1}.</span>{" "}
                          <span className="font-mono font-semibold">{nodeId}</span>{" "}
                          <span className="text-muted-foreground">{node.node_type}</span>
                        </span>
                        <span
                          className={cn(
                            "shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold",
                            nodeStateBadgeClass(state),
                          )}
                        >
                          {nodeStateLabel(state)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          </div>

          <div className="space-y-4 lg:col-span-4">
            <section
              aria-label="Workflow execution controls"
              className="space-y-3.5 rounded-xl border border-border bg-secondary/30 p-4"
            >
              <div className="flex items-center justify-between">
                <h3 className="flex items-center gap-1.5 text-sm font-semibold">
                  <Sparkles className="size-4 text-primary" aria-hidden="true" />
                  Execute / Plan Workflow
                </h3>
                <span className="font-mono text-[11px] text-muted-foreground">POST .../runs</span>
              </div>

              <label className="block space-y-1 text-xs font-medium">
                <span>Run mode</span>
                <select
                  aria-label="Run mode"
                  aria-describedby="workflow-run-mode-hint"
                  value={runMode}
                  onChange={(event) => {
                    const nextMode = event.target.value as WorkflowRunRequest["run_mode"];
                    setRunMode(nextMode);
                    if (nextMode !== "full") setConfirmFullRun(false);
                  }}
                  className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="dry_run">Dry run (plan only)</option>
                  <option value="from_here">From selected node</option>
                  <option value="downstream">Selected + downstream</option>
                  <option value="full">Full DAG</option>
                </select>
                <span
                  id="workflow-run-mode-hint"
                  className="block font-normal leading-relaxed text-muted-foreground"
                >
                  <code className="font-mono">{runMode}</code> — {RUN_MODE_HINTS[runMode]}
                </span>
              </label>

              {needsStartNode && (
                <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs">
                  <span className="text-muted-foreground">Selected start node: </span>
                  <strong className="font-mono text-foreground">
                    {effectiveStartNodeId ?? "None selected"}
                  </strong>
                </div>
              )}

              <label className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={forceRerun}
                  onChange={(event) => setForceRerun(event.target.checked)}
                  className="rounded border-input"
                />
                <span>Force rerun (bypass Merkle cache hits)</span>
              </label>

              {runMode === "full" && (
                <label className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 p-2.5 text-xs text-foreground">
                  <input
                    type="checkbox"
                    checked={confirmFullRun}
                    onChange={(event) => setConfirmFullRun(event.target.checked)}
                    className="mt-0.5 rounded border-input"
                  />
                  <span>
                    Confirm full DAG execution across all {nodes.length} nodes (stops at{" "}
                    <code className="font-mono">ManualApproval</code> gates).
                  </span>
                </label>
              )}

              {executeWorkflow.isError && (
                <p role="alert" className="text-xs text-destructive">
                  {actionErrorMessage(executeWorkflow.error, activeAdminToken, "run the workflow")}
                </p>
              )}

              <Button
                type="button"
                className="w-full"
                disabled={!canExecute}
                onClick={() =>
                  runWorkflow({
                    run_mode: runMode,
                    force_rerun: forceRerun,
                    start_node_id: needsStartNode ? effectiveStartNodeId : null,
                  })
                }
              >
                {executeWorkflow.isPending ? (
                  <>
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                    Running DAG...
                  </>
                ) : (
                  <>
                    <Play className="size-4" aria-hidden="true" />
                    {runMode === "dry_run" ? "Run Dry-Run Plan" : `Execute (${runMode})`}
                  </>
                )}
              </Button>
            </section>

            {selectedNode && (
              <section
                aria-label="Node inspector"
                className="space-y-3 rounded-xl border border-border bg-card p-4"
              >
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold">
                    Node Inspector: <span className="font-mono">{selectedNode.node_id}</span>
                  </h3>
                  <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary">
                    {selectedNode.node_type}
                  </span>
                </div>

                <div className="space-y-2 text-xs">
                  <div>
                    <p className="font-medium text-muted-foreground">Merkle Cache Key</p>
                    <p className="mt-0.5 break-all rounded bg-secondary px-2 py-1 font-mono text-[11px]">
                      {workflow.cache_keys[selectedNode.node_id] ?? "—"}
                    </p>
                  </div>
                  <p>
                    <span className="text-muted-foreground">State: </span>
                    <span
                      className={cn(
                        "rounded-full border px-2 py-0.5 text-[10px] font-semibold",
                        nodeStateBadgeClass(selectedExecution?.state),
                      )}
                    >
                      {nodeStateLabel(selectedExecution?.state)}
                    </span>
                  </p>
                  {selectedExecution && selectedExecution.logs.length > 0 && (
                    <div>
                      <p className="font-medium text-muted-foreground">Logs</p>
                      <ul className="mt-0.5 space-y-0.5 rounded bg-secondary px-2 py-1 text-[11px]">
                        {selectedExecution.logs.map((line, index) => (
                          <li key={`${index}-${line}`}>{line}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={executeWorkflow.isPending}
                    onClick={() =>
                      runWorkflow({
                        run_mode: "from_here",
                        force_rerun: forceRerun,
                        start_node_id: selectedNode.node_id,
                      })
                    }
                  >
                    <Play className="size-3.5" aria-hidden="true" />
                    Run from this node
                  </Button>
                  {selectedExecution?.state === "waiting_approval" && (
                    <Button
                      type="button"
                      size="sm"
                      disabled={approveGate.isPending || pendingApprovalNodeId !== null}
                      onClick={() => requestApproval(selectedNode.node_id)}
                    >
                      <ShieldCheck className="size-3.5" aria-hidden="true" />
                      Approve gate
                    </Button>
                  )}
                </div>

                {pendingApprovalNodeId && executionRun && (
                  <ConfirmActionDialog
                    title={`Approve manual gate ${pendingApprovalNodeId}?`}
                    confirmLabel={approveGate.isPending ? "Approving..." : "Approve & resume"}
                    isSubmitting={approveGate.isPending}
                    onConfirm={confirmApproval}
                    onCancel={() => setPendingApprovalNodeId(null)}
                  >
                    <p>
                      Approval cannot be undone. The run resumes until the next ManualApproval gate.
                      Approvals are not cached, so a later run stops at this gate again.
                    </p>
                  </ConfirmActionDialog>
                )}

                {approveGate.isError && (
                  <p role="alert" className="text-xs text-destructive">
                    {actionErrorMessage(
                      approveGate.error,
                      activeAdminToken,
                      "approve the manual gate",
                    )}
                  </p>
                )}
              </section>
            )}

            {lastRun && (
              <section
                aria-label="Execution run summary"
                className="space-y-3 rounded-xl border border-border bg-card p-4"
              >
                <div className="flex items-center justify-between gap-2">
                  <h3 className="flex items-center gap-1.5 text-sm font-semibold">
                    <RunStatusIcon status={lastRun.status} />
                    Latest Run Result
                  </h3>
                  <span className="rounded-full border border-warning/35 bg-warning/10 px-2 py-0.5 font-mono text-[11px] font-medium text-warning">
                    {lastRun.execution_mode}
                  </span>
                </div>

                <dl className="grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <dt className="text-muted-foreground">Run ID</dt>
                    <dd className="truncate font-mono">{lastRun.run_id}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Status</dt>
                    <dd className="font-semibold">{lastRun.status.replace("_", " ")}</dd>
                  </div>
                </dl>

                {lastRun.status === "failed" && (
                  <div
                    role="alert"
                    className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"
                  >
                    <p className="font-semibold">Run failed</p>
                    {failedExecutions.length > 0 ? (
                      <ul className="mt-1 space-y-0.5">
                        {failedExecutions.map((item) => (
                          <li key={item.node_id}>
                            <span className="font-mono">{item.node_id}</span>
                            {item.logs.length > 0 && `: ${item.logs[item.logs.length - 1]}`}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-1">The server did not report which node failed.</p>
                    )}
                  </div>
                )}

                {waitingGateIds.length > 0 && (
                  <div className="space-y-1.5 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs">
                    <p className="font-medium">Waiting for operator approval:</p>
                    {waitingGateIds.map((nodeId) => (
                      <Button
                        key={nodeId}
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={approveGate.isPending || pendingApprovalNodeId !== null}
                        onClick={() => requestApproval(nodeId)}
                      >
                        <ShieldCheck className="size-3.5" aria-hidden="true" />
                        Review gate {nodeId}
                      </Button>
                    ))}
                  </div>
                )}

                <div>
                  <p className="text-xs font-medium text-muted-foreground">Execution Plan Order</p>
                  <ol className="mt-1.5 flex flex-wrap gap-1.5">
                    {lastRun.execution_plan_order.map((nodeId, idx) => (
                      <li
                        key={nodeId}
                        className="rounded border border-border bg-secondary px-2 py-0.5 font-mono text-[11px]"
                      >
                        {idx + 1}. {nodeId}
                      </li>
                    ))}
                  </ol>
                </div>
              </section>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default WorkflowCanvasPage;

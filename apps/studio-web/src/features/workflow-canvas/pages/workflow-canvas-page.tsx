import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  GitBranch,
  Layers,
  LoaderCircle,
  Play,
  Plus,
  Save,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  useApproveWorkflowGate,
  useCreateWorkflow,
  useExecuteWorkflow,
  useSaveWorkflowLayout,
  type WorkflowCreateResponse,
  type WorkflowExecutionResponse,
  type WorkflowRunRequest,
  type WorkflowRunResponse,
} from "@/features/workflow-canvas/api";
import { authErrorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

interface WorkflowCanvasPageProps {
  projectId: string;
}

type NodeExecutionState = components["schemas"]["NodeExecutionState"];

function isExecutionResponse(run: WorkflowRunResponse): run is WorkflowExecutionResponse {
  return "node_executions" in run;
}

function stateBadgeClass(state: NodeExecutionState | undefined): string {
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

export function WorkflowCanvasPage({ projectId }: WorkflowCanvasPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const createWorkflow = useCreateWorkflow(projectId);
  const saveLayout = useSaveWorkflowLayout(projectId);
  const executeWorkflow = useExecuteWorkflow(projectId);
  const approveGate = useApproveWorkflowGate(projectId);

  const [workflow, setWorkflow] = useState<WorkflowCreateResponse | null>(null);
  const [workflowName, setWorkflowName] = useState("Script to Batch Film Pipeline");
  const [workflowDesc, setWorkflowDesc] = useState(
    "Typed DAG pipeline with Merkle cache keys and human-in-the-loop approval gates",
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [runMode, setRunMode] = useState<WorkflowRunRequest["run_mode"]>("dry_run");
  const [forceRerun, setForceRerun] = useState(false);
  const [confirmFullRun, setConfirmFullRun] = useState(false);
  const [lastRun, setLastRun] = useState<WorkflowRunResponse | null>(null);
  const [layoutPositions, setLayoutPositions] = useState<Record<string, { x: number; y: number }>>(
    {},
  );
  const [layoutSavedMessage, setLayoutSavedMessage] = useState<string | null>(null);

  const nodes = useMemo(() => workflow?.nodes ?? [], [workflow]);
  const edges = useMemo(() => workflow?.edges ?? [], [workflow]);
  const selectedNode = useMemo(
    () => nodes.find((node) => node.node_id === selectedNodeId) ?? nodes[0] ?? null,
    [nodes, selectedNodeId],
  );

  const nodeExecutionById = useMemo(() => {
    const map = new Map<
      string,
      {
        state: NodeExecutionState;
        cache_hit: boolean;
        logs?: string[];
        output_refs?: Record<string, unknown>;
      }
    >();
    if (lastRun && isExecutionResponse(lastRun)) {
      for (const item of lastRun.node_executions) {
        map.set(item.node_id, {
          state: item.state,
          cache_hit: item.cache_hit,
          logs: item.logs,
          output_refs: item.output_refs,
        });
      }
    }
    return map;
  }, [lastRun]);

  const needsStartNode = runMode === "from_here" || runMode === "downstream";
  const effectiveStartNodeId = selectedNode?.node_id ?? null;
  const canExecute =
    Boolean(workflow) &&
    !executeWorkflow.isPending &&
    (!needsStartNode || Boolean(effectiveStartNodeId)) &&
    (runMode !== "full" || confirmFullRun);

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
            disabled={createWorkflow.isPending}
            onClick={() => {
              setLayoutSavedMessage(null);
              createWorkflow.mutate(
                {
                  name: workflowName.trim() || "Script to Batch Film Pipeline",
                  description: workflowDesc.trim() || "M3 Default Typed DAG Workflow",
                  use_default_m3_template: true,
                },
                {
                  onSuccess: (created) => {
                    setWorkflow(created);
                    setSelectedNodeId(created.nodes[0]?.node_id ?? null);
                    setLastRun(null);
                    const initialPositions: Record<string, { x: number; y: number }> = {};
                    created.nodes.forEach((node, index) => {
                      initialPositions[node.node_id] = {
                        x: 60 + (index % 3) * 260,
                        y: 60 + Math.floor(index / 3) * 140,
                      };
                    });
                    setLayoutPositions(initialPositions);
                  },
                },
              );
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
              onClick={() => {
                setLayoutSavedMessage(null);
                saveLayout.mutate(workflow.id, layoutPositions, {
                  onSuccess: (saved) => {
                    setLayoutSavedMessage(
                      `Canvas layout autosaved (v${saved.version_number}) — Merkle cache keys preserved.`,
                    );
                  },
                });
              }}
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

      {createWorkflow.isError && (
        <p role="alert" className="text-xs text-destructive">
          {authErrorMessage(createWorkflow.error, activeAdminToken, "create workflow")}
        </p>
      )}

      {saveLayout.isError && (
        <p role="alert" className="text-xs text-destructive">
          {authErrorMessage(saveLayout.error, activeAdminToken, "save workflow layout")}
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
              Create a project workflow instance via{" "}
              <code className="rounded bg-secondary px-1.5 py-0.5 font-mono text-xs">
                POST /api/v1/projects/{projectId}/workflows
              </code>{" "}
              to inspect topological execution order, node port contracts, Merkle cache keys, and
              interactive approval gates.
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
                  {nodes.length} nodes · {edges.length} edges
                </span>
              </div>
              <span className="font-mono text-xs text-muted-foreground">ID: {workflow.id}</span>
            </div>

            <div
              aria-label="Workflow DAG nodes"
              className="rounded-2xl border border-border bg-secondary/20 p-4"
            >
              <div className="mb-3 flex items-center justify-between text-xs text-muted-foreground">
                <span className="font-medium text-foreground">
                  Topological DAG Pipeline (Click any node to inspect ports, cache key, or run from
                  node)
                </span>
                <span>Layout coordinates tracked independently of Merkle hashes</span>
              </div>

              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {workflow.execution_order.map((nodeId, orderIndex) => {
                  const node = nodes.find((item) => item.node_id === nodeId);
                  if (!node) return null;
                  const isSelected = selectedNode?.node_id === node.node_id;
                  const execState = nodeExecutionById.get(node.node_id);
                  const cacheKey = workflow.cache_keys[node.node_id] ?? "—";
                  const incomingEdges = edges.filter(
                    (edge) => edge.target_node_id === node.node_id,
                  );
                  const outgoingEdges = edges.filter(
                    (edge) => edge.source_node_id === node.node_id,
                  );
                  const coords = layoutPositions[node.node_id] ?? { x: 0, y: 0 };

                  return (
                    <div
                      key={node.node_id}
                      onClick={() => setSelectedNodeId(node.node_id)}
                      className={cn(
                        "cursor-pointer rounded-xl border p-3.5 transition-all",
                        isSelected
                          ? "border-primary bg-card ring-2 ring-primary/20 shadow-sm"
                          : "border-border bg-card hover:border-primary/40",
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-1.5">
                          <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] font-bold text-muted-foreground">
                            #{orderIndex + 1}
                          </span>
                          <span className="font-mono text-xs font-semibold text-foreground">
                            {node.node_id}
                          </span>
                        </div>
                        <span
                          className={cn(
                            "rounded-full border px-2 py-0.5 text-[10px] font-semibold",
                            stateBadgeClass(execState?.state),
                          )}
                        >
                          {execState?.state ?? "ready"}
                        </span>
                      </div>

                      <p className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-primary">
                        <Cpu className="size-3.5" aria-hidden="true" />
                        {node.node_type}
                      </p>

                      <div className="mt-2.5 space-y-1 border-t border-border pt-2 text-[11px] text-muted-foreground">
                        <p className="truncate font-mono">Merkle: {cacheKey.slice(0, 14)}...</p>
                        <p>
                          In: {incomingEdges.length} · Out: {outgoingEdges.length} · Pos ({coords.x}
                          , {coords.y})
                        </p>
                        {execState?.cache_hit && (
                          <p className="font-medium text-success">Merkle Cache Hit</p>
                        )}
                      </div>

                      {execState?.state === "waiting_approval" &&
                        lastRun &&
                        isExecutionResponse(lastRun) && (
                          <div className="mt-3 border-t border-warning/30 pt-2.5">
                            <Button
                              type="button"
                              size="sm"
                              className="w-full"
                              disabled={approveGate.isPending}
                              onClick={(event) => {
                                event.stopPropagation();
                                approveGate.mutate(workflow.id, lastRun.run_id, node.node_id, {
                                  onSuccess: (approved) => {
                                    setLastRun({
                                      run_id: approved.run_id,
                                      status: approved.status,
                                      execution_mode: approved.execution_mode,
                                      execution_plan_order: workflow.execution_order,
                                      node_executions: approved.node_executions.map((item) => ({
                                        node_id: item.node_id,
                                        node_type:
                                          nodes.find((n) => n.node_id === item.node_id)
                                            ?.node_type ?? "Node",
                                        state: item.state,
                                        cache_hit: item.cache_hit,
                                        cache_key: workflow.cache_keys[item.node_id] ?? "",
                                        logs: [],
                                        output_refs: item.output_refs,
                                      })),
                                    });
                                  },
                                });
                              }}
                            >
                              {approveGate.isPending ? (
                                <LoaderCircle
                                  className="size-3.5 animate-spin"
                                  aria-hidden="true"
                                />
                              ) : (
                                <ShieldCheck className="size-3.5" aria-hidden="true" />
                              )}
                              Approve Manual Gate & Resume
                            </Button>
                          </div>
                        )}
                    </div>
                  );
                })}
              </div>
            </div>

            {edges.length > 0 && (
              <div className="rounded-xl border border-border bg-card p-4">
                <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  <Layers className="size-3.5 text-primary" aria-hidden="true" />
                  Typed Port Connections ({edges.length})
                </h3>
                <div className="mt-2.5 flex flex-wrap gap-2">
                  {edges.map((edge) => (
                    <span
                      key={edge.edge_id}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-secondary/50 px-2.5 py-1 font-mono text-xs"
                    >
                      <span className="font-semibold text-foreground">{edge.source_node_id}</span>
                      <span className="text-muted-foreground">.{edge.source_port} →</span>
                      <span className="font-semibold text-primary">{edge.target_node_id}</span>
                      <span className="text-muted-foreground">.{edge.target_port}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
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
                  value={runMode}
                  onChange={(event) => {
                    const nextMode = event.target.value as WorkflowRunRequest["run_mode"];
                    setRunMode(nextMode);
                    if (nextMode !== "full") setConfirmFullRun(false);
                  }}
                  className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="dry_run">dry_run (Planning only — safe default)</option>
                  <option value="from_here">from_here (Start from selected node)</option>
                  <option value="downstream">downstream (Selected node & downstream)</option>
                  <option value="full">full (Execute entire DAG simulation)</option>
                </select>
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
                  {authErrorMessage(executeWorkflow.error, activeAdminToken, "execute workflow")}
                </p>
              )}

              <Button
                type="button"
                className="w-full"
                disabled={!canExecute}
                onClick={() => {
                  if (!workflow) return;
                  executeWorkflow.mutate(
                    workflow.id,
                    {
                      run_mode: runMode,
                      force_rerun: forceRerun,
                      start_node_id: needsStartNode ? effectiveStartNodeId : null,
                    },
                    {
                      onSuccess: (result) => setLastRun(result),
                    },
                  );
                }}
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
                <div className="flex items-center justify-between">
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

                  <div className="grid grid-cols-2 gap-2">
                    <label className="space-y-1">
                      <span className="text-muted-foreground">Canvas X</span>
                      <input
                        type="number"
                        value={layoutPositions[selectedNode.node_id]?.x ?? 0}
                        onChange={(event) =>
                          setLayoutPositions((current) => ({
                            ...current,
                            [selectedNode.node_id]: {
                              x: Number(event.target.value),
                              y: current[selectedNode.node_id]?.y ?? 0,
                            },
                          }))
                        }
                        className="h-8 w-full rounded border border-input bg-background px-2 font-mono text-xs"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-muted-foreground">Canvas Y</span>
                      <input
                        type="number"
                        value={layoutPositions[selectedNode.node_id]?.y ?? 0}
                        onChange={(event) =>
                          setLayoutPositions((current) => ({
                            ...current,
                            [selectedNode.node_id]: {
                              x: current[selectedNode.node_id]?.x ?? 0,
                              y: Number(event.target.value),
                            },
                          }))
                        }
                        className="h-8 w-full rounded border border-input bg-background px-2 font-mono text-xs"
                      />
                    </label>
                  </div>
                </div>
              </section>
            )}

            {lastRun && (
              <section
                aria-label="Execution run summary"
                className="space-y-3 rounded-xl border border-border bg-card p-4"
              >
                <div className="flex items-center justify-between gap-2">
                  <h3 className="flex items-center gap-1.5 text-sm font-semibold">
                    {lastRun.status === "waiting_approval" ? (
                      <AlertTriangle className="size-4 text-warning" aria-hidden="true" />
                    ) : (
                      <CheckCircle2 className="size-4 text-success" aria-hidden="true" />
                    )}
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
                    <dd className="font-semibold">{lastRun.status}</dd>
                  </div>
                </dl>

                <div>
                  <p className="text-xs font-medium text-muted-foreground">Execution Plan Order</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {lastRun.execution_plan_order.map((nodeId, idx) => (
                      <span
                        key={nodeId}
                        className="rounded border border-border bg-secondary px-2 py-0.5 font-mono text-[11px]"
                      >
                        {idx + 1}. {nodeId}
                      </span>
                    ))}
                  </div>
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

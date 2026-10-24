import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { WorkflowCreateResponse } from "@/features/workflow-canvas/api";
import { WorkflowCanvasPage } from "@/features/workflow-canvas/pages/workflow-canvas-page";
import type { components } from "@/lib/api/schema";

type WorkflowExecutionOut = components["schemas"]["WorkflowExecutionOut"];
type WorkflowApprovalOut = components["schemas"]["WorkflowApprovalOut"];

// React Flow measures nodes with ResizeObserver and reads the viewport transform via
// DOMMatrixReadOnly; jsdom implements neither, so install inert stand-ins for this file only.
const originalResizeObserver = globalThis.ResizeObserver;
const originalDOMMatrixReadOnly = globalThis.DOMMatrixReadOnly;

beforeAll(() => {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  class DOMMatrixReadOnlyStub {
    m22 = 1;
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
  globalThis.DOMMatrixReadOnly = DOMMatrixReadOnlyStub as unknown as typeof DOMMatrixReadOnly;
});

afterAll(() => {
  globalThis.ResizeObserver = originalResizeObserver;
  globalThis.DOMMatrixReadOnly = originalDOMMatrixReadOnly;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const BASE = "/api/v1/projects/proj-1/workflows";

const createdWorkflow: WorkflowCreateResponse = {
  id: "wf-1",
  project_id: "proj-1",
  name: "Script to Batch Film Pipeline",
  current_version: 1,
  execution_order: ["n_script_in", "n_approval", "n_render"],
  cache_keys: {
    n_script_in: "merkle-script-1234567890",
    n_approval: "merkle-review-1234567890",
    n_render: "merkle-render-1234567890",
  },
  nodes: [
    { node_id: "n_script_in", node_type: "ScriptInput" },
    { node_id: "n_approval", node_type: "ManualApproval" },
    { node_id: "n_render", node_type: "PromptTemplate" },
  ],
  edges: [
    {
      edge_id: "e1",
      source_node_id: "n_script_in",
      source_port: "script",
      target_node_id: "n_approval",
      target_port: "in",
    },
    {
      edge_id: "e2",
      source_node_id: "n_approval",
      source_port: "out",
      target_node_id: "n_render",
      target_port: "shots",
    },
  ],
  // A genuinely custom (operator-saved) layout: positions do not share a single row.
  ui_layout: { n_script_in: { x: 0, y: 120 }, n_approval: { x: 240, y: 320 } },
};

function execution(node_id: string, state: components["schemas"]["NodeExecutionState"]) {
  return {
    node_id,
    node_type: createdWorkflow.nodes.find((n) => n.node_id === node_id)?.node_type ?? "Node",
    state,
    cache_key: createdWorkflow.cache_keys[node_id] ?? "",
    cache_hit: false,
    logs: [`${node_id} log`],
    output_refs: {},
  };
}

const waitingRun: WorkflowExecutionOut = {
  run_id: "run-1",
  status: "waiting_approval",
  execution_mode: "simulation",
  execution_plan_order: ["n_script_in", "n_approval", "n_render"],
  node_executions: [
    execution("n_script_in", "succeeded"),
    execution("n_approval", "waiting_approval"),
    execution("n_render", "blocked"),
  ],
};

interface MockRoute {
  method: string;
  path: string;
  status: number;
  body: unknown;
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Routes fetch calls by method + path; unmatched calls fail loudly (GET /workflows defaults to 404). */
function mockApi(routes: MockRoute[]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const route = routes.find((item) => item.method === method && item.path === url.pathname);
    if (!route) {
      if (method === "GET" && url.pathname === BASE) {
        return jsonResponse(404, { detail: "Project chưa có Workflow nào." });
      }
      return jsonResponse(500, { detail: `Unmocked ${method} ${url.pathname}` });
    }
    return jsonResponse(route.status, route.body);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestsTo(fetchMock: ReturnType<typeof mockApi>, method: string, path: string) {
  return fetchMock.mock.calls
    .filter(([input, init]) => {
      const url = new URL(String(input), "http://localhost");
      return (init?.method ?? "GET").toUpperCase() === method && url.pathname === path;
    })
    .map(([, init]) => (init?.body ? JSON.parse(String(init.body)) : undefined));
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const ui: ReactNode = <WorkflowCanvasPage projectId="proj-1" />;
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

async function createWorkflow() {
  fireEvent.click(screen.getByRole("button", { name: /Create pipeline workflow/i }));
  await screen.findByRole("region", { name: "Workflow DAG canvas" });
}

function nodeList() {
  return within(screen.getByRole("region", { name: "Workflow nodes" }));
}

describe("WorkflowCanvasPage", () => {
  it("loads an existing project workflow via GET and renders its saved nodes and layout", async () => {
    const fetchMock = mockApi([{ method: "GET", path: BASE, status: 200, body: createdWorkflow }]);
    renderPage();

    await screen.findByRole("region", { name: "Workflow DAG canvas" });
    expect(requestsTo(fetchMock, "GET", BASE)).toEqual([undefined]);
    expect(screen.getByText("Script to Batch Film Pipeline")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(document.querySelectorAll(".react-flow__node")).toHaveLength(3);
  });

  it("renders a retryable alert when loading the project workflow fails with a server error", async () => {
    mockApi([{ method: "GET", path: BASE, status: 503, body: { detail: "Database busy." } }]);
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Không thể tải the project workflow. Kiểm tra Orchestrator và thử lại.",
    );
    expect(within(alert).getByRole("button", { name: /thử lại|retry/i })).toBeInTheDocument();
  });

  it("creates the workflow with a typed POST and renders it on the React Flow canvas", async () => {
    const fetchMock = mockApi([{ method: "POST", path: BASE, status: 201, body: createdWorkflow }]);
    renderPage();

    expect(screen.getByRole("note")).toHaveTextContent(
      /persist on the server and reload automatically/i,
    );
    await createWorkflow();

    expect(requestsTo(fetchMock, "POST", BASE)).toEqual([
      {
        name: "Script to Batch Film Pipeline",
        description:
          "Typed DAG pipeline with Merkle cache keys and human-in-the-loop approval gates",
        use_default_m3_template: true,
      },
    ]);
    expect(screen.getByTestId("workflow-node-n_script_in")).toHaveAttribute(
      "data-state",
      "not_run",
    );
    expect(document.querySelectorAll(".react-flow__node")).toHaveLength(3);
    expect(nodeList().getByRole("button", { name: /n_approval/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Run mode")).toHaveValue("dry_run");
  });

  it("asks for confirmation before replacing an existing workflow", async () => {
    const fetchMock = mockApi([{ method: "POST", path: BASE, status: 201, body: createdWorkflow }]);
    renderPage();
    await createWorkflow();

    fireEvent.click(screen.getByRole("button", { name: /Create a new workflow/i }));
    const dialog = screen.getByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel|hủy/i }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(requestsTo(fetchMock, "POST", BASE)).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: /Create a new workflow/i }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Create new workflow" }),
    );
    await waitFor(() => expect(requestsTo(fetchMock, "POST", BASE)).toHaveLength(2));
  });

  it("runs from the selected node with run_mode from_here and its start_node_id", async () => {
    const fetchMock = mockApi([
      { method: "POST", path: BASE, status: 201, body: createdWorkflow },
      { method: "POST", path: `${BASE}/wf-1/runs`, status: 201, body: waitingRun },
    ]);
    renderPage();
    await createWorkflow();

    fireEvent.click(nodeList().getByRole("button", { name: /n_approval/ }));
    fireEvent.click(screen.getByRole("button", { name: "Run from this node" }));

    await waitFor(() =>
      expect(requestsTo(fetchMock, "POST", `${BASE}/wf-1/runs`)).toEqual([
        { run_mode: "from_here", force_rerun: false, start_node_id: "n_approval" },
      ]),
    );
    await waitFor(() =>
      expect(screen.getByTestId("workflow-node-n_approval")).toHaveAttribute(
        "data-state",
        "waiting_approval",
      ),
    );
  });

  it("requires confirmation to approve a gate and merges the approval into the previous run", async () => {
    const approved: WorkflowApprovalOut = {
      run_id: "run-1",
      status: "succeeded",
      execution_mode: "simulation",
      node_executions: [
        { node_id: "n_approval", state: "succeeded", cache_hit: false, output_refs: {} },
        { node_id: "n_render", state: "succeeded", cache_hit: false, output_refs: {} },
      ],
    };
    const approvePath = `${BASE}/wf-1/runs/run-1/nodes/n_approval/approve`;
    const fetchMock = mockApi([
      { method: "POST", path: BASE, status: 201, body: createdWorkflow },
      {
        method: "POST",
        path: `${BASE}/wf-1/runs`,
        status: 201,
        body: { ...waitingRun, execution_plan_order: ["n_approval", "n_render"] },
      },
      { method: "POST", path: approvePath, status: 200, body: approved },
    ]);
    renderPage();
    await createWorkflow();
    fireEvent.change(screen.getByLabelText("Run mode"), { target: { value: "full" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Confirm full DAG execution/ }));
    fireEvent.click(screen.getByRole("button", { name: "Execute (full)" }));

    const review = await screen.findByRole("button", { name: "Review gate n_approval" });
    fireEvent.click(review);
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: /cancel|hủy/i }),
    );
    expect(requestsTo(fetchMock, "POST", approvePath)).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Approve gate" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Approve & resume" }),
    );

    await waitFor(() => expect(requestsTo(fetchMock, "POST", approvePath)).toHaveLength(1));
    const summary = within(screen.getByRole("region", { name: "Execution run summary" }));
    await waitFor(() => expect(summary.getByText("succeeded")).toBeInTheDocument());
    // Plan order from the run is preserved instead of being replaced by the full DAG order.
    expect(summary.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "1. n_approval",
      "2. n_render",
    ]);
    // Logs from the original run survive the merge.
    expect(screen.getByText("n_approval log")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-node-n_render")).toHaveAttribute("data-state", "succeeded");
  });

  it("shows the server conflict when approving a gate that is no longer waiting", async () => {
    const approvePath = `${BASE}/wf-1/runs/run-1/nodes/n_approval/approve`;
    mockApi([
      { method: "POST", path: BASE, status: 201, body: createdWorkflow },
      { method: "POST", path: `${BASE}/wf-1/runs`, status: 201, body: waitingRun },
      {
        method: "POST",
        path: approvePath,
        status: 409,
        body: { detail: "Node is not waiting_approval." },
      },
    ]);
    renderPage();
    await createWorkflow();
    fireEvent.click(nodeList().getByRole("button", { name: /n_approval/ }));
    fireEvent.click(screen.getByRole("button", { name: "Run from this node" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve gate" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Approve & resume" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Không thể approve the manual gate: Node is not waiting_approval.",
    );
  });

  it("renders a failed run with a destructive failure state instead of success", async () => {
    const failedRun: WorkflowExecutionOut = {
      ...waitingRun,
      status: "failed",
      node_executions: [
        { ...execution("n_script_in", "failed"), logs: ["Script missing"] },
        execution("n_approval", "blocked"),
        execution("n_render", "blocked"),
      ],
    };
    mockApi([
      { method: "POST", path: BASE, status: 201, body: createdWorkflow },
      { method: "POST", path: `${BASE}/wf-1/runs`, status: 201, body: failedRun },
    ]);
    renderPage();
    await createWorkflow();
    fireEvent.click(screen.getByRole("button", { name: "Run from this node" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Run failed");
    expect(alert).toHaveTextContent("n_script_in: Script missing");
    expect(screen.getByTestId("workflow-node-n_script_in")).toHaveAttribute("data-state", "failed");
  });

  it("seeds positions from ui_layout and saves the layout with a PATCH", async () => {
    const layoutPath = `${BASE}/wf-1/layout`;
    const fetchMock = mockApi([
      { method: "POST", path: BASE, status: 201, body: createdWorkflow },
      {
        method: "PATCH",
        path: layoutPath,
        status: 200,
        body: { workflow_id: "wf-1", version_number: 1, ui_layout_saved: true },
      },
    ]);
    renderPage();
    await createWorkflow();
    fireEvent.click(screen.getByRole("button", { name: "Save canvas layout" }));

    await waitFor(() =>
      expect(requestsTo(fetchMock, "PATCH", layoutPath)).toEqual([
        {
          ui_layout: {
            n_script_in: { x: 0, y: 120 },
            n_approval: { x: 240, y: 320 },
            // Missing from ui_layout: falls back to the grid slot for index 2.
            n_render: { x: 560, y: 0 },
          },
        },
      ]),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Canvas layout saved (v1)");
  });

  it("replaces the backend default single-row layout with a wrapped grid in execution order", async () => {
    const layoutPath = `${BASE}/wf-1/layout`;
    const singleRow: WorkflowCreateResponse = {
      ...createdWorkflow,
      // Backend M3 template default: {x: idx * 240, y: 120} for every node.
      ui_layout: {
        n_script_in: { x: 0, y: 120 },
        n_approval: { x: 240, y: 120 },
        n_render: { x: 480, y: 120 },
      },
    };
    const fetchMock = mockApi([
      { method: "POST", path: BASE, status: 201, body: singleRow },
      {
        method: "PATCH",
        path: layoutPath,
        status: 200,
        body: { workflow_id: "wf-1", version_number: 2, ui_layout_saved: true },
      },
    ]);
    renderPage();
    await createWorkflow();
    fireEvent.click(screen.getByRole("button", { name: "Save canvas layout" }));

    await waitFor(() =>
      expect(requestsTo(fetchMock, "PATCH", layoutPath)).toEqual([
        {
          ui_layout: {
            n_script_in: { x: 0, y: 0 },
            n_approval: { x: 280, y: 0 },
            n_render: { x: 560, y: 0 },
          },
        },
      ]),
    );
  });

  it("uses short run mode labels and explains the selected mode in helper text", async () => {
    mockApi([{ method: "POST", path: BASE, status: 201, body: createdWorkflow }]);
    renderPage();
    await createWorkflow();

    const select = screen.getByLabelText("Run mode");
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Dry run (plan only)", "From selected node", "Selected + downstream", "Full DAG"]);
    expect(select).toHaveAccessibleDescription(/Planning only/);
    fireEvent.change(select, { target: { value: "full" } });
    expect(select).toHaveAccessibleDescription(/entire DAG simulation/);
  });

  it("renders create errors with the action error message", async () => {
    mockApi([{ method: "POST", path: BASE, status: 422, body: { detail: "Name is too long." } }]);
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /Create pipeline workflow/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Không thể create the workflow: Name is too long.",
    );
  });
});

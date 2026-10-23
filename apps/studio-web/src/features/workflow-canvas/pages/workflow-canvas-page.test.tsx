import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowCanvasPage } from "@/features/workflow-canvas/pages/workflow-canvas-page";

afterEach(() => cleanup());

function renderWithQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("WorkflowCanvasPage", () => {
  it("renders the simulation banner and initializes an M3 pipeline DAG workflow", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              id: "wf-1",
              project_id: "proj-1",
              name: "Script to Batch Film Pipeline",
              current_version: 1,
              execution_order: ["script_parse", "manual_review", "batch_render"],
              cache_keys: {
                script_parse: "merkle-script-1234567890",
                manual_review: "merkle-review-1234567890",
                batch_render: "merkle-render-1234567890",
              },
              nodes: [
                { node_id: "script_parse", node_type: "ScriptParser" },
                { node_id: "manual_review", node_type: "ManualApproval" },
                { node_id: "batch_render", node_type: "BatchRender" },
              ],
              edges: [
                {
                  edge_id: "e1",
                  source_node_id: "script_parse",
                  source_port: "shots",
                  target_node_id: "manual_review",
                  target_port: "input",
                },
              ],
              ui_layout: {},
            }),
            { status: 201, headers: { "content-type": "application/json" } },
          ),
      ),
    );

    renderWithQueryClient(<WorkflowCanvasPage projectId="proj-1" />);

    expect(screen.getByText("Simulation / Planning Engine")).toBeInTheDocument();
    const initBtn = screen.getByRole("button", { name: /Initialize M3 Pipeline DAG/i });
    fireEvent.click(initBtn);

    expect((await screen.findAllByText("ScriptParser")).length).toBeGreaterThan(0);
    expect(screen.getByText("ManualApproval")).toBeInTheDocument();
    expect(screen.getByLabelText("Run mode")).toHaveValue("dry_run");
  });
});

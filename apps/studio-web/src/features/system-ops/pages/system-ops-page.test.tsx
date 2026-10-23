import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SystemOpsPage } from "@/features/system-ops/pages/system-ops-page";

afterEach(() => cleanup());

function renderWithQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("SystemOpsPage", () => {
  it("renders backups, asset manager, and requires confirmation before orphan deletion", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/backups")) {
          return new Response(JSON.stringify([]), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        if (url.includes("/metrics/summary")) {
          return new Response(
            JSON.stringify({
              projects: { total: 2 },
              connections: { total: 1, accounts_total: 2, by_health: { healthy: 1 } },
              queue: { depth: 0, generation_tasks_total: 4, generation_tasks_by_state: {} },
              assets: { total: 5, total_bytes: 204800 },
              backups: { total: 1, total_bytes: 102400 },
              exports: { total: 1, by_state: { succeeded: 1 } },
              storage: { media_root: "/tmp/media", file_count: 5, total_bytes: 204800 },
              service: {
                name: "muse-orchestrator",
                version: "0.7.0",
                environment: "test",
                started_at: 1700000000,
                uptime_seconds: 120,
              },
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );

    renderWithQueryClient(<SystemOpsPage projectId="proj-1" />);

    expect(
      screen.getByRole("heading", { name: /System Operations, Backups & Asset Maintenance/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete Orphan Files/i })).toBeDisabled();
    expect(await screen.findByText(/v0.7.0/i)).toBeInTheDocument();
  });
});

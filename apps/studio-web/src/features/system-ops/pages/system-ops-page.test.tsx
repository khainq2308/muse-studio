import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SystemOpsPage } from "@/features/system-ops/pages/system-ops-page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderWithQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const metricsBody = {
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
};

const backupBody = {
  id: "bk-1",
  project_id: "proj-1",
  filename: "proj-1-20261024.zip",
  size_bytes: 2048,
  sha256: "abcdef0123456789abcdef",
  created_at: "2026-10-24T10:00:00Z",
};

type Route = (url: string, init: RequestInit | undefined) => Response | undefined;

/** Stub fetch with default read responses; `route` may override any request first. */
function stubFetch(route: Route = () => undefined) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const overridden = route(url, init);
    if (overridden) return overridden;
    if (url.includes("/projects/proj-1/backups")) return json([backupBody]);
    if (url.includes("/metrics/summary")) return json(metricsBody);
    return json([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestsTo(fetchMock: ReturnType<typeof stubFetch>, fragment: string, method: string) {
  return fetchMock.mock.calls.filter(
    ([input, init]) => String(input).includes(fragment) && (init?.method ?? "GET") === method,
  );
}

function cleanupBody(dryRun: boolean, retentionHours: number, errors: string[] = []) {
  return {
    dry_run: dryRun,
    retention_hours: retentionHours,
    scanned_orphans: 2,
    deleted: dryRun ? 0 : 2 - errors.length,
    freed_bytes: dryRun ? 0 : 1024,
    errors,
    orphans: [
      { rel_path: "proj-1/orphan-a.png", size_bytes: 512, age_hours: 30 },
      { rel_path: "proj-2/orphan-b.png", size_bytes: 512, age_hours: 48 },
    ],
  };
}

describe("SystemOpsPage", () => {
  it("renders backups, asset manager, and requires confirmation before orphan deletion", async () => {
    stubFetch();
    renderWithQueryClient(<SystemOpsPage projectId="proj-1" />);

    expect(
      screen.getByRole("heading", { name: /System Operations, Backups & Asset Maintenance/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete Orphan Files/i })).toBeDisabled();
    expect(await screen.findByText(/v0.7.0/i)).toBeInTheDocument();
  });

  it("requires confirming a backup delete and sends nothing on cancel", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch((url, init) =>
      init?.method === "DELETE" && url.includes("/backups/bk-1")
        ? json({ detail: "Backup locked" }, 409)
        : undefined,
    );
    renderWithQueryClient(<SystemOpsPage projectId="proj-1" />);

    await user.click(
      await screen.findByRole("button", { name: "Delete backup proj-1-20261024.zip" }),
    );
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("proj-1-20261024.zip");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(requestsTo(fetchMock, "/backups/bk-1", "DELETE")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Delete backup proj-1-20261024.zip" }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete backup" }),
    );
    expect(await screen.findByText(/Could not delete the backup: Backup locked/)).toBeVisible();
    expect(requestsTo(fetchMock, "/backups/bk-1", "DELETE")).toHaveLength(1);
  });

  it("rejects an out-of-range retention window", async () => {
    const user = userEvent.setup();
    stubFetch();
    renderWithQueryClient(<SystemOpsPage projectId="proj-1" />);

    const retention = screen.getByRole("spinbutton", { name: /Retention window/i });
    await user.clear(retention);
    await user.type(retention, "0");
    expect(screen.getByText(/Retention must be between 1 and 8760 hours/)).toBeVisible();
    expect(screen.getByRole("button", { name: /Scan Orphans/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Dry-Run Cleanup/i })).toBeDisabled();

    await user.clear(retention);
    expect(screen.getByRole("button", { name: /Dry-Run Cleanup/i })).toBeDisabled();

    await user.type(retention, "48");
    expect(screen.queryByText(/Retention must be between/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Dry-Run Cleanup/i })).toBeEnabled();
  });

  it("enables real cleanup only after a dry-run for the same retention window", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch((url, init) => {
      if (!url.includes("/maintenance/orphans/cleanup")) return undefined;
      const body = JSON.parse(String(init?.body)) as { dry_run: boolean; retention_hours: number };
      return json(
        cleanupBody(
          body.dry_run,
          body.retention_hours,
          body.dry_run ? [] : ["proj-2/orphan-b.png: permission denied"],
        ),
      );
    });
    renderWithQueryClient(<SystemOpsPage projectId="proj-1" />);

    const deleteButton = screen.getByRole("button", { name: /Delete Orphan Files/i });
    expect(deleteButton).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /Dry-Run Cleanup/i }));
    const dryRun = await screen.findByTestId("dry-run-report");
    expect(dryRun).toHaveTextContent("proj-1/orphan-a.png");
    expect(dryRun).toHaveTextContent("proj-2/orphan-b.png");
    expect(deleteButton).toBeEnabled();

    const retention = screen.getByRole("spinbutton", { name: /Retention window/i });
    await user.type(retention, "0");
    expect(deleteButton).toBeDisabled();
    expect(screen.queryByTestId("dry-run-report")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Dry-Run Cleanup/i }));
    await screen.findByTestId("dry-run-report");
    await user.click(deleteButton);
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent(/all projects/i);
    await user.click(within(dialog).getByRole("button", { name: "Delete orphan files" }));

    const report = await screen.findByTestId("cleanup-report");
    expect(report).toHaveAttribute("data-warning", "true");
    expect(report).toHaveAttribute("role", "alert");
    expect(report).toHaveTextContent(/Cleanup finished with 1 error/);
    expect(report).toHaveTextContent("proj-2/orphan-b.png: permission denied");
    expect(report).not.toHaveTextContent("Cleanup completed");

    const realCalls = requestsTo(fetchMock, "/maintenance/orphans/cleanup", "POST").filter(
      ([, init]) => (JSON.parse(String(init?.body)) as { dry_run: boolean }).dry_run === false,
    );
    expect(realCalls).toHaveLength(1);
    expect(JSON.parse(String(realCalls[0]?.[1]?.body))).toEqual({
      dry_run: false,
      retention_hours: 240,
    });
  });

  it("confirms a restore and renders its error", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch((url) =>
      url.includes("/backups/restore") ? json({ detail: "Archive is corrupt" }, 422) : undefined,
    );
    renderWithQueryClient(<SystemOpsPage projectId="proj-1" />);

    const file = new File(["zip"], "backup.zip", { type: "application/zip" });
    await user.upload(screen.getByLabelText("Backup ZIP file"), file);
    await user.click(screen.getByRole("button", { name: /Restore ZIP/i }));
    expect(requestsTo(fetchMock, "/backups/restore", "POST")).toHaveLength(0);

    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Restore backup" }),
    );
    expect(
      await screen.findByText(/Could not restore the backup: Archive is corrupt/),
    ).toBeVisible();
    expect(requestsTo(fetchMock, "/backups/restore", "POST")).toHaveLength(1);
  });

  it("highlights skipped rows and neutralized tasks in the restore report", async () => {
    const user = userEvent.setup();
    stubFetch((url) =>
      url.includes("/backups/restore")
        ? json({
            project_id: "proj-9",
            project_name: "Restored",
            report: {
              tables_restored: { shots: 3 },
              rows_skipped: { takes: 1 },
              skipped_rows: [{ table: "takes", original_id: "take-1", reason: "missing shot" }],
              media_restored: 4,
              media_missing: [],
              tasks_neutralized: 2,
              exports_neutralized: 1,
              tasks_with_placeholder_connection: 1,
            },
          })
        : undefined,
    );
    renderWithQueryClient(<SystemOpsPage projectId="proj-1" />);

    await user.upload(
      screen.getByLabelText("Backup ZIP file"),
      new File(["zip"], "backup.zip", { type: "application/zip" }),
    );
    await user.click(screen.getByRole("button", { name: /Restore ZIP/i }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Restore backup" }),
    );

    const report = await screen.findByTestId("restore-report");
    expect(report).toHaveAttribute("data-warning", "true");
    expect(report).toHaveTextContent("Tasks neutralized: 2");
    expect(report).toHaveTextContent("Exports neutralized: 1");
    expect(report).toHaveTextContent("Tasks with placeholder connection: 1");
    expect(report).toHaveTextContent("takes: 1");
    expect(report).toHaveTextContent("takes/take-1: missing shot");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Restore ZIP/i })).toBeDisabled(),
    );
  });

  it("requires confirming an asset delete with forced wording", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch((url, init) =>
      init?.method === "DELETE" && url.includes("/assets/asset-7")
        ? new Response(null, { status: 204 })
        : undefined,
    );
    renderWithQueryClient(<SystemOpsPage projectId="proj-1" />);

    await user.type(screen.getByLabelText("Asset ID to delete"), "asset-7");
    await user.click(screen.getByRole("checkbox", { name: "Force" }));
    await user.click(screen.getByRole("button", { name: /^Delete asset$/ }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent(/force deletes even if referenced by takes\/shots/i);
    expect(dialog).toHaveTextContent(/not scoped to the current project/i);
    expect(requestsTo(fetchMock, "/assets/asset-7", "DELETE")).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Force delete asset" }));
    expect(await screen.findByText("Asset asset-7 was force deleted.")).toBeVisible();
    const calls = requestsTo(fetchMock, "/assets/asset-7", "DELETE");
    expect(calls).toHaveLength(1);
    expect(String(calls[0]?.[0])).toContain("force=true");
  });

  it("shows a metrics load error instead of hiding the summary", async () => {
    stubFetch((url) =>
      url.includes("/metrics/summary") ? json({ detail: "boom" }, 500) : undefined,
    );
    renderWithQueryClient(<SystemOpsPage projectId="proj-1" />);

    expect(await screen.findByText(/Could not load metrics summary/)).toBeVisible();
  });
});

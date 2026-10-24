import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectSettingsPage } from "@/features/projects/pages/project-settings-page";
import { useSessionStore } from "@/lib/stores/session-store";

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    params,
  }: {
    children: ReactNode;
    params?: { projectId: string; module: string };
  }) => <a href={params ? `/projects/${params.projectId}/${params.module}` : "/"}>{children}</a>,
}));

function project(overrides: Record<string, unknown> = {}) {
  return {
    id: "proj_a",
    name: "Neon Horizon",
    description: "",
    aspect_ratio: "16:9",
    fps: 24,
    style_preset: "2d_anime_cinematic",
    language: "vi",
    output_profile: "1080p_h264",
    current_revision_num: 1,
    asset_count: 0,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

interface Recorded {
  method: string;
  url: string;
  body: unknown;
}

function stubApi(options: { createStatus?: number; duplicateStatus?: number } = {}) {
  const calls: Recorded[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, url, body });
      if (method === "POST" && url.endsWith("/api/v1/projects/proj_a/duplicate")) {
        if (options.duplicateStatus && options.duplicateStatus >= 400) {
          return json({ detail: "Original project not found." }, options.duplicateStatus);
        }
        return json(project({ id: "proj_copy", name: "Neon Horizon (Copy)" }), 201);
      }
      if (method === "POST" && url.endsWith("/api/v1/projects")) {
        if (options.createStatus && options.createStatus >= 400) {
          return json({ detail: "Project name cannot be blank." }, options.createStatus);
        }
        return json(project({ id: "proj_new", name: "Glass Citadel" }), 201);
      }
      if (url.includes("/revisions")) return json([]);
      if (url.endsWith("/api/v1/projects")) return json([project()]);
      return json([]);
    }),
  );
  return calls;
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProjectSettingsPage projectId="proj_a" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useSessionStore.setState({ adminToken: "token" });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ProjectSettingsPage", () => {
  it("shows the project profile without workspace-level project creation", async () => {
    stubApi();
    renderPage();

    expect(await screen.findByRole("region", { name: "Active project profile" })).toHaveTextContent(
      "Neon Horizon",
    );
    expect(screen.queryByRole("button", { name: "New project" })).toBeNull();
  });

  it("states that duplication copies only project settings", async () => {
    stubApi();
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Duplicate settings" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Duplicated project settings as Neon Horizon (Copy)");
    expect(status).toHaveTextContent(
      "Only project settings are copied; Story Bible, shots and assets are not copied.",
    );
    expect(status).not.toHaveTextContent(/cloned Story Bible/i);
  });

  it("shows a duplicate mutation error", async () => {
    stubApi({ duplicateStatus: 404 });
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "Duplicate settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not duplicate project: Original project not found.",
    );
  });
});

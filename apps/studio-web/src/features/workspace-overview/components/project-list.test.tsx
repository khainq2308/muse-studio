import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectList } from "@/features/workspace-overview/components/project-list";
import type { components } from "@/lib/api/schema";
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

type Project = components["schemas"]["ProjectOut"];

const BACKEND_ASPECT_RATIO = /^(16:9|9:16|1:1|21:9)$/;

function project(overrides: Partial<Project> = {}): Project {
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

function stubApi(options: { createStatus?: number; duplicateStatus?: number } = {}) {
  const calls: Array<{ method: string; url: string; body: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, url, body });
      if (method === "POST" && url.endsWith("/duplicate")) {
        if (options.duplicateStatus) {
          return json({ detail: "Original project not found." }, options.duplicateStatus);
        }
        return json(project({ id: "proj_copy", name: "Neon Horizon (Copy)" }), 201);
      }
      if (method === "POST" && url.endsWith("/api/v1/projects")) {
        if (options.createStatus) {
          return json(
            [{ loc: ["body", "aspect_ratio"], msg: "String should match pattern" }],
            options.createStatus,
          );
        }
        return json(project({ id: "proj_new", name: "Episode 01" }), 201);
      }
      return json([]);
    }),
  );
  return calls;
}

function renderList() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProjectList
        projects={[project()]}
        isLoading={false}
        isError={false}
        errorMessage=""
        onRetry={() => undefined}
      />
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

describe("ProjectList", () => {
  it("does not offer the unsupported 4:3 aspect ratio or unapplied output profiles", async () => {
    stubApi();
    renderList();
    await userEvent.click(screen.getByRole("button", { name: "New project" }));

    const form = screen.getByRole("form", { name: "Create project" });
    const ratios = within(within(form).getByLabelText("Aspect ratio"))
      .getAllByRole("option")
      .map((option) => option.getAttribute("value") ?? "");
    expect(ratios).not.toContain("4:3");
    for (const ratio of ratios) expect(ratio).toMatch(BACKEND_ASPECT_RATIO);
    expect(within(form).queryByRole("option", { name: "720p_h264" })).toBeNull();
    expect(within(form).getByText(/export does not apply output profiles yet/i)).toBeVisible();
  });

  it("posts a backend-valid body and reports the created project", async () => {
    const calls = stubApi();
    renderList();
    await userEvent.click(screen.getByRole("button", { name: "New project" }));
    const form = screen.getByRole("form", { name: "Create project" });
    await userEvent.type(within(form).getByLabelText("Project name"), "Episode 01");
    await userEvent.selectOptions(within(form).getByLabelText("Aspect ratio"), "9:16");
    await userEvent.click(within(form).getByRole("button", { name: "Create project" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Created project Episode 01 (proj_new).");
    expect(status).not.toHaveTextContent(/duplicat/i);

    const post = calls.find((call) => call.method === "POST");
    expect(post?.body).toMatchObject({
      name: "Episode 01",
      aspect_ratio: "9:16",
      fps: 24,
      output_profile: "1080p_h264",
    });
    expect((post?.body as { aspect_ratio: string }).aspect_ratio).toMatch(BACKEND_ASPECT_RATIO);
  });

  it("reports that duplicating copies only project settings", async () => {
    const calls = stubApi();
    renderList();
    await userEvent.click(
      screen.getByRole("button", { name: "Duplicate settings of Neon Horizon" }),
    );

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Duplicated project settings as Neon Horizon (Copy)");
    expect(status).toHaveTextContent("Story Bible, shots and assets are not copied.");
    expect(calls.some((call) => call.url.endsWith("/api/v1/projects/proj_a/duplicate"))).toBe(true);
  });

  it("shows create and duplicate errors instead of a success message", async () => {
    stubApi({ createStatus: 422, duplicateStatus: 404 });
    renderList();

    await userEvent.click(
      screen.getByRole("button", { name: "Duplicate settings of Neon Horizon" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not duplicate project: Original project not found.",
    );

    await userEvent.click(screen.getByRole("button", { name: "New project" }));
    const form = screen.getByRole("form", { name: "Create project" });
    await userEvent.type(within(form).getByLabelText("Project name"), "Episode 01");
    await userEvent.click(within(form).getByRole("button", { name: "Create project" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Could not create project: String should match pattern",
    );
    expect(screen.queryByRole("status")).toBeNull();
  });
});

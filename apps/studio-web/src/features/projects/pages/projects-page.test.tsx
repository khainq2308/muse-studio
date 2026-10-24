import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PROJECT_ASPECT_RATIO_OPTIONS } from "@/entities/project/options";
import { ProjectsPage } from "@/features/projects/pages/projects-page";
import { useSessionStore } from "@/lib/stores/session-store";

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    params,
    "aria-label": ariaLabel,
  }: {
    children: ReactNode;
    params?: { projectId: string; module: string };
    "aria-label"?: string;
  }) => (
    <a
      href={params ? `/projects/${params.projectId}/${params.module}` : "/"}
      aria-label={ariaLabel}
    >
      {children}
    </a>
  ),
}));

const BACKEND_ASPECT_RATIO = /^(16:9|9:16|1:1|21:9)$/;

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
      <ProjectsPage />
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

describe("ProjectsPage", () => {
  it("only offers aspect ratios the backend accepts", async () => {
    stubApi();
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "New project" }));

    const form = screen.getByRole("form", { name: "Create new project" });
    const options = within(within(form).getByLabelText("Aspect ratio"))
      .getAllByRole("option")
      .map((option) => option.getAttribute("value"));
    expect(options).toEqual([...PROJECT_ASPECT_RATIO_OPTIONS]);
    for (const value of options) expect(value).toMatch(BACKEND_ASPECT_RATIO);
    expect(within(form).queryByRole("option", { name: "4k_h264" })).toBeNull();
    expect(within(form).getByText(/export does not apply output profiles yet/i)).toBeVisible();
  });

  it("creates a project with a valid body and points to the script as the first step", async () => {
    const calls = stubApi();
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "New project" }));
    const form = screen.getByRole("form", { name: "Create new project" });
    await userEvent.type(within(form).getByLabelText("Project name"), "Glass Citadel");
    await userEvent.selectOptions(within(form).getByLabelText("Aspect ratio"), "21:9");
    await userEvent.click(within(form).getByRole("button", { name: "Create project" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Created project Glass Citadel (proj_new).");
    expect(within(status).getByRole("link", { name: /Start with the script/ })).toHaveAttribute(
      "href",
      "/projects/proj_new/script-studio",
    );

    const post = calls.find((call) => call.method === "POST" && call.url.endsWith("/projects"));
    expect(post?.body).toEqual({
      name: "Glass Citadel",
      description: "",
      aspect_ratio: "21:9",
      fps: 24,
      language: "vi",
      style_preset: "2d_anime_cinematic",
      output_profile: "1080p_h264",
    });
    expect((post?.body as { aspect_ratio: string }).aspect_ratio).toMatch(BACKEND_ASPECT_RATIO);
  });

  it("lists projects most recently updated first, each opening inside the project", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json([
          project({ id: "proj_old", name: "Old Cut", updated_at: "2026-09-01T00:00:00Z" }),
          project({ id: "proj_new", name: "New Cut", updated_at: "2026-10-20T00:00:00Z" }),
        ]),
      ),
    );
    renderPage();

    const list = await screen.findByRole("region", { name: "All projects" });
    const opens = await within(list).findAllByRole("link", { name: /^Open / });
    expect(opens[0]).toHaveAccessibleName("Open New Cut");
    expect(opens[0]).toHaveAttribute("href", "/projects/proj_new/shot-board");
    expect(within(list).getAllByRole("link", { name: "Settings" })[1]).toHaveAttribute(
      "href",
      "/projects/proj_old/settings",
    );
  });

  it("shows a create mutation error inside the form", async () => {
    stubApi({ createStatus: 422 });
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "New project" }));
    const form = screen.getByRole("form", { name: "Create new project" });
    await userEvent.type(within(form).getByLabelText("Project name"), "X");
    await userEvent.click(within(form).getByRole("button", { name: "Create project" }));
    await waitFor(() =>
      expect(within(form).getByRole("alert")).toHaveTextContent(
        "Could not create project: Project name cannot be blank.",
      ),
    );
    expect(screen.queryByRole("status")).toBeNull();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routeTree } from "@/app/router";
import { makeConnection, makeShot } from "@/features/shot-board/test-fixtures";
import { useSessionStore } from "@/lib/stores/session-store";

// The real route tree with an in-memory history; only fetch is stubbed. Covers the
// workspace/project split: workspace routes, legacy redirects and overview next steps.

const LINT_BLOCKER = {
  code: "MISSING_REF",
  severity: "blocker" as const,
  message: "Reference missing",
  field: null,
};

function project(id: string, name: string, updatedAt: string) {
  return {
    id,
    name,
    description: "",
    aspect_ratio: "16:9",
    fps: 24,
    style_preset: "2d_anime_cinematic",
    language: "vi",
    output_profile: "1080p_h264",
    current_revision_num: 1,
    asset_count: 0,
    created_at: updatedAt,
    updated_at: updatedAt,
  };
}

const PROJECTS = [
  project("project-old", "Old Cut", "2026-09-01T00:00:00Z"),
  project("project-1", "Neon Horizon", "2026-10-20T00:00:00Z"),
];

const SHOTS: Record<string, unknown[]> = {
  "project-1": [
    makeShot({ id: "shot-1", sequence_index: 1, title: "Hero enters", selected_take_id: "t-1" }),
    makeShot({ id: "shot-2", sequence_index: 2, title: "Chase", lint_report: [LINT_BLOCKER] }),
  ],
  "project-old": [],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubApi() {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const { pathname } = new URL(String(input));
    if (pathname === "/healthz") return json(200, { status: "ok" });
    if (pathname === "/api/v1/projects") return json(200, PROJECTS);
    const shots = /^\/api\/v1\/projects\/([^/]+)\/shots$/.exec(pathname);
    if (shots?.[1] && shots[1] in SHOTS) return json(200, SHOTS[shots[1]]);
    if (pathname === "/api/v1/connections") return json(200, [makeConnection()]);
    if (/^\/api\/v1\/queue\/shots\/[^/]+\/takes$/.test(pathname)) return json(200, []);
    return json(404, { detail: "Not mocked" });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mount(url: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  useSessionStore.getState().setAdminToken("test-token");
  // jsdom does not implement scrolling; the router restores scroll on navigation.
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useSessionStore.getState().clearAdminToken();
});

describe("Workspace navigation", () => {
  it("shows the next step of the most recent project and opens the blocked shot's lint tab", async () => {
    stubApi();
    const user = userEvent.setup();
    const router = mount("/");

    const recent = await screen.findByRole("list", { name: "Recently updated projects" });
    const cards = within(recent).getAllByRole("listitem");
    expect(cards.map((card) => card.getAttribute("aria-label"))).toEqual([
      "Neon Horizon",
      "Old Cut",
    ]);

    const active = cards[0]!;
    expect(await within(active).findByText("Resolve 1 blocked shot")).toBeVisible();
    expect(within(active).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
    expect(await within(cards[1]!).findByText("Import a script")).toBeVisible();

    await user.click(within(active).getByRole("link", { name: /Open first blocked shot/ }));
    await waitFor(() =>
      expect(router.state.location.href).toBe(
        "/projects/project-1/shot-board?shot=shot-2&tab=prompt",
      ),
    );
    const panel = await screen.findByTestId("shot-workspace-shot-2");
    expect(await within(panel).findByRole("tab", { name: /^Prompt & lint/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("limits progress to the four most recent projects and isolates a failing card", async () => {
    const many = [
      project("p1", "One", "2026-10-01T00:00:00Z"),
      project("p2", "Two", "2026-10-02T00:00:00Z"),
      project("p3", "Three", "2026-10-03T00:00:00Z"),
      project("p4", "Four", "2026-10-04T00:00:00Z"),
      project("p5", "Five", "2026-10-05T00:00:00Z"),
    ];
    let threeFails = true;
    const shotRequests: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input) => {
        const { pathname } = new URL(String(input));
        if (pathname === "/healthz") return json(200, { status: "ok" });
        if (pathname === "/api/v1/projects") return json(200, many);
        if (pathname === "/api/v1/connections") return json(200, [makeConnection()]);
        const shots = /^\/api\/v1\/projects\/([^/]+)\/shots$/.exec(pathname);
        if (shots?.[1]) {
          shotRequests.push(shots[1]);
          if (shots[1] === "p3" && threeFails) return json(500, { detail: "boom" });
          return json(200, []);
        }
        return json(404, { detail: "Not mocked" });
      }),
    );
    const user = userEvent.setup();
    mount("/");

    const recent = await screen.findByRole("list", { name: "Recently updated projects" });
    expect(
      within(recent)
        .getAllByRole("listitem")
        .map((card) => card.getAttribute("aria-label")),
    ).toEqual(["Five", "Four", "Three", "Two"]);
    expect(screen.getByRole("link", { name: "View all 5 projects" })).toHaveAttribute(
      "href",
      "/projects",
    );
    // The oldest project is not loaded at all.
    await waitFor(() => expect(new Set(shotRequests)).toEqual(new Set(["p5", "p4", "p3", "p2"])));

    const failing = within(recent).getByRole("listitem", { name: "Three" });
    expect(await within(failing).findByRole("alert")).toBeVisible();
    for (const name of ["Five", "Four", "Two"]) {
      expect(
        await within(within(recent).getByRole("listitem", { name })).findByText("Import a script"),
      ).toBeVisible();
    }

    threeFails = false;
    const before = shotRequests.length;
    await user.click(
      within(failing).getByRole("button", { name: "Retry loading progress for Three" }),
    );
    expect(await within(failing).findByText("Import a script")).toBeVisible();
    expect(shotRequests.slice(before)).toEqual(["p3"]);
  });

  it("keeps workspace tools in the header and marks the current one", async () => {
    stubApi();
    mount("/system");

    const nav = await screen.findByRole("navigation", { name: "Workspace" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Overview",
      "Projects",
      "Connections",
      "System",
    ]);
    expect(within(nav).getByRole("link", { name: "System" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "System & maintenance" })).toBeVisible(),
    );
    expect(screen.queryByRole("navigation", { name: "Project modules" })).toBeNull();
  });

  it.each([
    ["/projects/project-1/connections", "/connections"],
    ["/projects/project-1/system-ops", "/projects/project-1/settings"],
    ["/projects/project-1/projects", "/projects/project-1/settings"],
    ["/projects/project-1", "/projects/project-1/shot-board"],
  ])("redirects the pre-split URL %s to %s", async (from, to) => {
    stubApi();
    const router = mount(from);
    await waitFor(() => expect(router.state.location.pathname).toBe(to));
  });

  it("opens project settings with the project's backups, inside the project navigation", async () => {
    stubApi();
    mount("/projects/project-1/settings");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Project settings" })).toBeVisible(),
    );
    expect(screen.getByRole("heading", { name: "Backups & assets" })).toBeVisible();
    const nav = screen.getByRole("navigation", { name: "Project modules" });
    expect(within(nav).getByRole("link", { name: "Project Settings" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    // Only the open module is the current page; the header and breadcrumb links to the
    // project list are not.
    const current = document.querySelectorAll('[aria-current="page"]');
    expect([...current].map((element) => element.textContent)).toEqual([
      "Neon Horizon",
      "Project Settings",
    ]);
    const workspace = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(workspace).getByRole("link", { name: "Projects" })).not.toHaveAttribute(
      "aria-current",
    );
  });
});

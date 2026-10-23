import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routeTree } from "@/app/router";
import { makeConnection, makeShot } from "@/features/shot-board/test-fixtures";
import { useSessionStore } from "@/lib/stores/session-store";

// The real route tree with an in-memory history: proves `?shot=&tab=` is validated, read and
// written by the router. Only fetch is stubbed.

const shots = [
  makeShot({ id: "shot-1", sequence_index: 1, title: "Hero enters" }),
  makeShot({ id: "shot-2", sequence_index: 2, title: "Chase" }),
  makeShot({ id: "shot-3", scene_id: "scene-2", sequence_index: 3, title: "Rooftop" }),
];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const { pathname } = new URL(String(input));
      if (pathname === "/healthz") return json(200, { status: "ok" });
      if (pathname === "/api/v1/projects") {
        return json(200, [{ id: "project-1", name: "Project one" }]);
      }
      if (pathname === "/api/v1/projects/project-1/shots") return json(200, shots);
      if (pathname === "/api/v1/connections") return json(200, [makeConnection()]);
      if (/^\/api\/v1\/queue\/shots\/[^/]+\/takes$/.test(pathname)) return json(200, []);
      return json(404, { detail: "Not mocked" });
    }),
  );
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
  stubApi();
  // jsdom does not implement scrolling; the router restores scroll on navigation.
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useSessionStore.getState().clearAdminToken();
});

describe("Shot Board URL state", () => {
  it("deep-links a shot and tab, and keeps the URL in sync with the rail and tabs", async () => {
    const user = userEvent.setup();
    const router = mount("/projects/project-1/shot-board?shot=shot-2&tab=prompt");

    const panel = await screen.findByTestId("shot-workspace-shot-2");
    expect(within(panel).getByRole("tab", { name: "Prompt & lint" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("shot-card-shot-2")).toHaveAttribute("aria-current", "true");

    await user.click(screen.getByTestId("shot-card-shot-3"));
    await waitFor(() => expect(router.state.location.search).toEqual({ shot: "shot-3" }));
    expect(router.state.location.pathname).toBe("/projects/project-1/shot-board");
    expect(router.state.location.href).toBe("/projects/project-1/shot-board?shot=shot-3");
    expect(await screen.findByTestId("shot-workspace-shot-3")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Details" }));
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ shot: "shot-3", tab: "details" }),
    );

    // Browser Back returns to the previously selected shot (rail clicks push history).
    act(() => router.history.back());
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ shot: "shot-2", tab: "prompt" }),
    );
    expect(await screen.findByTestId("shot-workspace-shot-2")).toBeInTheDocument();
  });

  it("drops invalid search values and does not carry them to another module", async () => {
    const user = userEvent.setup();
    const router = mount("/projects/project-1/shot-board?tab=storyboard");

    const panel = await screen.findByTestId("shot-workspace-shot-1");
    // validateSearch drops the unknown tab, so the default tab is used.
    expect(router.state.matches[router.state.matches.length - 1]?.search).toEqual({});
    expect(within(panel).getByRole("tab", { name: "Details" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    await user.click(screen.getAllByRole("link", { name: "Open Queue Monitor" })[0]!);
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/projects/project-1/queue-monitor"),
    );
    expect(router.state.location.search).toEqual({});
  });
});

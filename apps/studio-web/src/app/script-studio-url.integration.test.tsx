import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routeTree } from "@/app/router";
import { useSessionStore } from "@/lib/stores/session-store";

// The real route tree with an in-memory history: proves `?view=` is validated, read and written
// by the router for Script & Story Bible. Only fetch is stubbed.

const ENTRY = {
  id: "bib_1",
  entry_type: "character",
  code_name: "char_linh",
  display_name: "Detective Linh",
  immutable_traits: "",
  mutable_attributes: {},
  reference_asset_ids: ["ast_1"],
  requires_reference_lock: true,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
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
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const { pathname } = new URL(String(input));
      if (pathname === "/healthz") return json(200, { status: "ok" });
      if (pathname === "/api/v1/projects") return json(200, [{ id: "project-1", name: "One" }]);
      if (pathname === "/api/v1/projects/project-1/story-bible") return json(200, [ENTRY]);
      if (pathname === "/api/v1/projects/project-1/shots") return json(200, []);
      if (pathname === "/api/v1/connections") return json(200, []);
      return json(404, { detail: "Not mocked" });
    }),
  );
  // jsdom does not implement scrolling; the router restores scroll on navigation.
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useSessionStore.getState().clearAdminToken();
});

describe("Script & Story Bible URL state", () => {
  it("deep-links a view, and writes the selected view back to the URL", async () => {
    const user = userEvent.setup();
    const router = mount("/projects/project-1/script-studio?view=script");

    // The URL wins over the data-derived default (this project has entries).
    expect(
      await screen.findByRole("tab", { name: /Script import|Nhập kịch bản/i }),
    ).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("tab", { name: /^Story Bible/i }));
    await waitFor(() =>
      expect(router.state.location.href).toBe("/projects/project-1/script-studio?view=bible"),
    );
    expect(await screen.findByTestId("story-bible-entry-char_linh")).toBeVisible();

    // Browser back returns to the previous view.
    router.history.back();
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Script import|Nhập kịch bản/i })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(router.state.location.search.view).toBe("script");
  });

  it("drops an unknown view and does not carry the view to other modules", async () => {
    const user = userEvent.setup();
    const router = mount("/projects/project-1/script-studio?view=bogus");

    // Falls back to the data-derived default: the project has a Story Bible.
    expect(await screen.findByRole("tab", { name: /^Story Bible/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // The unknown value is dropped and the derived default written back, replacing the entry.
    await waitFor(() => expect(router.state.location.search.view).toBe("bible"));
    expect(router.state.location.href).toBe("/projects/project-1/script-studio?view=bible");

    const nav = screen.getByRole("navigation", { name: /Project modules|Module dự án/i });
    await user.click(within(nav).getByRole("link", { name: "Shot Board" }));
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/projects/project-1/shot-board"),
    );
    expect(router.state.location.search.view).toBeUndefined();
  });
});

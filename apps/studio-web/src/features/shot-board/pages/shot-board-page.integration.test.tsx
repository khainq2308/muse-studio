import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => <a href="/">{children as string}</a>,
}));

import type { ShotBoardSearch } from "@/features/shot-board/search";
import {
  makeConnection,
  makeEnqueuedTask,
  makeReview,
  makeShot,
  makeTake,
  makeTaskPage,
  makeQueueTask,
} from "@/features/shot-board/test-fixtures";
import { useGenerationSettingsStore } from "@/features/shot-board/generation-settings-store";
import { chooseConnection } from "@/features/shot-board/test-actions";
import { ShotBoardHarness } from "@/features/shot-board/test-harness";
import { useSessionStore } from "@/lib/stores/session-store";

// Exercises the real hooks and API client; only fetch is stubbed.

interface RecordedRequest {
  method: string;
  pathname: string;
  body: unknown;
}

type Handler = (request: RecordedRequest) => { status: number; body: unknown } | undefined;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubApi(handler: Handler) {
  const requests: RecordedRequest[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input));
      const request: RecordedRequest = {
        method: init?.method ?? "GET",
        pathname: url.pathname,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      };
      requests.push(request);
      const handled = handler(request);
      // The board polls the project's recent tasks; default to an empty page (the real shape)
      // unless the test answers that request itself.
      const response =
        request.method === "GET" &&
        request.pathname === "/api/v1/queue/tasks" &&
        (!handled || (!(handled instanceof Response) && Array.isArray(handled.body)))
          ? { status: 200, body: makeTaskPage() }
          : handled;
      if (!response) return json(404, { detail: "Not mocked", category: "not_found" });
      return json(response.status, response.body);
    }),
  );
  return requests;
}

const shots = [
  makeShot({ id: "shot-1", sequence_index: 1, title: "Hero enters" }),
  makeShot({
    id: "shot-2",
    sequence_index: 2,
    title: "Chase",
    continuity_mode: "frame_chain",
    previous_shot_id: "shot-1",
    first_frame_asset_id: "asset-ff",
    approval_state: "needs_review",
    generation_state: "blocked_upstream_frame",
  }),
  makeShot({ id: "shot-3", scene_id: "scene-2", sequence_index: 3, title: "Rooftop" }),
];

function mount(initialSearch: ShotBoardSearch = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onNavigate = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <ShotBoardHarness
        projectId="project-1"
        initialSearch={initialSearch}
        onNavigate={onNavigate}
      />
    </QueryClientProvider>,
  );
  return { onNavigate };
}

/** Shots and connections; every shot has no takes unless `takes` says otherwise. */
function boardHandler(takes: Record<string, unknown[]> = {}): Handler {
  return ({ method, pathname }) => {
    if (pathname === "/api/v1/projects/project-1/shots") return { status: 200, body: shots };
    if (pathname === "/api/v1/connections") return { status: 200, body: [makeConnection()] };
    const match = /^\/api\/v1\/queue\/shots\/([^/]+)\/takes$/.exec(pathname);
    if (method === "GET" && match) return { status: 200, body: takes[match[1]!] ?? [] };
    return undefined;
  };
}

beforeEach(() => {
  useGenerationSettingsStore.setState({ byProject: {} });
  useSessionStore.getState().setAdminToken("test-token");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useSessionStore.getState().clearAdminToken();
});

describe("ShotBoardPage", () => {
  it("lists shots by scene in the navigator with scannable status and opens one in the panel", async () => {
    const user = userEvent.setup();
    stubApi(boardHandler());
    mount();

    const sceneOne = await screen.findByTestId("shot-scene-scene-1");
    expect(within(sceneOne).getAllByRole("listitem")).toHaveLength(2);
    expect(within(screen.getByTestId("shot-scene-scene-2")).getAllByRole("listitem")).toHaveLength(
      1,
    );

    const chase = screen.getByTestId("shot-card-shot-2");
    expect(chase).toHaveAccessibleName("Shot 2 · Chase");
    expect(within(chase).getByTestId("shot-generation-state")).toHaveTextContent(
      "Blocked: upstream frame missing",
    );
    expect(within(chase).getByTestId("shot-approval-state")).toHaveTextContent("Needs review");
    expect(within(chase).getByTestId("shot-continuity")).toHaveTextContent("Frame chain");
    // The first shot is open by default.
    expect(screen.getByTestId("shot-card-shot-1")).toHaveAttribute("aria-current", "true");
    expect(chase).not.toHaveAttribute("aria-current");
    expect(screen.getByTestId("shot-workspace-shot-1")).toBeInTheDocument();

    await user.click(chase);
    const panel = await screen.findByTestId("shot-workspace-shot-2");
    expect(chase).toHaveAttribute("aria-current", "true");
    expect(
      within(panel).getByRole("heading", { level: 3, name: "Shot 2 · Chase" }),
    ).toBeInTheDocument();
    expect(within(panel).getByTestId("shot-generation-state")).toHaveTextContent(
      "Blocked: upstream frame missing",
    );
    // No takes: the panel opens on Details, which shows the stored fields read-only.
    expect(await within(panel).findByRole("tab", { name: "Details" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await waitFor(() =>
      expect(within(panel).getByRole("tab", { name: /^Takes/ })).toHaveTextContent("Takes0"),
    );
    const details = within(panel).getByTestId("shot-details");
    expect(within(details).getByTestId("shot-first-frame")).toHaveTextContent("asset-ff");
    expect(details).toHaveTextContent("Frame chain");
    expect(details).toHaveTextContent("Shot 1 · Hero enters");
    expect(within(panel).queryByTestId("shot-predecessor-warning")).not.toBeInTheDocument();

    // No dead Storyboard/Shot Editor toggle, and no implied soundtrack.
    expect(screen.queryByRole("button", { name: "Storyboard" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Shot Editor" })).not.toBeInTheDocument();
    // Module links live in the project navigation, not repeated inside the page.
    expect(screen.queryByRole("navigation", { name: "Related modules" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Soundtrack/)).not.toBeInTheDocument();
  });

  it("explains a missing admin token and shows the empty state when there are no shots", async () => {
    useSessionStore.getState().clearAdminToken();
    stubApi(({ pathname }) =>
      pathname === "/api/v1/projects/project-1/shots"
        ? { status: 401, body: { detail: "Unauthorized", category: "auth", retryable: false } }
        : { status: 200, body: [] },
    );
    mount();

    expect(await screen.findByText("Admin token required to read shots.")).toBeInTheDocument();
    cleanup();

    stubApi(() => ({ status: 200, body: [] }));
    mount();
    expect(await screen.findByText("No shots yet")).toBeInTheDocument();
  });

  it("maps a 422 PATCH response to the field and then shows the recompiled prompt", async () => {
    const user = userEvent.setup();
    let patchCount = 0;
    const base = boardHandler();
    const requests = stubApi((request) => {
      const { method, pathname } = request;
      if (pathname === "/api/v1/connections") return { status: 200, body: [] };
      if (method === "PATCH" && pathname === "/api/v1/projects/project-1/shots/shot-1") {
        patchCount += 1;
        return patchCount === 1
          ? {
              status: 422,
              body: {
                detail: [
                  {
                    type: "less_than_equal",
                    loc: ["body", "duration_seconds"],
                    msg: "Input should be less than or equal to 600",
                  },
                ],
                category: "validation",
                retryable: false,
              },
            }
          : { status: 200, body: makeReview({ id: "shot-1" }) };
      }
      return base(request);
    });
    mount();

    await user.click(await screen.findByRole("button", { name: "Edit Shot 1 · Hero enters" }));
    const panel = screen.getByTestId("shot-edit-panel-shot-1");
    const duration = within(panel).getByLabelText("Duration (seconds)");
    await user.clear(duration);
    await user.type(duration, "8");
    await user.click(within(panel).getByRole("button", { name: "Save shot" }));

    expect(
      await within(panel).findByText("Input should be less than or equal to 600"),
    ).toBeInTheDocument();
    expect(requests.find((request) => request.method === "PATCH")?.body).toEqual({
      duration_seconds: 8,
    });

    await user.clear(duration);
    await user.type(duration, "9");
    await user.click(within(panel).getByRole("button", { name: "Save shot" }));
    expect(await within(panel).findByTestId("shot-review-prompt")).toHaveTextContent(
      "close-up handheld shot of the hero running",
    );
    const patches = requests.filter((request) => request.method === "PATCH");
    expect(patches[patches.length - 1]?.body).toEqual({
      duration_seconds: 9,
    });
  });

  it("confirms and enqueues one shot, reporting a deduplicated request honestly", async () => {
    const user = userEvent.setup();
    const base = boardHandler();
    const requests = stubApi((request) => {
      if (request.method === "POST" && request.pathname === "/api/v1/queue/tasks") {
        return {
          status: 201,
          body: makeEnqueuedTask({ id: "task-old", state: "failed", created_new: false }),
        };
      }
      // Two connections, so nothing is chosen until the user picks one.
      if (request.pathname === "/api/v1/connections") {
        return {
          status: 200,
          body: [
            makeConnection(),
            makeConnection({ id: "conn-live", name: "Live gateway", provider_type: "muse2api" }),
          ],
        };
      }
      return base(request);
    });
    mount();

    const generate = await screen.findByRole("button", { name: "Generate Shot 1 · Hero enters" });
    expect(generate).toBeDisabled();
    await chooseConnection(user, "conn-mock");
    expect(screen.getByLabelText("Model")).toHaveValue("muse-video-mock-v1");
    expect(screen.getByTestId("connection-cost-label")).toHaveTextContent(
      "Mock provider: no provider cost.",
    );

    await user.click(generate);
    // The confirmation opens inside the selected shot's panel, under its header.
    const workspace = screen.getByTestId("shot-workspace-shot-1");
    const dialog = within(workspace).getByRole("alertdialog");
    expect(dialog).toHaveTextContent("Queue video generation for Shot 1 · Hero enters?");
    expect(dialog).toHaveTextContent("Mock provider (no provider cost)");
    await user.click(within(dialog).getByRole("button", { name: "Confirm and queue" }));

    const result = await within(workspace).findByTestId("shot-enqueue-result");
    expect(result).toHaveTextContent("matched an existing task task-old (state: failed)");
    expect(result).toHaveTextContent("No new generation was started.");
    await waitFor(() =>
      expect(requests.find((request) => request.method === "POST")?.body).toEqual({
        shot_id: "shot-1",
        connection_id: "conn-mock",
        model_id: "muse-video-mock-v1",
        task_type: "video",
        priority: 10,
        max_polls: 60,
        force_retry: false,
      }),
    );
  });
});

describe("ShotBoardPage workspace navigation", () => {
  it("opens the shot named by the URL and writes rail and tab choices back to it", async () => {
    const user = userEvent.setup();
    stubApi(boardHandler({ "shot-3": [makeTake({ id: "take-9", shot_id: "shot-3" })] }));
    const { onNavigate } = mount({ shot: "shot-3" });

    const panel = await screen.findByTestId("shot-workspace-shot-3");
    expect(screen.getByTestId("shot-card-shot-3")).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("shot-card-shot-1")).not.toHaveAttribute("aria-current");
    // The shot has a take, so the panel opens on Takes.
    await waitFor(() =>
      expect(within(panel).getByRole("tab", { name: /^Takes/ })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(await within(panel).findByTestId("take-take-9")).toBeInTheDocument();

    await user.click(within(panel).getByRole("tab", { name: /^Prompt & lint/ }));
    expect(onNavigate).toHaveBeenLastCalledWith(
      { shot: "shot-3", tab: "prompt" },
      { replace: true },
    );
    expect(within(panel).getByTestId("shot-prompt-final")).toHaveTextContent(
      "wide static shot of the hero walking into the hall",
    );
    expect(within(panel).getByText("Prompt Linter: no issues")).toBeInTheDocument();

    await user.click(screen.getByTestId("shot-card-shot-2"));
    // Selecting a shot pushes a history entry and resets the tab to the shot's default.
    expect(onNavigate).toHaveBeenLastCalledWith({ shot: "shot-2" }, undefined);
    const chase = await screen.findByTestId("shot-workspace-shot-2");
    await waitFor(() =>
      expect(within(chase).getByRole("tab", { name: "Details" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
  });

  it("explains an unknown shot ID from the URL and falls back to the first shot", async () => {
    stubApi(boardHandler());
    mount({ shot: "shot-deleted", tab: "details" });

    expect(await screen.findByTestId("shot-workspace-shot-1")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(
      "Shot shot-deleted is not in this project; showing the first shot instead.",
    );
    expect(screen.getByRole("tab", { name: "Details" })).toHaveAttribute("aria-selected", "true");
  });

  it("moves focus through the navigator with the keyboard and opens the focused shot", async () => {
    const user = userEvent.setup();
    stubApi(boardHandler());
    const { onNavigate } = mount();

    const first = await screen.findByTestId("shot-card-shot-1");
    first.focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByTestId("shot-card-shot-2")).toHaveFocus();
    await user.keyboard("{End}");
    expect(screen.getByTestId("shot-card-shot-3")).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByTestId("shot-card-shot-3")).toHaveFocus();
    await user.keyboard("{Home}");
    expect(first).toHaveFocus();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(onNavigate).toHaveBeenLastCalledWith({ shot: "shot-2" }, undefined);
    expect(await screen.findByTestId("shot-workspace-shot-2")).toBeInTheDocument();

    // Tabs follow the ARIA tabs pattern: arrows move between them and activate the focused tab.
    const details = await screen.findByRole("tab", { name: "Details" });
    details.focus();
    await user.keyboard("{ArrowRight}");
    const prompt = screen.getByRole("tab", { name: /^Prompt & lint/ });
    expect(prompt).toHaveFocus();
    expect(prompt).toHaveAttribute("aria-selected", "true");
    expect(onNavigate).toHaveBeenLastCalledWith(
      { shot: "shot-2", tab: "prompt" },
      { replace: true },
    );
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /^Takes/ })).toHaveFocus();

    // Previous/next step through the navigator order.
    await user.click(screen.getByRole("button", { name: "Next shot: Shot 3 · Rooftop" }));
    expect(await screen.findByTestId("shot-workspace-shot-3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next shot" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous shot: Shot 2 · Chase" }));
    expect(await screen.findByTestId("shot-workspace-shot-2")).toBeInTheDocument();
  });

  it("filters the navigator by real shot fields and keeps the open shot visible in the panel", async () => {
    const user = userEvent.setup();
    const approvedShots = [
      ...shots.slice(0, 2),
      makeShot({
        id: "shot-3",
        scene_id: "scene-2",
        sequence_index: 3,
        title: "Rooftop",
        approval_state: "approved",
        selected_take_id: "take-3",
      }),
    ];
    stubApi((request) =>
      request.pathname === "/api/v1/projects/project-1/shots"
        ? { status: 200, body: approvedShots }
        : boardHandler()(request),
    );
    mount();

    const filters = await screen.findByRole("group", { name: "Filter shots" });
    expect(within(filters).getByRole("button", { name: "All 3" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(filters).getByRole("button", { name: "Needs take 2" })).toBeInTheDocument();

    await user.click(within(filters).getByRole("button", { name: "Blocked 1" }));
    expect(screen.queryByTestId("shot-card-shot-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("shot-card-shot-2")).toBeInTheDocument();
    expect(screen.queryByTestId("shot-card-shot-3")).not.toBeInTheDocument();
    // Shot 1 stays open in the panel and the rail says why it is not listed.
    expect(screen.getByTestId("shot-workspace-shot-1")).toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation", { name: "Shot navigator" })).getByRole("note"),
    ).toHaveTextContent('Shot 1 · Hero enters is open but hidden by the "Blocked" filter.');

    await user.click(within(filters).getByRole("button", { name: "Approved 1" }));
    expect(screen.getByTestId("shot-card-shot-3")).toBeInTheDocument();
    expect(screen.queryByTestId("shot-card-shot-2")).not.toBeInTheDocument();

    await user.click(within(filters).getByRole("button", { name: "Needs take 2" }));
    expect(screen.getByTestId("shot-card-shot-1")).toBeInTheDocument();
    expect(screen.getByTestId("shot-card-shot-2")).toBeInTheDocument();
    expect(screen.queryByTestId("shot-card-shot-3")).not.toBeInTheDocument();
  });

  it("keeps batch launch behind a toggle in the toolbar", async () => {
    const user = userEvent.setup();
    const base = boardHandler();
    stubApi((request) =>
      request.pathname === "/api/v1/connections"
        ? {
            status: 200,
            body: [
              makeConnection(),
              makeConnection({ id: "conn-live", name: "Live gateway", provider_type: "muse2api" }),
            ],
          }
        : base(request),
    );
    mount();

    const toggle = await screen.findByRole("button", { name: "Batch launch" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("batch-launch-panel")).not.toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("batch-launch-panel")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Launch batch" })).toBeDisabled();
  });

  it("opens Details when the take list fails, marks the Takes tab and does not refetch in a loop", async () => {
    const user = userEvent.setup();
    const requests = stubApi((request) =>
      request.pathname === "/api/v1/queue/shots/shot-1/takes"
        ? { status: 500, body: { detail: "Database unavailable", retryable: true } }
        : boardHandler()(request),
    );
    mount();

    const panel = await screen.findByTestId("shot-workspace-shot-1");
    const takesTab = within(panel).getByRole("tab", { name: /^Takes/ });
    await waitFor(() => expect(takesTab).toHaveAccessibleName("Takes (failed to load)"));
    expect(within(panel).getByRole("tab", { name: "Details" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await new Promise((resolve) => setTimeout(resolve, 50));
    const takeLoads = () =>
      requests.filter((request) => request.pathname === "/api/v1/queue/shots/shot-1/takes").length;
    expect(takeLoads()).toBe(1);

    // Opening the tab retries once and shows the error with Retry.
    await user.click(takesTab);
    const review = await within(panel).findByTestId("shot-take-review-shot-1");
    expect(await within(review).findByRole("alert")).toHaveTextContent(
      "Could not load takes: Database unavailable",
    );
    expect(within(review).getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(takeLoads()).toBe(2);
  });
});

describe("ShotBoardPage generation setup and queue status", () => {
  it("uses the only enabled connection, summarized in one line, and keeps it across remounts", async () => {
    stubApi(boardHandler());
    mount();

    const summary = await screen.findByTestId("generation-setup-summary");
    await waitFor(() => expect(summary).toHaveTextContent("Generating with Mock studio"));
    expect(summary).toHaveTextContent("mock, no provider cost");
    expect(summary).toHaveTextContent("muse-video-mock-v1");
    expect(screen.queryByLabelText("Connection")).not.toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Generate Shot 1 · Hero enters" }),
    ).toBeEnabled();

    // A choice made by the user survives leaving and re-entering the module.
    const user = userEvent.setup();
    await user.click(
      within(screen.getByTestId("generation-setup")).getByRole("button", { name: "Change" }),
    );
    await user.selectOptions(screen.getByLabelText("Single-shot output"), "image");
    cleanup();
    stubApi(boardHandler());
    mount();
    const remounted = await screen.findByTestId("generation-setup-summary");
    await waitFor(() => expect(remounted).toHaveTextContent("Image"));
  });

  it("shows live generation activity with a link to the queue", async () => {
    const base = boardHandler();
    stubApi((request) =>
      request.method === "GET" && request.pathname === "/api/v1/queue/tasks"
        ? {
            status: 200,
            body: makeTaskPage(
              [
                makeQueueTask({ id: "t-run", shot_id: "shot-1", state: "running" }),
                makeQueueTask({ id: "t-ask", shot_id: "shot-2", state: "awaiting_input" }),
                makeQueueTask({ id: "t-bad", shot_id: "shot-3", state: "failed" }),
              ],
              // All-time failures are not what the bar reports.
              { failed_tasks: 9 },
            ),
          }
        : base(request),
    );
    mount();

    const bar = await screen.findByTestId("generation-activity");
    expect(bar).toHaveTextContent("1 task queued or running");
    expect(bar).toHaveTextContent("1 task waiting for your answer");
    expect(bar).toHaveTextContent("1 shot's latest generation failed");
    // The router Link is mocked in this file; the real href is covered by the route tests.
    expect(within(bar).getByRole("link", { name: /Open Generation Queue/ })).toBeInTheDocument();
  });

  it("explains the selected shot's failed task and starts a new task only after confirmation", async () => {
    const user = userEvent.setup();
    const base = boardHandler();
    const requests = stubApi((request) => {
      if (request.method === "GET" && request.pathname === "/api/v1/queue/tasks") {
        return {
          status: 200,
          body: makeTaskPage([
            makeQueueTask({
              id: "t-bad",
              shot_id: "shot-1",
              state: "failed",
              error_message_redacted: "Provider rejected the prompt.",
              error_category: "provider_rejected",
              retryable: false,
              attempt_count: 3,
            }),
          ]),
        };
      }
      if (request.method === "POST" && request.pathname === "/api/v1/queue/tasks") {
        return { status: 201, body: makeEnqueuedTask({ id: "t-new", created_new: true }) };
      }
      return base(request);
    });
    mount();

    const latest = await screen.findByTestId("shot-latest-task");
    expect(latest).toHaveTextContent("Failed");
    expect(latest).toHaveTextContent("automatic tries 3 of 3");
    expect(within(latest).getByTestId("shot-latest-task-error")).toHaveTextContent(
      "Provider rejected the prompt. (provider_rejected)",
    );
    expect(latest).toHaveTextContent(/marked this error as permanent/);

    await user.click(within(latest).getByRole("button", { name: "Start a new task" }));
    const confirm = screen.getByRole("alertdialog");
    expect(confirm).toHaveTextContent("Start a new video generation task for Shot 1");
    expect(confirm).toHaveTextContent(/always creates a new task/);
    expect(requests.filter((r) => r.method === "POST")).toHaveLength(0);

    await user.click(within(confirm).getByRole("button", { name: "Confirm new task" }));
    await waitFor(() =>
      expect(requests.filter((r) => r.method === "POST").map((r) => r.body)).toEqual([
        expect.objectContaining({ shot_id: "shot-1", force_retry: true }),
      ]),
    );
  });

  it("offers a new task when Generate matched a task that already failed", async () => {
    const user = userEvent.setup();
    const base = boardHandler();
    const requests = stubApi((request) =>
      request.method === "POST" && request.pathname === "/api/v1/queue/tasks"
        ? {
            status: 201,
            body: makeEnqueuedTask({
              id: "task-old",
              state: "failed",
              created_new: (request.body as { force_retry: boolean }).force_retry,
            }),
          }
        : base(request),
    );
    mount();

    await user.click(await screen.findByRole("button", { name: "Generate Shot 1 · Hero enters" }));
    await user.click(screen.getByRole("button", { name: "Confirm and queue" }));
    const result = await screen.findByTestId("shot-enqueue-result");
    expect(result).toHaveTextContent("No new generation was started.");

    await user.click(within(result).getByRole("button", { name: "Start a new task" }));
    await user.click(screen.getByRole("button", { name: "Confirm new task" }));
    await waitFor(() =>
      expect(
        requests
          .filter((r) => r.method === "POST")
          .map((r) => (r.body as { force_retry: boolean }).force_retry),
      ).toEqual([false, true]),
    );
  });

  it("reloads shots as soon as a running task finishes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const base = boardHandler();
      let taskPolls = 0;
      const requests = stubApi((request) => {
        if (request.method === "GET" && request.pathname === "/api/v1/queue/tasks") {
          taskPolls += 1;
          const state = taskPolls === 1 ? "running" : "succeeded";
          return {
            status: 200,
            body: makeTaskPage([makeQueueTask({ id: "t-1", shot_id: "shot-1", state })]),
          };
        }
        return base(request);
      });
      mount();

      const shotLoads = () =>
        requests.filter((r) => r.pathname === "/api/v1/projects/project-1/shots").length;
      expect(await screen.findByTestId("generation-activity")).toHaveTextContent(
        "1 task queued or running",
      );
      expect(shotLoads()).toBe(1);
      // The task poll (2 s while active) sees it finish; shots reload before their own 5 s poll.
      await vi.advanceTimersByTimeAsync(2_500);
      await waitFor(() => expect(shotLoads()).toBe(2));
      await waitFor(() => expect(screen.queryByTestId("generation-activity")).toBeNull());
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops polling shots with the task query's 30-minute window and says so", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const base = boardHandler();
      const requests = stubApi((request) =>
        request.method === "GET" && request.pathname === "/api/v1/queue/tasks"
          ? {
              status: 200,
              // Waiting on an operator answer for hours keeps the task active.
              body: makeTaskPage([
                makeQueueTask({ id: "t-1", shot_id: "shot-1", state: "awaiting_input" }),
              ]),
            }
          : base(request),
      );
      mount();
      await screen.findByTestId("generation-activity");

      const loads = (path: string) => requests.filter((r) => r.pathname === path).length;
      await vi.advanceTimersByTimeAsync(31 * 60_000);
      const shotsAtExpiry = loads("/api/v1/projects/project-1/shots");
      const tasksAtExpiry = loads("/api/v1/queue/tasks");
      await vi.advanceTimersByTimeAsync(10 * 60_000);
      expect(loads("/api/v1/projects/project-1/shots")).toBe(shotsAtExpiry);
      expect(loads("/api/v1/queue/tasks")).toBe(tasksAtExpiry);
      expect(screen.getByTestId("auto-refresh-stopped")).toHaveTextContent("Auto-refresh stopped");
    } finally {
      vi.useRealTimers();
    }
  }, 30_000); // Simulates ~40 minutes of polling.

  it("does not poll shots on a failed status query and reports the failure", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const base = boardHandler();
      const requests = stubApi((request) =>
        request.method === "GET" && request.pathname === "/api/v1/queue/tasks"
          ? { status: 403, body: { detail: "Forbidden", category: "auth", retryable: false } }
          : base(request),
      );
      mount();

      const bar = await screen.findByTestId("generation-activity");
      expect(bar).toHaveTextContent("Could not load generation status.");
      expect(within(bar).getByRole("button", { name: "Refresh" })).toBeInTheDocument();
      const shotLoads = () =>
        requests.filter((r) => r.pathname === "/api/v1/projects/project-1/shots").length;
      const before = shotLoads();
      await vi.advanceTimersByTimeAsync(30_000);
      expect(shotLoads()).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("moves focus to the generation setup from the shortcut next to Generate", async () => {
    const user = userEvent.setup();
    const base = boardHandler();
    stubApi((request) =>
      request.pathname === "/api/v1/connections"
        ? {
            status: 200,
            body: [makeConnection(), makeConnection({ id: "conn-live", name: "Live gateway" })],
          }
        : base(request),
    );
    mount();

    await screen.findByText("Choose a provider connection to generate.");
    const shortcuts = screen.getAllByRole("button", { name: "Set up generation" });
    // The last one sits next to the disabled Generate button.
    await user.click(shortcuts[shortcuts.length - 1]!);
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveFocus());
  });
});

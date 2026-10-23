import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => <a href="/">{children as string}</a>,
}));

import { ShotBoardPage } from "@/features/shot-board/pages/shot-board-page";
import {
  makeConnection,
  makeEnqueuedTask,
  makeReview,
  makeShot,
} from "@/features/shot-board/test-fixtures";
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
      const response = handler(request);
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

function mount() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ShotBoardPage projectId="project-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useSessionStore.getState().setAdminToken("test-token");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useSessionStore.getState().clearAdminToken();
});

describe("ShotBoardPage", () => {
  it("lists shots grouped by scene with their state, continuity and first-frame indicators", async () => {
    stubApi(({ pathname }) =>
      pathname === "/api/v1/projects/project-1/shots"
        ? { status: 200, body: shots }
        : pathname === "/api/v1/connections"
          ? { status: 200, body: [makeConnection()] }
          : undefined,
    );
    mount();

    const sceneOne = await screen.findByTestId("shot-scene-scene-1");
    expect(within(sceneOne).getAllByRole("article")).toHaveLength(2);
    expect(within(screen.getByTestId("shot-scene-scene-2")).getAllByRole("article")).toHaveLength(
      1,
    );

    const chase = screen.getByTestId("shot-card-shot-2");
    expect(within(chase).getByTestId("shot-generation-state")).toHaveTextContent(
      "Blocked: upstream frame missing",
    );
    expect(within(chase).getByTestId("shot-approval-state")).toHaveTextContent("Needs review");
    expect(within(chase).getByTestId("shot-first-frame")).toHaveTextContent("First frame set");
    expect(chase).toHaveTextContent("Frame chain");
    expect(chase).toHaveTextContent("Shot 1 · Hero enters");
    expect(
      within(chase).getByRole("button", { name: "Review takes for Shot 2 · Chase" }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(within(chase).queryByTestId("shot-predecessor-warning")).not.toBeInTheDocument();
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
    const requests = stubApi(({ method, pathname }) => {
      if (pathname === "/api/v1/projects/project-1/shots") return { status: 200, body: shots };
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
      return undefined;
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
    const requests = stubApi(({ method, pathname }) => {
      if (pathname === "/api/v1/projects/project-1/shots") return { status: 200, body: shots };
      if (pathname === "/api/v1/connections") return { status: 200, body: [makeConnection()] };
      if (method === "POST" && pathname === "/api/v1/queue/tasks") {
        return {
          status: 201,
          body: makeEnqueuedTask({ id: "task-old", state: "failed", created_new: false }),
        };
      }
      return undefined;
    });
    mount();

    const generate = await screen.findByRole("button", { name: "Generate Shot 1 · Hero enters" });
    expect(generate).toBeDisabled();
    await user.selectOptions(await screen.findByLabelText("Connection"), "conn-mock");
    expect(screen.getByLabelText("Model")).toHaveValue("muse-video-mock-v1");
    expect(screen.getByTestId("connection-cost-label")).toHaveTextContent(
      "Mock provider: no provider cost.",
    );

    await user.click(generate);
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("Queue video generation for Shot 1 · Hero enters?");
    expect(dialog).toHaveTextContent("Mock provider (no provider cost)");
    await user.click(within(dialog).getByRole("button", { name: "Confirm and queue" }));

    const result = await screen.findByTestId("shot-enqueue-result");
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

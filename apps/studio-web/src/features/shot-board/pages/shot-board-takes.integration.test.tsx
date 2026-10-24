import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => <a href="/">{children as string}</a>,
}));

import {
  makeComparison,
  makeConnection,
  makeFlagResult,
  makeSelection,
  makeShot,
  makeTake,
} from "@/features/shot-board/test-fixtures";
import { ShotBoardHarness } from "@/features/shot-board/test-harness";
import { useSessionStore } from "@/lib/stores/session-store";

// Exercises the real hooks, API client and object-URL lifecycle; only fetch and the
// URL.createObjectURL/revokeObjectURL statics are stubbed.

interface RecordedRequest {
  method: string;
  pathname: string;
  search: string;
  token: string | null;
  body: unknown;
}

type Handler = (
  request: RecordedRequest,
) => { status: number; body: unknown } | Response | undefined;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function binary(type: string): Response {
  return new Response(new Uint8Array([1, 2, 3, 4]), {
    status: 200,
    headers: { "content-type": type, "content-length": "4" },
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
        search: url.search,
        token: new Headers(init?.headers).get("X-Studio-Admin-Token"),
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      };
      requests.push(request);
      const response = handler(request);
      if (!response) return json(404, { detail: "Not mocked", category: "not_found" });
      if (response instanceof Response) return response;
      return json(response.status, response.body);
    }),
  );
  return requests;
}

function stubObjectUrls() {
  let next = 0;
  const create = vi.fn<(object: Blob | MediaSource) => string>(() => `blob:media-${++next}`);
  const revoke = vi.fn<(url: string) => void>();
  const NativeURL = globalThis.URL;
  vi.stubGlobal(
    "URL",
    class extends NativeURL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
  return { create, revoke };
}

const shots = [
  makeShot({ id: "shot-1", sequence_index: 1, title: "Hero enters", next_shot_id: "shot-2" }),
  makeShot({
    id: "shot-2",
    sequence_index: 2,
    title: "Chase",
    continuity_mode: "frame_chain",
    previous_shot_id: "shot-1",
  }),
];

const takeOne = makeTake({
  id: "take-1",
  take_number: 1,
  asset_id: "video-1",
  first_frame_asset_id: "frame-1-first",
  last_frame_asset_id: "frame-1-last",
  is_selected: true,
});
const takeTwo = makeTake({
  id: "take-2",
  take_number: 2,
  task_id: "task-2",
  asset_id: "video-2",
  model_id: "muse-video-v2",
  prompt_hash: "hash-2",
  last_frame_asset_id: "frame-2-last",
  continuity_flagged: true,
  director_notes: "Scarf switches shoulders.",
});

function baseHandler(extra?: Handler): Handler {
  return (request) => {
    const custom = extra?.(request);
    if (custom) return custom;
    const { method, pathname } = request;
    if (pathname === "/api/v1/projects/project-1/shots") return { status: 200, body: shots };
    if (pathname === "/api/v1/connections") return { status: 200, body: [makeConnection()] };
    if (method === "GET" && pathname === "/api/v1/queue/shots/shot-1/takes") {
      return { status: 200, body: [takeTwo, takeOne] };
    }
    if (method === "GET" && pathname === "/api/v1/queue/shots/shot-2/takes") {
      return { status: 200, body: [] };
    }
    if (pathname.startsWith("/api/v1/assets/frame-")) return binary("image/png");
    if (pathname.startsWith("/api/v1/assets/video-")) return binary("video/mp4");
    return undefined;
  };
}

function mount() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ShotBoardHarness projectId="project-1" />
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

/** Opens the shot from the navigator, then its Takes tab. */
async function openReview(shotLabel: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: shotLabel }));
  await user.click(await screen.findByRole("tab", { name: /^Takes/ }));
  return user;
}

describe("Shot Board take review", () => {
  it("lists takes with badges, notes and authenticated frame thumbnails, revoking URLs when the tab closes", async () => {
    const { create, revoke } = stubObjectUrls();
    const requests = stubApi(baseHandler());
    mount();

    const user = await openReview("Shot 1 · Hero enters");
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    const items = await within(panel).findAllByRole("listitem");
    expect(items.map((item) => item.getAttribute("data-testid"))).toEqual([
      "take-take-1",
      "take-take-2",
    ]);

    const first = within(panel).getByTestId("take-take-1");
    expect(within(first).getByTestId("take-selected-badge")).toHaveTextContent("Selected");
    expect(within(first).getByRole("button", { name: "Take 1 is selected" })).toBeDisabled();
    const second = within(panel).getByTestId("take-take-2");
    expect(within(second).getByTestId("take-continuity-badge")).toHaveTextContent(
      "Continuity flagged",
    );
    expect(within(second).getByTestId("take-notes")).toHaveTextContent("Scarf switches shoulders.");
    expect(second).toHaveTextContent("muse-video-v2");
    expect(second).toHaveTextContent("task-2");
    expect(
      within(second).getByRole("img", { name: "Take 2 first frame: No first frame" }),
    ).toBeInTheDocument();

    const thumb = await within(first).findByRole("img", { name: "Take 1 first frame" });
    expect(thumb.getAttribute("src")).toMatch(/^blob:media-/);
    const frameRequest = requests.find(
      (request) => request.pathname === "/api/v1/assets/frame-1-first/download",
    );
    expect(frameRequest?.token).toBe("test-token");
    // Videos are not downloaded for the list; only frames are.
    expect(requests.some((request) => request.pathname.startsWith("/api/v1/assets/video-"))).toBe(
      false,
    );

    await user.click(within(first).getByRole("button", { name: "Preview Take 1" }));
    const video = await within(first).findByLabelText("Take 1 media");
    expect(video.tagName).toBe("VIDEO");
    expect(create.mock.calls.some(([blob]) => (blob as Blob).type === "video/mp4")).toBe(true);

    const created = create.mock.results.map((result) => result.value as string);
    // Leaving the Takes tab unmounts the review and releases every object URL it created.
    await user.click(screen.getByRole("tab", { name: "Details" }));
    expect(screen.queryByTestId("shot-take-review-shot-1")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(new Set(revoke.mock.calls.map(([url]) => url))).toEqual(new Set(created)),
    );
  });

  it("shows the empty state and the auth error state", async () => {
    stubObjectUrls();
    stubApi(
      baseHandler(({ pathname }) =>
        pathname === "/api/v1/queue/shots/shot-1/takes"
          ? { status: 401, body: { detail: "Invalid admin token" } }
          : undefined,
      ),
    );
    mount();

    await openReview("Shot 2 · Chase");
    expect(await screen.findByText("No takes yet")).toBeInTheDocument();

    await openReview("Shot 1 · Hero enters");
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "The configured admin token was rejected while reading takes.",
    );
  });

  it("compares two takes side by side and selects one, refreshing shots and reporting downstream shots", async () => {
    stubObjectUrls();
    const requests = stubApi(
      baseHandler(({ method, pathname }) => {
        if (pathname === "/api/v1/projects/project-1/shots/shot-1/compare-takes") {
          return { status: 200, body: makeComparison(takeOne, takeTwo) };
        }
        if (method === "POST" && pathname === "/api/v1/queue/shots/shot-1/takes/take-2/select") {
          return {
            status: 200,
            body: makeSelection({
              selected_take_id: "take-2",
              last_frame_asset_id: "frame-2-last",
              propagated_downstream_shots: ["shot-2"],
            }),
          };
        }
        return undefined;
      }),
    );
    mount();

    const user = await openReview("Shot 1 · Hero enters");
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    await within(panel).findAllByRole("listitem");
    const compareButton = within(panel).getByRole("button", { name: "Compare A/B" });
    expect(compareButton).toBeDisabled();
    await user.click(within(panel).getByRole("checkbox", { name: "Compare Take 1" }));
    await user.click(within(panel).getByRole("checkbox", { name: "Compare Take 2" }));
    await user.click(compareButton);

    const compare = await within(panel).findByTestId("take-compare");
    expect(
      within(compare).getByRole("heading", { name: /A\/B compare: Take 1 vs Take 2/ }),
    ).toHaveFocus();
    expect(within(compare).getByTestId("compare-same-model")).toHaveTextContent("Different model");
    expect(within(compare).getByTestId("compare-same-prompt")).toHaveTextContent(
      "Different prompt",
    );
    expect(requests.find((request) => request.pathname.endsWith("/compare-takes"))?.search).toBe(
      "?take_a_id=take-1&take_b_id=take-2",
    );
    expect(
      within(within(compare).getByTestId("compare-side-A")).getByRole("button", {
        name: "Take 1 is selected",
      }),
    ).toBeDisabled();

    const shotLoadsBefore = requests.filter((r) => r.pathname.endsWith("/project-1/shots")).length;
    await user.click(within(compare).getByRole("button", { name: "Select Take 2 (side B)" }));

    const result = await within(panel).findByTestId("take-selection-result");
    expect(result).toHaveTextContent("Take 2 selected.");
    expect(result).toHaveTextContent("frame-2-last");
    expect(within(result).getByTestId("take-selection-downstream")).toHaveTextContent(
      "Shot 2 · Chase",
    );
    await waitFor(() =>
      expect(
        requests.filter((r) => r.pathname.endsWith("/project-1/shots")).length,
      ).toBeGreaterThan(shotLoadsBefore),
    );
    expect(requests.filter((r) => r.method === "POST").map((r) => [r.pathname, r.body])).toEqual([
      ["/api/v1/queue/shots/shot-1/takes/take-2/select", undefined],
    ]);
  });

  it("flags continuity with regenerate after a quota confirmation and links the new task", async () => {
    stubObjectUrls();
    let flagCalls = 0;
    const requests = stubApi(
      baseHandler(({ method, pathname }) => {
        if (
          method === "POST" &&
          pathname === "/api/v1/projects/project-1/shots/shot-1/takes/take-1/flag-continuity"
        ) {
          flagCalls += 1;
          return flagCalls === 1
            ? { status: 422, body: { detail: "Shot has Prompt Linter blockers." } }
            : {
                status: 200,
                body: makeFlagResult({
                  director_notes: "Lamp is missing.",
                  regenerated_task_id: "task-regen-1",
                }),
              };
        }
        return undefined;
      }),
    );
    mount();

    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Connection"), "conn-mock");
    await openReview("Shot 1 · Hero enters");
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    await within(panel).findAllByRole("listitem");
    await user.click(within(panel).getByRole("button", { name: "Flag continuity on Take 1" }));

    const form = within(panel).getByTestId("continuity-flag-form-take-1");
    const notes = within(form).getByLabelText("Director notes");
    expect(notes).toHaveFocus();
    await user.click(within(form).getByRole("button", { name: "Flag continuity" }));
    expect(within(form).getByText(/Describe the continuity problem/)).toBeInTheDocument();

    await user.type(notes, "Lamp is missing.");
    await user.click(within(form).getByRole("checkbox", { name: "Regenerate this shot now" }));
    await user.click(within(form).getByRole("button", { name: "Flag and regenerate" }));

    const confirm = within(form).getByRole("alertdialog");
    expect(confirm).toHaveTextContent("Mock provider (no provider cost)");
    expect(confirm).toHaveTextContent("muse-video-mock-v1");
    await user.click(within(confirm).getByRole("button", { name: "Confirm and regenerate" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "The regeneration was refused: Shot has Prompt Linter blockers. The continuity flag may already be saved",
    );

    await user.click(within(form).getByRole("button", { name: "Flag and regenerate" }));
    await user.click(
      within(within(form).getByRole("alertdialog")).getByRole("button", {
        name: "Confirm and regenerate",
      }),
    );

    const result = await within(panel).findByTestId("take-flag-result");
    expect(result).toHaveTextContent("Continuity flag saved on Take 1.");
    expect(within(result).getByTestId("regenerated-task-id")).toHaveTextContent("task-regen-1");
    expect(within(result).getByText("Follow regeneration in Generation Queue")).toBeInTheDocument();
    expect(within(panel).queryByTestId("continuity-flag-form-take-1")).not.toBeInTheDocument();

    const flags = requests.filter((request) => request.pathname.endsWith("/flag-continuity"));
    expect(flags.map((request) => request.body)).toEqual([
      {
        continuity_flagged: true,
        director_notes: "Lamp is missing.",
        regenerate_now: true,
        connection_id: "conn-mock",
        model_id: "muse-video-mock-v1",
      },
      {
        continuity_flagged: true,
        director_notes: "Lamp is missing.",
        regenerate_now: true,
        connection_id: "conn-mock",
        model_id: "muse-video-mock-v1",
      },
    ]);
  });

  it("disables regenerate until Generation settings has a connection and flags without it", async () => {
    stubObjectUrls();
    const requests = stubApi(
      baseHandler(({ method, pathname }) =>
        method === "POST" && pathname.endsWith("/takes/take-1/flag-continuity")
          ? { status: 200, body: makeFlagResult({ director_notes: "Wrong prop." }) }
          : method === "GET" && pathname === "/api/v1/queue/shots/shot-1/takes"
            ? {
                status: 200,
                body: [{ ...takeOne, model_id: "studio-custom-video-v3" }, takeTwo],
              }
            : undefined,
      ),
    );
    mount();

    const user = await openReview("Shot 1 · Hero enters");
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    await within(panel).findAllByRole("listitem");
    await user.click(within(panel).getByRole("button", { name: "Flag continuity on Take 1" }));
    const form = within(panel).getByTestId("continuity-flag-form-take-1");
    expect(within(form).getByRole("checkbox", { name: "Regenerate this shot now" })).toBeDisabled();
    expect(form).toHaveTextContent("Select a connection in Generation settings first.");

    await user.type(within(form).getByLabelText("Director notes"), "Wrong prop.");
    await user.click(within(form).getByRole("button", { name: "Flag continuity" }));

    const result = await within(panel).findByTestId("take-flag-result");
    expect(result).not.toHaveTextContent("Regeneration queued");
    expect(requests.find((request) => request.pathname.endsWith("/flag-continuity"))?.body).toEqual(
      {
        continuity_flagged: true,
        director_notes: "Wrong prop.",
        regenerate_now: false,
        // The take's own model, not the server default `muse-video-mock-v1`.
        model_id: "studio-custom-video-v3",
      },
    );
    // A saved flag closes the form and returns focus to the take's flag button.
    expect(within(panel).getByRole("button", { name: "Flag continuity on Take 1" })).toHaveFocus();
  });
});

describe("Predecessor order warning", () => {
  it("warns in the shot panel and in the edit panel without blocking the save", async () => {
    stubObjectUrls();
    const outOfOrder = [
      makeShot({
        id: "shot-1",
        sequence_index: 1,
        title: "Hero enters",
        previous_shot_id: "shot-2",
      }),
      makeShot({ id: "shot-2", sequence_index: 2, title: "Chase" }),
      makeShot({ id: "shot-3", sequence_index: 3, title: "Exit" }),
      makeShot({
        id: "shot-4",
        scene_id: "scene-0",
        sequence_index: 0,
        title: "Prologue",
        previous_shot_id: "shot-3",
      }),
    ];
    const requests = stubApi(({ method, pathname }) => {
      if (pathname === "/api/v1/projects/project-1/shots" && method === "GET") {
        return { status: 200, body: outOfOrder };
      }
      if (pathname === "/api/v1/connections") return { status: 200, body: [] };
      if (method === "PATCH") {
        return {
          status: 200,
          body: {
            id: "shot-2",
            prompt_final: "p",
            prompt_hash: "h",
            lint_report: [],
            approval_state: "draft",
            generation_state: "not_started",
          },
        };
      }
      return undefined;
    });
    mount();

    // shot-4 (#0) is first in the navigator and opens by default. It points at a later shot of
    // another scene: the backend only links shots within a scene, so no order warning is shown.
    const prologue = await screen.findByTestId("shot-workspace-shot-4");
    expect(within(prologue).queryByTestId("shot-predecessor-warning")).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Shot 1 · Hero enters" }));
    const first = await screen.findByTestId("shot-workspace-shot-1");
    expect(within(first).getByTestId("shot-predecessor-warning")).toHaveTextContent(
      "Previous shot comes after this shot in the sequence: Shot 2 · Chase is #2; this shot is #1.",
    );
    await user.click(screen.getByRole("button", { name: "Shot 2 · Chase" }));
    expect(
      within(await screen.findByTestId("shot-workspace-shot-2")).queryByTestId(
        "shot-predecessor-warning",
      ),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit Shot 2 · Chase" }));
    const panel = screen.getByTestId("shot-edit-panel-shot-2");
    const previous = within(panel).getByLabelText("Previous shot (same scene)");
    await user.selectOptions(previous, "shot-1");
    expect(within(panel).queryByTestId("edit-predecessor-warning")).not.toBeInTheDocument();
    await user.selectOptions(previous, "shot-3");
    expect(within(panel).getByTestId("edit-predecessor-warning")).toHaveTextContent(
      "Previous shot comes after this shot in the sequence (Shot 3 · Exit is #3; this shot is #2). You can still save.",
    );

    const save = within(panel).getByRole("button", { name: "Save shot" });
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() =>
      expect(requests.find((request) => request.method === "PATCH")?.body).toEqual({
        previous_shot_id: "shot-3",
      }),
    );
  });
});

describe("Take review: uncertain outcomes, focus and sessions", () => {
  it("reports an unknown outcome when a regenerate request times out, without inviting a retry", async () => {
    stubObjectUrls();
    const requests = stubApi(
      baseHandler(({ method, pathname }) =>
        method === "POST" && pathname.endsWith("/takes/take-1/flag-continuity")
          ? { status: 504, body: { detail: "Gateway timeout" } }
          : undefined,
      ),
    );
    mount();

    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Connection"), "conn-mock");
    await openReview("Shot 1 · Hero enters");
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    await within(panel).findAllByRole("listitem");
    await user.click(within(panel).getByRole("button", { name: "Flag continuity on Take 1" }));
    const form = within(panel).getByTestId("continuity-flag-form-take-1");
    await user.type(within(form).getByLabelText("Director notes"), "Lamp is missing.");
    await user.click(within(form).getByRole("checkbox", { name: "Regenerate this shot now" }));
    await user.click(within(form).getByRole("button", { name: "Flag and regenerate" }));
    await user.click(within(form).getByRole("button", { name: "Confirm and regenerate" }));

    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveAttribute("data-outcome-unknown", "true");
    expect(alert).toHaveTextContent(
      "The outcome is unknown: the continuity flag and a regeneration task may already exist.",
    );
    expect(alert).not.toHaveTextContent(/not saved|Try again/);
    expect(within(alert).getByText("Check the Generation Queue")).toBeInTheDocument();

    const takeLoads = () =>
      requests.filter((request) => request.pathname === "/api/v1/queue/shots/shot-1/takes").length;
    const before = takeLoads();
    await user.click(within(alert).getByRole("button", { name: "Refresh takes" }));
    await waitFor(() => expect(takeLoads()).toBeGreaterThan(before));
    expect(requests.filter((request) => request.method === "POST")).toHaveLength(1);
  });

  it("treats a network failure of a regenerate request as an unknown outcome", async () => {
    stubObjectUrls();
    stubApi(baseHandler());
    const fetchMock = vi.mocked(fetch);
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      if (init?.method === "POST") throw new TypeError("Failed to fetch");
      return original(input, init);
    });
    mount();

    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Connection"), "conn-mock");
    await openReview("Shot 1 · Hero enters");
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    await within(panel).findAllByRole("listitem");
    await user.click(within(panel).getByRole("button", { name: "Flag continuity on Take 1" }));
    const form = within(panel).getByTestId("continuity-flag-form-take-1");
    await user.type(within(form).getByLabelText("Director notes"), "Lamp is missing.");
    await user.click(within(form).getByRole("checkbox", { name: "Regenerate this shot now" }));
    await user.click(within(form).getByRole("button", { name: "Flag and regenerate" }));
    await user.click(within(form).getByRole("button", { name: "Confirm and regenerate" }));

    expect(await within(form).findByRole("alert")).toHaveAttribute("data-outcome-unknown", "true");
  });

  it("closes the compare with Esc, returns focus, and swaps A/B without refetching", async () => {
    stubObjectUrls();
    const requests = stubApi(
      baseHandler(({ pathname }) =>
        pathname === "/api/v1/projects/project-1/shots/shot-1/compare-takes"
          ? { status: 200, body: makeComparison(takeOne, takeTwo) }
          : undefined,
      ),
    );
    mount();

    const user = await openReview("Shot 1 · Hero enters");
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    await within(panel).findAllByRole("listitem");
    await user.click(within(panel).getByRole("checkbox", { name: "Compare Take 1" }));
    await user.click(within(panel).getByRole("checkbox", { name: "Compare Take 2" }));
    const compareButton = within(panel).getByRole("button", { name: "Compare A/B" });
    await user.click(compareButton);

    let compare = await within(panel).findByTestId("take-compare");
    const heading = within(compare).getByRole("heading", { name: /A\/B compare/ });
    expect(heading).toHaveFocus();
    expect(heading).toHaveTextContent("Take 1 vs Take 2");

    const swap = within(compare).getByRole("button", { name: "Swap A/B" });
    await user.click(swap);
    expect(within(panel).getByTestId("take-compare")).toBe(compare);
    expect(heading).toHaveTextContent("Take 2 vs Take 1");
    expect(within(compare).getByTestId("compare-side-A")).toHaveAccessibleName("Side A: Take 2");
    expect(swap).toHaveFocus();
    expect(
      within(panel).getByRole("checkbox", { name: "Compare Take 2" }).parentElement,
    ).toHaveTextContent("Compare (A)");
    expect(requests.filter((request) => request.pathname.endsWith("/compare-takes"))).toHaveLength(
      1,
    );

    await user.keyboard("{Escape}");
    expect(within(panel).queryByTestId("take-compare")).not.toBeInTheDocument();
    expect(compareButton).toHaveFocus();

    await user.click(compareButton);
    compare = await within(panel).findByTestId("take-compare");
    await user.click(within(compare).getByRole("button", { name: "Close compare" }));
    expect(compareButton).toHaveFocus();
  });

  it("returns focus from the confirm to the submit button and from the form to its trigger", async () => {
    stubObjectUrls();
    stubApi(baseHandler());
    mount();

    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Connection"), "conn-mock");
    await openReview("Shot 1 · Hero enters");
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    await within(panel).findAllByRole("listitem");
    const trigger = within(panel).getByRole("button", { name: "Flag continuity on Take 1" });
    await user.click(trigger);
    const form = within(panel).getByTestId("continuity-flag-form-take-1");
    await user.type(within(form).getByLabelText("Director notes"), "Lamp.");
    await user.click(within(form).getByRole("checkbox", { name: "Regenerate this shot now" }));
    await user.click(within(form).getByRole("button", { name: "Flag and regenerate" }));

    await user.click(within(form).getByRole("button", { name: "Back" }));
    expect(within(form).getByRole("button", { name: "Flag and regenerate" })).toHaveFocus();

    await user.click(within(form).getByRole("button", { name: "Flag and regenerate" }));
    await user.keyboard("{Escape}");
    expect(within(form).queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Flag and regenerate" })).toHaveFocus();

    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(within(panel).queryByTestId("continuity-flag-form-take-1")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("does not show takes loaded for a previous admin session", async () => {
    stubObjectUrls();
    let releaseOld: () => void = () => undefined;
    const oldGate = new Promise<void>((resolve) => {
      releaseOld = resolve;
    });
    let takeCalls = 0;
    const handler = baseHandler();
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/v1/queue/shots/shot-1/takes") {
        takeCalls += 1;
        if (takeCalls === 1) {
          await oldGate;
          return json(200, [makeTake({ id: "take-old", take_number: 9 })]);
        }
        return json(200, [takeOne]);
      }
      const response = handler({
        method: init?.method ?? "GET",
        pathname: url.pathname,
        search: url.search,
        token: null,
        body: undefined,
      });
      if (!response) return json(404, { detail: "Not mocked" });
      return response instanceof Response ? response : json(response.status, response.body);
    });
    vi.stubGlobal("fetch", fetchMock);
    mount();

    await openReview("Shot 1 · Hero enters");
    await screen.findByText("Loading takes...");
    act(() => useSessionStore.getState().setAdminToken("another-token"));
    // The new session reloads the takes under its own cache key; the review is remounted.
    const panel = await screen.findByTestId("shot-take-review-shot-1");
    await within(panel).findByTestId("take-take-1");
    releaseOld();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(within(panel).queryByTestId("take-take-old")).not.toBeInTheDocument();
    expect(takeCalls).toBe(2);
  });
});

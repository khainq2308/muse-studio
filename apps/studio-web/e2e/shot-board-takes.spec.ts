import { expect, test, type Page, type Route } from "@playwright/test";

const testProject = {
  id: "test-project",
  name: "Test project",
  description: "",
  aspect_ratio: "16:9",
  asset_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  current_revision_num: 1,
  fps: 24,
  language: "en",
  output_profile: "default",
  style_preset: "default",
};

function makeShot(overrides: Record<string, unknown>) {
  return {
    id: "shot-1",
    scene_id: "scene-1",
    sequence_index: 1,
    title: "Hero enters",
    subject_codes: ["HERO"],
    location_code: "LOC_HALL",
    shot_size: "wide",
    camera_motion: "static",
    composition: "Hero framed in the doorway",
    action: "The hero walks into the hall.",
    dialogue: "",
    duration_seconds: 4,
    requires_reference_lock: false,
    reference_asset_ids: [],
    prompt_final: "wide static shot of the hero walking into the hall",
    negative_prompt: "",
    prompt_hash: "hash-1",
    lint_report: [],
    continuity_mode: "standard",
    previous_shot_id: null,
    next_shot_id: "shot-2",
    first_frame_asset_id: null,
    last_frame_asset_id: null,
    selected_take_id: "take-1",
    approval_state: "draft",
    generation_state: "succeeded",
    ...overrides,
  };
}

const shots = [
  makeShot({}),
  makeShot({
    id: "shot-2",
    sequence_index: 2,
    title: "Chase",
    continuity_mode: "frame_chain",
    previous_shot_id: "shot-1",
    next_shot_id: null,
    selected_take_id: null,
    generation_state: "not_started",
  }),
];

function makeTake(overrides: Record<string, unknown>) {
  return {
    id: "take-1",
    shot_id: "shot-1",
    take_number: 1,
    task_id: "task-1",
    asset_id: "video-1",
    model_id: "muse-video-mock-v1",
    prompt_hash: "hash-1",
    account_used: null,
    first_frame_asset_id: "frame-1-first",
    last_frame_asset_id: "frame-1-last",
    continuity_flagged: false,
    director_notes: "",
    is_selected: true,
    ...overrides,
  };
}

const mockConnection = {
  id: "conn-mock",
  name: "Mock studio",
  provider_type: "mock",
  base_url: "http://mock.local",
  api_key_masked: "****",
  proxy_display_host: null,
  timeout_seconds: 30,
  max_concurrency: 2,
  enabled: true,
  health_state: "healthy",
  latency_ms: 12,
  capabilities_snapshot: {
    models: [
      { model_id: "muse-video-mock-v1", modality: "video", verification_status: "mock-only" },
    ],
  },
  last_error_redacted: null,
  last_checked_at: null,
};

// 1x1 transparent PNG so frame thumbnails decode as real images.
const PNG_1X1 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

interface Recorded {
  method: string;
  pathname: string;
  search: string;
  body: unknown;
}

interface State {
  selectedTakeId: string;
  flagged: boolean;
}

function takesFor(state: State) {
  return [
    makeTake({ is_selected: state.selectedTakeId === "take-1" }),
    makeTake({
      id: "take-2",
      take_number: 2,
      task_id: "task-2",
      asset_id: "video-2",
      model_id: "muse-video-mock-v2",
      prompt_hash: "hash-2",
      first_frame_asset_id: null,
      last_frame_asset_id: "frame-2-last",
      is_selected: state.selectedTakeId === "take-2",
      continuity_flagged: state.flagged,
      director_notes: state.flagged ? "Scarf switches shoulders." : "",
    }),
  ];
}

function comparison(take: ReturnType<typeof makeTake>) {
  return {
    take_id: take.id,
    take_number: take.take_number,
    asset_id: take.asset_id,
    first_frame_asset_id: take.first_frame_asset_id,
    last_frame_asset_id: take.last_frame_asset_id,
    model_id: take.model_id,
    prompt_hash: take.prompt_hash,
    account_used: take.account_used,
    continuity_flagged: take.continuity_flagged,
    director_notes: take.director_notes,
    is_selected: take.is_selected,
  };
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockApi(page: Page) {
  const requests: Recorded[] = [];
  const state: State = { selectedTakeId: "take-1", flagged: false };
  await page.route("**/healthz", (route) => fulfillJson(route, 200, { status: "ok" }));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const recorded: Recorded = {
      method: request.method(),
      pathname: url.pathname,
      search: url.search,
      body: request.postData() ? JSON.parse(request.postData()!) : undefined,
    };
    requests.push(recorded);
    const { method, pathname } = recorded;

    if (pathname.startsWith("/api/v1/assets/frame-")) {
      await route.fulfill({ status: 200, contentType: "image/png", body: PNG_1X1 });
      return;
    }
    if (pathname.startsWith("/api/v1/assets/video-")) {
      // Not a decodable video: the test checks the authenticated <video> wiring, not playback.
      await route.fulfill({ status: 200, contentType: "video/mp4", body: Buffer.alloc(64) });
      return;
    }
    if (pathname === "/api/v1/projects") return fulfillJson(route, 200, [testProject]);
    if (pathname === "/api/v1/projects/test-project/shots") {
      return fulfillJson(route, 200, shots);
    }
    if (pathname === "/api/v1/connections") return fulfillJson(route, 200, [mockConnection]);
    if (method === "GET" && pathname === "/api/v1/queue/shots/shot-1/takes") {
      return fulfillJson(route, 200, takesFor(state));
    }
    if (pathname === "/api/v1/projects/test-project/shots/shot-1/compare-takes") {
      const [a, b] = takesFor(state);
      const ids = new URLSearchParams(recorded.search);
      const byId = new Map([a, b].map((take) => [take!.id, take!]));
      const takeA = byId.get(ids.get("take_a_id") ?? "")!;
      const takeB = byId.get(ids.get("take_b_id") ?? "")!;
      return fulfillJson(route, 200, {
        shot_id: "shot-1",
        same_prompt_hash: takeA.prompt_hash === takeB.prompt_hash,
        same_model: takeA.model_id === takeB.model_id,
        take_a: comparison(takeA),
        take_b: comparison(takeB),
      });
    }
    if (method === "POST" && pathname === "/api/v1/queue/shots/shot-1/takes/take-2/select") {
      state.selectedTakeId = "take-2";
      return fulfillJson(route, 200, {
        shot_id: "shot-1",
        selected_take_id: "take-2",
        last_frame_asset_id: "frame-2-last",
        propagated_downstream_shots: ["shot-2"],
      });
    }
    if (
      method === "POST" &&
      pathname === "/api/v1/projects/test-project/shots/shot-1/takes/take-2/flag-continuity"
    ) {
      state.flagged = true;
      return fulfillJson(route, 200, {
        shot_id: "shot-1",
        take_id: "take-2",
        continuity_flagged: true,
        director_notes: "Scarf switches shoulders.",
        regenerated_task_id: "task-regen-1",
      });
    }
    if (pathname === "/api/v1/queue/tasks") {
      return fulfillJson(route, 200, {
        items: [],
        total: 0,
        limit: 50,
        offset: 0,
        has_more: false,
        summary: { total_tasks: 0, active_tasks: 0, failed_tasks: 0, awaiting_input_tasks: 0 },
      });
    }
    return fulfillJson(route, 404, { detail: "Not mocked", category: "not_found" });
  });
  return requests;
}

/**
 * Opens a shot from the navigator. Shot 1 has a selected take, so its panel opens on the Takes
 * tab without another click; the tab must already be selected.
 */
async function openTakes(page: Page, shotLabel: string) {
  await page
    .getByRole("navigation", { name: "Shot navigator" })
    .getByRole("button", { name: shotLabel, exact: true })
    .click();
  await expect(page.getByRole("tab", { name: /^Takes/ })).toHaveAttribute("aria-selected", "true");
}

test("selects a take from the A/B compare and reports re-pointed downstream shots", async ({
  page,
}) => {
  const requests = await mockApi(page);
  await page.goto("/projects/test-project/shot-board");

  await openTakes(page, "Shot 1 · Hero enters");
  const panel = page.getByTestId("shot-take-review-shot-1");
  await expect(panel.getByRole("listitem")).toHaveCount(2);

  // Frame thumbnails are fetched with the admin header path and rendered from blob: URLs.
  const firstFrame = panel.getByTestId("take-take-1").getByRole("img", {
    name: "Take 1 first frame",
  });
  // Frames load lazily near the viewport; the take list starts below the shot header.
  await panel.getByTestId("take-take-1").scrollIntoViewIfNeeded();
  await expect(firstFrame).toHaveAttribute("src", /^blob:/);
  await expect
    .poll(() => firstFrame.evaluate((image: HTMLImageElement) => image.naturalWidth))
    .toBe(1);

  // Keyboard-only A/B selection.
  await panel.getByRole("checkbox", { name: "Compare Take 1" }).focus();
  await page.keyboard.press("Space");
  await panel.getByRole("checkbox", { name: "Compare Take 2" }).focus();
  await page.keyboard.press("Space");
  await panel.getByRole("button", { name: "Compare A/B" }).focus();
  await page.keyboard.press("Enter");

  const compare = panel.getByTestId("take-compare");
  await expect(
    compare.getByRole("heading", { name: /A\/B compare: Take 1 vs Take 2/ }),
  ).toBeFocused();
  await expect(compare.getByTestId("compare-same-model")).toHaveText("Different model");
  await expect(compare.getByTestId("compare-side-A").getByLabel("Take 1 media")).toHaveAttribute(
    "src",
    /^blob:/,
  );
  await expect(compare.getByTestId("compare-side-B").getByLabel("Take 2 media")).toHaveAttribute(
    "src",
    /^blob:/,
  );

  await page.screenshot({ path: "test-results/shot-board-take-compare.png", fullPage: true });

  await compare.getByRole("button", { name: "Select Take 2 (side B)" }).focus();
  await page.keyboard.press("Enter");

  const result = panel.getByTestId("take-selection-result");
  await expect(result).toContainText("Take 2 selected.");
  await expect(result.getByTestId("take-selection-downstream")).toHaveText("Shot 2 · Chase");
  // The refreshed takes now mark take 2 as selected in both the compare and the list.
  await expect(compare.getByTestId("compare-side-B").getByTestId("compare-selected")).toBeVisible();
  await expect(panel.getByTestId("take-take-2").getByTestId("take-selected-badge")).toBeVisible();

  const posts = requests.filter((request) => request.method === "POST");
  expect(posts.map((request) => request.pathname)).toEqual([
    "/api/v1/queue/shots/shot-1/takes/take-2/select",
  ]);
  expect(
    requests.filter((request) => request.pathname === "/api/v1/projects/test-project/shots").length,
  ).toBeGreaterThan(1);
  for (const request of requests) expect(request.pathname.startsWith("/api/")).toBe(true);
});

test("flags continuity with regenerate after confirmation and links to the Generation Queue", async ({
  page,
}) => {
  const requests = await mockApi(page);
  await page.goto("/projects/test-project/shot-board");

  await page.getByLabel("Connection").selectOption("conn-mock");
  await expect(page.getByLabel("Model")).toHaveValue("muse-video-mock-v1");

  await openTakes(page, "Shot 1 · Hero enters");
  const panel = page.getByTestId("shot-take-review-shot-1");
  await panel.getByRole("button", { name: "Flag continuity on Take 2" }).click();

  const form = panel.getByTestId("continuity-flag-form-take-2");
  await expect(form.getByLabel("Director notes")).toBeFocused();
  await form.getByLabel("Director notes").fill("Scarf switches shoulders.");
  await form.getByRole("checkbox", { name: "Regenerate this shot now" }).check();
  await form.getByRole("button", { name: "Flag and regenerate" }).click();

  const confirm = form.getByRole("alertdialog");
  await expect(confirm).toContainText("Mock provider (no provider cost)");
  await expect(confirm).toContainText("muse-video-mock-v1");
  await confirm.getByRole("button", { name: "Confirm and regenerate" }).click();

  const result = panel.getByTestId("take-flag-result");
  await expect(result).toContainText("Continuity flag saved on Take 2.");
  await expect(result.getByTestId("regenerated-task-id")).toHaveText("task-regen-1");
  await expect(panel.getByTestId("take-take-2").getByTestId("take-notes")).toHaveText(
    "Scarf switches shoulders.",
  );

  const flags = requests.filter((request) => request.pathname.endsWith("/flag-continuity"));
  expect(flags.map((request) => request.body)).toEqual([
    {
      continuity_flagged: true,
      director_notes: "Scarf switches shoulders.",
      regenerate_now: true,
      connection_id: "conn-mock",
      model_id: "muse-video-mock-v1",
    },
  ]);

  await page.screenshot({ path: "test-results/shot-board-flag-continuity.png", fullPage: true });

  await result.getByRole("link", { name: "Follow regeneration in Generation Queue" }).click();
  await expect(page).toHaveURL(/\/projects\/test-project\/queue-monitor$/);
  await expect(page.getByRole("heading", { name: "Generation Queue" })).toBeVisible();
});

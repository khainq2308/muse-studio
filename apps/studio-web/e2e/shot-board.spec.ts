import { expect, test, type Page } from "@playwright/test";

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
    selected_take_id: null,
    approval_state: "draft",
    generation_state: "not_started",
    ...overrides,
  };
}

const shots = [
  makeShot({}),
  makeShot({
    id: "shot-2",
    sequence_index: 2,
    title: "Chase",
    action: "The hero runs after the thief.",
    continuity_mode: "frame_chain",
    previous_shot_id: "shot-1",
    next_shot_id: null,
    approval_state: "needs_review",
  }),
  makeShot({
    id: "shot-3",
    scene_id: "scene-2",
    sequence_index: 3,
    title: "Rooftop",
    next_shot_id: null,
    lint_report: [
      {
        code: "MISSING_REFERENCE",
        severity: "blocker",
        message: "Reference image required for HERO.",
        field: "reference_asset_ids",
      },
    ],
  }),
];

const liveConnection = {
  id: "conn-live",
  name: "Live gateway",
  provider_type: "muse2api",
  base_url: "https://gateway.example",
  api_key_masked: "sk-****",
  proxy_display_host: null,
  timeout_seconds: 30,
  max_concurrency: 2,
  enabled: true,
  health_state: "healthy",
  latency_ms: 40,
  capabilities_snapshot: {
    models: [{ model_id: "muse-video-v1", modality: "video", verification_status: "unverified" }],
    video_first_frame: "unknown",
  },
  last_error_redacted: null,
  last_checked_at: "2026-01-01T00:00:00Z",
};

interface Recorded {
  method: string;
  pathname: string;
  body: unknown;
}

async function mockApi(page: Page, overrides: (request: Recorded) => unknown | undefined) {
  const requests: Recorded[] = [];
  await page.route("**/healthz", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' }),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const recorded: Recorded = {
      method: request.method(),
      pathname: new URL(request.url()).pathname,
      body: request.postData() ? JSON.parse(request.postData()!) : undefined,
    };
    requests.push(recorded);
    const custom = overrides(recorded) as { status: number; body: unknown } | undefined;
    if (custom) {
      await route.fulfill({
        status: custom.status,
        contentType: "application/json",
        body: JSON.stringify(custom.body),
      });
      return;
    }
    const body =
      recorded.pathname === "/api/v1/projects"
        ? [testProject]
        : recorded.pathname === "/api/v1/projects/test-project/shots"
          ? shots
          : recorded.pathname === "/api/v1/connections"
            ? [liveConnection]
            : /^\/api\/v1\/queue\/shots\/[^/]+\/takes$/.test(recorded.pathname)
              ? []
              : recorded.pathname === "/api/v1/queue/tasks"
                ? {
                    items: [],
                    total: 0,
                    limit: 50,
                    offset: 0,
                    has_more: false,
                    summary: {
                      total_tasks: 0,
                      active_tasks: 0,
                      failed_tasks: 0,
                      awaiting_input_tasks: 0,
                    },
                  }
                : null;
    await route.fulfill({
      status: body === null ? 404 : 200,
      contentType: "application/json",
      body: JSON.stringify(body ?? { detail: "Not mocked", category: "not_found" }),
    });
  });
  return requests;
}

test("edits a shot, maps a 422 to the field and shows the recompiled prompt", async ({ page }) => {
  let patchCount = 0;
  const requests = await mockApi(page, ({ method, pathname }) => {
    if (method !== "PATCH" || pathname !== "/api/v1/projects/test-project/shots/shot-2") {
      return undefined;
    }
    patchCount += 1;
    if (patchCount === 1) {
      return {
        status: 422,
        body: {
          detail:
            "Không thể Approve Shot khi còn lỗi Blocker: MISSING_REFERENCE - Reference image required",
          category: "validation",
          retryable: false,
        },
      };
    }
    return {
      status: 200,
      body: {
        id: "shot-2",
        prompt_final: "medium handheld shot, the hero sprints after the thief",
        prompt_hash: "hash-2b",
        lint_report: [],
        approval_state: "needs_review",
        generation_state: "not_started",
      },
    };
  });

  await page.goto("/projects/test-project/shot-board");

  await expect(page.getByRole("heading", { name: "Shot Board" })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Project modules" });
  await expect(nav.getByRole("link", { name: "Shot Board" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  const rail = page.getByRole("navigation", { name: "Shot navigator" });
  await expect(rail.getByTestId("shot-scene-scene-1").getByRole("listitem")).toHaveCount(2);
  await expect(rail.getByTestId("shot-scene-scene-2").getByRole("listitem")).toHaveCount(1);
  await expect(rail.getByTestId("shot-card-shot-1")).toHaveAttribute("aria-current", "true");

  // Open Shot 2 from the navigator; the selection is kept in the URL.
  await rail.getByRole("button", { name: "Shot 2 · Chase", exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/test-project\/shot-board\?shot=shot-2$/);
  await expect(rail.getByTestId("shot-card-shot-2")).toHaveAttribute("aria-current", "true");
  const workspace = page.getByTestId("shot-workspace-shot-2");
  await expect(workspace.getByRole("heading", { name: "Shot 2 · Chase" })).toBeVisible();

  await workspace.getByRole("button", { name: "Edit Shot 2 · Chase" }).click();
  const panel = page.getByTestId("shot-edit-panel-shot-2");
  await expect(panel.getByLabel("Action")).toBeFocused();
  await panel.getByLabel("Approval").selectOption("approved");
  await panel.getByRole("button", { name: "Save shot" }).click();
  await expect(panel.getByText(/Không thể Approve Shot/)).toBeVisible();
  await expect(panel.getByLabel("Approval")).toHaveAttribute("aria-invalid", "true");

  await panel.getByLabel("Approval").selectOption("needs_review");
  await panel.getByLabel("Action").fill("The hero sprints after the thief.");
  await panel.getByLabel("Shot size").fill("medium");
  await panel.getByLabel("Camera motion").fill("handheld");
  await panel.getByRole("button", { name: "Save shot" }).click();

  await expect(panel.getByTestId("shot-review-prompt")).toHaveText(
    "medium handheld shot, the hero sprints after the thief",
  );
  await expect(
    panel.getByText("Final prompt (compiled from the shot fields, read-only)"),
  ).toBeVisible();

  const patches = requests.filter((request) => request.method === "PATCH");
  expect(patches.map((request) => request.body)).toEqual([
    { approval_state: "approved" },
    { action: "The hero sprints after the thief.", shot_size: "medium", camera_motion: "handheld" },
  ]);
  for (const request of requests) expect(request.pathname.startsWith("/api/")).toBe(true);

  await page.screenshot({ path: "test-results/shot-board-edit.png", fullPage: true });

  // The selected shot survives a reload.
  await page.reload();
  await expect(page.getByTestId("shot-workspace-shot-2")).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Shot navigator" }).getByTestId("shot-card-shot-2"),
  ).toHaveAttribute("aria-current", "true");
});

test("launches a batch after confirmation and lists frame-chain warnings and blocked shots", async ({
  page,
}) => {
  const requests = await mockApi(page, ({ method, pathname }) =>
    method === "POST" && pathname === "/api/v1/projects/test-project/batch/launch"
      ? {
          status: 201,
          body: {
            batch_id: "bat_e2e01",
            total_shots: 3,
            enqueued_count: 2,
            skipped_unchanged_count: 0,
            blocked_count: 1,
            enqueued_task_ids: ["task-1", "task-2"],
            skipped_shot_ids: [],
            blocked_shot_ids: ["shot-3"],
            warnings: [
              {
                code: "frame_chain_continuity_not_guaranteed",
                message:
                  "Connection 'Live gateway' first-frame conditioning is 'unknown': frame-chained shots will render, but the video may not start from the previous shot's last frame.",
                shot_ids: ["shot-2"],
              },
            ],
          },
        }
      : undefined,
  );

  await page.goto("/projects/test-project/shot-board");
  await expect(page.getByTestId("shot-card-shot-1")).toBeVisible();

  // Batch launch lives behind a toolbar toggle next to Generation settings.
  const batchToggle = page.getByRole("button", { name: "Batch launch", exact: true });
  await expect(batchToggle).toHaveAttribute("aria-expanded", "false");
  await batchToggle.click();
  await expect(batchToggle).toHaveAttribute("aria-expanded", "true");

  const launch = page.getByRole("button", { name: "Launch batch" });
  await expect(launch).toBeDisabled();
  await page.getByLabel("Connection").selectOption("conn-live");
  await expect(page.getByLabel("Model")).toHaveValue("muse-video-v1");
  await launch.click();

  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Launch batch for 3 shots?");
  await expect(dialog).toContainText("Live provider (muse2api): may consume quota");
  await dialog.getByRole("button", { name: "Confirm launch" }).click();

  const result = page.getByTestId("batch-launch-result");
  await expect(result.getByRole("status")).toHaveText(
    "Batch bat_e2e01: 2 queued, 0 skipped, 1 blocked of 3 shots.",
  );
  const warning = result.getByTestId("batch-warning");
  await expect(warning).toHaveAttribute("data-code", "frame_chain_continuity_not_guaranteed");
  await expect(warning).toContainText("Frame-chain continuity not guaranteed");
  await expect(warning.getByRole("listitem")).toHaveText(["Shot 2 · Chase"]);
  await expect(result.getByTestId("batch-blocked-shots")).toContainText(
    "Shot 3 · Rooftop: Prompt Linter blocker MISSING_REFERENCE: Reference image required for HERO.",
  );

  const launches = requests.filter((request) => request.pathname.endsWith("/batch/launch"));
  expect(launches.map((request) => request.body)).toEqual([
    { connection_id: "conn-live", model_id: "muse-video-v1", only_changed: true },
  ]);

  await page.screenshot({ path: "test-results/shot-board-batch.png", fullPage: true });

  await result.getByRole("link", { name: "Follow progress in Generation Queue" }).click();
  await expect(page).toHaveURL(/\/projects\/test-project\/queue-monitor$/);
  await expect(page.getByRole("heading", { name: "Generation Queue" })).toBeVisible();
});

import { expect, test } from "@playwright/test";
import { PROJECT_ID, emptyQueue, mockStudioApi } from "./support/studio-mocks";

// Task-based check: a shot whose last generation failed shows why on the Shot Board, and a new
// task is only requested (with force_retry) after an explicit confirmation.

const failedTask = {
  id: "task-failed-1",
  project_id: PROJECT_ID,
  scene_id: "scene-1",
  shot_id: "shot-1",
  connection_id: "conn-mock",
  task_type: "video",
  model_id: "muse-video-mock-v1",
  idempotency_key: "server-key",
  priority: 10,
  state: "failed",
  external_task_id: null,
  account_used: null,
  attempt_count: 3,
  max_attempts: 3,
  poll_count: 4,
  max_polls: 60,
  next_poll_delay_seconds: null,
  error_category: "provider_error",
  retryable: true,
  error_message_redacted: "Upstream returned HTTP 503 three times.",
  result_asset_id: null,
  take_id: null,
  history_log: [],
  pending_input: null,
};

test("a failed shot explains why and starts a new task only after confirmation", async ({
  page,
}) => {
  const requests = await mockStudioApi(page, ({ method, pathname, body }) => {
    if (method === "GET" && pathname === "/api/v1/queue/tasks") {
      return {
        status: 200,
        body: {
          ...emptyQueue,
          items: [failedTask],
          total: 1,
          summary: { total_tasks: 1, active_tasks: 0, failed_tasks: 1, awaiting_input_tasks: 0 },
        },
      };
    }
    if (method === "POST" && pathname === "/api/v1/queue/tasks") {
      const request = body as { force_retry: boolean };
      return {
        status: 201,
        body: {
          ...failedTask,
          id: "task-new-1",
          state: "queued",
          created_new: request.force_retry,
        },
      };
    }
    return undefined;
  });

  await page.goto(`/projects/${PROJECT_ID}/shot-board?shot=shot-1`);

  const activity = page.getByTestId("generation-activity");
  await expect(activity).toContainText("1 shot's latest generation failed");
  await expect(activity.getByRole("link", { name: /Open Generation Queue/ })).toHaveAttribute(
    "href",
    `/projects/${PROJECT_ID}/queue-monitor`,
  );

  const latest = page.getByTestId("shot-latest-task");
  await expect(latest).toContainText("Failed");
  await expect(latest).toContainText("Upstream returned HTTP 503 three times. (provider_error)");

  // Two connections exist, so the user picks one before generating.
  const retry = latest.getByRole("button", { name: "Start a new task" });
  await expect(retry).toBeDisabled();
  await page
    .getByTestId("generation-setup")
    .getByRole("button", { name: "Set up generation" })
    .click();
  await page.getByLabel("Connection").selectOption("conn-mock");
  await page.getByLabel("Model").fill("muse-video-mock-v1");
  await expect(page.getByTestId("generation-setup-summary")).toContainText(
    "Generating with Mock provider (mock, no provider cost)",
  );

  await retry.click();
  const confirm = page.getByRole("alertdialog");
  await expect(confirm).toContainText("always creates a new task");
  expect(requests.filter((r) => r.method === "POST")).toEqual([]);
  await confirm.getByRole("button", { name: "Confirm new task" }).click();

  await expect(page.getByTestId("shot-enqueue-result")).toContainText(
    "Queued video task task-new-1",
  );
  expect(
    requests
      .filter((r) => r.method === "POST" && r.pathname === "/api/v1/queue/tasks")
      .map((r) => (r.body as { force_retry: boolean }).force_retry),
  ).toEqual([true]);
});

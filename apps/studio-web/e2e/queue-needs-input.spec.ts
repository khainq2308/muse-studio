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

const QUESTION_TEXT = '<img src=x onerror="window.__injected = true">Which style should we use?';

function makeTask(state: "awaiting_input" | "running") {
  return {
    account_used: null,
    attempt_count: 1,
    connection_id: "connection-1",
    error_category: null,
    error_message_redacted: null,
    external_task_id: "video:ext-1",
    history_log: ["Provider asked a question."],
    id: "task-awaiting-1",
    idempotency_key: "idem-1",
    max_attempts: 3,
    max_polls: 60,
    model_id: "muse-video-v1",
    next_poll_delay_seconds: 15,
    poll_count: 2,
    priority: 10,
    project_id: "test-project",
    result_asset_id: null,
    retryable: true,
    scene_id: null,
    shot_id: "shot-1",
    state,
    take_id: null,
    task_type: "video",
    pending_input:
      state === "awaiting_input"
        ? {
            kind: "question",
            text: QUESTION_TEXT,
            options: ["Cinematic", "Anime"],
            asked_at: 1761200000.5,
          }
        : null,
  };
}

async function mockApi(page: Page, replyStatus: 200 | 409) {
  let answered = false;
  const replyBodies: unknown[] = [];

  await page.route("**/healthz", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' }),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/v1/queue/tasks/task-awaiting-1/reply" && request.method() === "POST") {
      replyBodies.push(request.postDataJSON());
      answered = true;
      await route.fulfill(
        replyStatus === 200
          ? {
              status: 200,
              contentType: "application/json",
              body: JSON.stringify(makeTask("running")),
            }
          : {
              status: 409,
              contentType: "application/json",
              body: JSON.stringify({
                detail: "Task is not awaiting input.",
                category: "input_not_pending",
                retryable: false,
              }),
            },
      );
      return;
    }
    const body =
      pathname === "/api/v1/queue/tasks"
        ? {
            items: [makeTask(answered ? "running" : "awaiting_input")],
            total: 1,
            limit: 50,
            offset: 0,
            has_more: false,
            summary: { total_tasks: 1, active_tasks: 1, failed_tasks: 0 },
          }
        : pathname === "/api/v1/projects"
          ? [testProject]
          : [];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });

  return { replyBodies };
}

test("an operator answers a provider question with an option", async ({ page }) => {
  const { replyBodies } = await mockApi(page, 200);
  await page.goto("/projects/test-project/queue-monitor");

  const task = page.getByTestId("queue-task-task-awaiting-1");
  await expect(task.getByTestId("queue-task-state")).toHaveText("Awaiting input");
  await expect(page.getByTestId("queue-awaiting-input-banner")).toContainText(
    "1 task on this page is waiting for your answer.",
  );
  const question = page.getByRole("region", { name: "Question" });
  await expect(question).toContainText(QUESTION_TEXT);
  await expect(question.locator("img")).toHaveCount(0);
  expect(
    await page.evaluate(() => (window as { __injected?: boolean }).__injected),
  ).toBeUndefined();
  await expect(task.getByRole("button", { name: "Pause task task-awaiting-1" })).toHaveCount(0);
  await expect(task.getByRole("button", { name: "Cancel task task-awaiting-1" })).toBeEnabled();

  await page.screenshot({ path: "test-results/queue-monitor-awaiting-input.png", fullPage: true });

  await question.getByRole("button", { name: "Anime" }).click();

  await expect.poll(() => replyBodies).toEqual([{ choice: "Anime" }]);
  await expect(task.getByTestId("queue-task-state")).toHaveText("Running");
  await expect(page.getByRole("region", { name: "Question" })).toHaveCount(0);
  await expect(page.getByTestId("queue-awaiting-input-banner")).toHaveCount(0);
});

test("a reply to a question that is no longer pending explains the conflict", async ({ page }) => {
  const { replyBodies } = await mockApi(page, 409);
  await page.goto("/projects/test-project/queue-monitor");

  const question = page.getByRole("region", { name: "Question" });
  await question.getByRole("textbox", { name: "Or write an answer" }).fill("  Noir  ");
  await question.getByRole("button", { name: "Send answer" }).click();

  await expect.poll(() => replyBodies).toEqual([{ text: "Noir" }]);
  await expect(page.getByRole("alert")).toContainText(
    "This question is no longer waiting for an answer.",
  );
  await expect(page.getByTestId("queue-task-state")).toHaveText("Running");
});

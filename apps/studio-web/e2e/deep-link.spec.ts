import { expect, test } from "@playwright/test";

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

test("production build deep link renders the Queue Monitor and uses the same-origin queue API", async ({
  page,
}) => {
  const requestedFetchUrls: Array<{ origin: string; pathname: string }> = [];

  page.on("request", (request) => {
    if (request.resourceType() !== "fetch" && request.resourceType() !== "xhr") return;
    const url = new URL(request.url());
    requestedFetchUrls.push({ origin: url.origin, pathname: url.pathname });
  });

  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    });
  });

  await page.route("**/api/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const body =
      pathname === "/api/v1/queue/tasks"
        ? {
            items: [],
            total: 0,
            limit: 50,
            offset: 0,
            has_more: false,
            summary: { total_tasks: 0, active_tasks: 0, failed_tasks: 0 },
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

  await page.goto("/projects/test-project/queue-monitor");

  const route = page.getByTestId("project-module-route");
  await expect(route).toHaveAttribute("data-project-id", "test-project");
  await expect(route).toHaveAttribute("data-module", "queue-monitor");
  await expect(page.getByRole("heading", { name: "MuseStudio" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Queue Monitor" })).toBeVisible();
  await expect(page.getByTestId("queue-monitor-page")).toBeVisible();
  await expect(page.getByText("No queue tasks yet", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Projects", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Provider connections" })).toHaveCount(0);
  await expect.poll(() => requestedFetchUrls.map(({ pathname }) => pathname)).toContain("/healthz");
  expect(requestedFetchUrls.map(({ pathname }) => pathname)).toContain("/api/v1/queue/tasks");
  // The project list is the only contract for validating that a deep-linked project exists.
  expect(requestedFetchUrls.map(({ pathname }) => pathname)).toContain("/api/v1/projects");
  expect(requestedFetchUrls.map(({ pathname }) => pathname)).not.toContain("/api/v1/connections");

  await page.screenshot({ path: "test-results/queue-monitor.png", fullPage: true });

  const appOrigin = new URL(page.url()).origin;
  expect(requestedFetchUrls.length).toBeGreaterThan(0);
  for (const request of requestedFetchUrls) {
    expect(request.origin).toBe(appOrigin);
    expect(request.pathname === "/healthz" || request.pathname.startsWith("/api/")).toBe(true);
  }
});

test("invalid project modules render the same 404 page as unknown routes", async ({ page }) => {
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    });
  });
  await page.route("**/api/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([testProject]),
    });
  });

  await page.goto("/projects/test-project/not-a-real-module");

  await expect(page).toHaveURL(/\/projects\/test-project\/not-a-real-module$/);
  await expect(page.getByRole("heading", { name: "404 - Page not found" })).toBeVisible();
  await expect(page.getByTestId("project-module-route")).toHaveCount(0);
});

test("a nonexistent project shows a not-found state instead of an empty module", async ({
  page,
}) => {
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    });
  });
  await page.route("**/api/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([testProject]),
    });
  });

  await page.goto("/projects/missing-project/queue-monitor");

  await expect(page.getByRole("heading", { name: "MuseStudio" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Project not found" })).toBeVisible();
  await expect(page.getByTestId("queue-monitor-page")).toHaveCount(0);
  await page.getByRole("link", { name: "Back to workspace overview" }).click();
  await expect(page).toHaveURL(/\/$/);
});

test("unknown routes render an explicit 404 page", async ({ page }) => {
  await page.goto("/not-a-real-route");

  await expect(page.getByRole("heading", { name: "404 - Page not found" })).toBeVisible();
  await expect(page.getByText("The page you requested doesn't exist.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "MuseStudio" })).toHaveCount(0);
});

import { expect, test } from "@playwright/test";

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
  expect(requestedFetchUrls.map(({ pathname }) => pathname)).not.toContain("/api/v1/projects");
  expect(requestedFetchUrls.map(({ pathname }) => pathname)).not.toContain("/api/v1/connections");

  await page.screenshot({ path: "test-results/queue-monitor.png", fullPage: true });

  const appOrigin = new URL(page.url()).origin;
  expect(requestedFetchUrls.length).toBeGreaterThan(0);
  for (const request of requestedFetchUrls) {
    expect(request.origin).toBe(appOrigin);
    expect(request.pathname === "/healthz" || request.pathname.startsWith("/api/")).toBe(true);
  }
});

test("invalid project modules redirect to the overview route", async ({ page }) => {
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
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });

  await page.goto("/projects/test-project/not-a-real-module");

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("project-module-route")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "MuseStudio" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Projects", exact: true })).toBeVisible();

  await expect
    .poll(() => requestedFetchUrls.map(({ pathname }) => pathname))
    .toEqual(expect.arrayContaining(["/api/v1/projects", "/api/v1/connections"]));

  const appOrigin = new URL(page.url()).origin;
  expect(requestedFetchUrls.length).toBeGreaterThan(0);
  for (const request of requestedFetchUrls) {
    expect(request.origin).toBe(appOrigin);
    expect(request.pathname === "/healthz" || request.pathname.startsWith("/api/")).toBe(true);
  }
});

test("unknown routes render an explicit 404 page", async ({ page }) => {
  await page.goto("/not-a-real-route");

  await expect(page.getByRole("heading", { name: "404 - Page not found" })).toBeVisible();
  await expect(page.getByText("The page you requested doesn't exist.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "MuseStudio" })).toHaveCount(0);
});

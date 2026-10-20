import { expect, test } from "@playwright/test";

test("production build deep link matches the registered project-module route", async ({ page }) => {
  const requestedApiUrls: Array<{ origin: string; pathname: string }> = [];

  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes("/api/") || url.pathname.includes("/healthz")) {
      requestedApiUrls.push({ origin: url.origin, pathname: url.pathname });
    }
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

  await page.goto("/projects/test-project/queue-monitor");

  const route = page.getByTestId("project-module-route");
  await expect(route).toHaveAttribute("data-project-id", "test-project");
  await expect(route).toHaveAttribute("data-module", "queue-monitor");
  await expect(page.getByRole("heading", { name: "MuseStudio" })).toBeVisible();
  await expect
    .poll(() => requestedApiUrls.map(({ pathname }) => pathname))
    .toEqual(expect.arrayContaining(["/api/v1/projects", "/api/v1/connections"]));

  const appOrigin = new URL(page.url()).origin;
  expect(requestedApiUrls.length).toBeGreaterThan(0);
  for (const request of requestedApiUrls) {
    expect(request.origin).toBe(appOrigin);
    expect(request.pathname === "/healthz" || request.pathname.startsWith("/api/")).toBe(true);
  }
});

test("invalid project modules redirect instead of rendering the app shell", async ({ page }) => {
  await page.goto("/projects/test-project/not-a-real-module");

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "MuseStudio" })).toBeVisible();
});

test("unknown routes do not masquerade as the dashboard", async ({ page }) => {
  await page.goto("/not-a-real-route");

  await expect(page.getByRole("heading", { name: "MuseStudio" })).toHaveCount(0);
});

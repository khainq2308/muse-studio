import { expect, test } from "@playwright/test";

test("production SPA fallback renders a project-module deep link with absolute API paths", async ({
  page,
}) => {
  const requestedApiPaths: string[] = [];

  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    });
  });

  await page.route("**/api/v1/projects", async (route) => {
    requestedApiPaths.push(new URL(route.request().url()).pathname);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });

  await page.route("**/api/v1/connections", async (route) => {
    requestedApiPaths.push(new URL(route.request().url()).pathname);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });

  await page.goto("/projects/test-project/queue-monitor");

  await expect(page.getByRole("heading", { name: "MuseStudio" })).toBeVisible();
  await expect
    .poll(() => requestedApiPaths)
    .toEqual(expect.arrayContaining(["/api/v1/projects", "/api/v1/connections"]));
  expect(requestedApiPaths.every((path) => path.startsWith("/api/"))).toBe(true);
});

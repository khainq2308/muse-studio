import { expect, test } from "@playwright/test";

test("production build deep link matches the registered project-module route", async ({
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

  const route = page.getByTestId("project-module-route");
  await expect(route).toHaveAttribute("data-project-id", "test-project");
  await expect(route).toHaveAttribute("data-module", "queue-monitor");
  await expect(page.getByRole("heading", { name: "MuseStudio" })).toBeVisible();
  await expect
    .poll(() => requestedApiPaths)
    .toEqual(expect.arrayContaining(["/api/v1/projects", "/api/v1/connections"]));
  expect(requestedApiPaths.every((path) => path.startsWith("/api/"))).toBe(true);
});

test("unknown routes do not masquerade as the dashboard", async ({ page }) => {
  await page.goto("/not-a-real-route");

  await expect(page.getByRole("heading", { name: "MuseStudio" })).toHaveCount(0);
});

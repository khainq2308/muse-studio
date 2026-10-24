import { expect, test } from "@playwright/test";
import { mockStudioApi } from "./support/studio-mocks";

test.describe("i18n language switching smoke test", () => {
  test("opens with Vietnamese by default, switches to English and persists across reloads", async ({
    page,
  }) => {
    await mockStudioApi(page);
    await page.goto("/");

    // 1. Initial load has documentElement.lang === "vi"
    await expect(page.locator("html")).toHaveAttribute("lang", "vi");

    // 2. Default Vietnamese UI elements are rendered
    const workspaceNav = page.getByRole("navigation", { name: /Không gian làm việc|Workspace/i });
    await expect(workspaceNav.getByRole("link", { name: "Tổng quan" })).toBeVisible();
    await expect(workspaceNav.getByRole("link", { name: "Dự án" })).toBeVisible();
    await expect(workspaceNav.getByRole("link", { name: "Kết nối" })).toBeVisible();
    await expect(workspaceNav.getByRole("link", { name: "Hệ thống" })).toBeVisible();

    // 3. Language switcher exists and indicates current language
    const switcher = page.getByTestId("language-switcher");
    await expect(switcher).toBeVisible();
    await expect(switcher).toHaveText(/VI/i);

    // 4. Switch to English
    await switcher.click();

    // 5. Document lang is updated to "en" and UI updates immediately without reload
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(switcher).toHaveText(/EN/i);
    await expect(workspaceNav.getByRole("link", { name: "Overview" })).toBeVisible();
    await expect(workspaceNav.getByRole("link", { name: "Projects" })).toBeVisible();
    await expect(workspaceNav.getByRole("link", { name: "Connections" })).toBeVisible();
    await expect(workspaceNav.getByRole("link", { name: "System" })).toBeVisible();

    // 6. Reload and verify persistence from localStorage
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(workspaceNav.getByRole("link", { name: "Overview" })).toBeVisible();

    // 7. Toggle back to Vietnamese
    await page.getByTestId("language-switcher").click();
    await expect(page.locator("html")).toHaveAttribute("lang", "vi");
    await expect(workspaceNav.getByRole("link", { name: "Tổng quan" })).toBeVisible();
  });
});

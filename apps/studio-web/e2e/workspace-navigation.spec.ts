import { expect, test } from "@playwright/test";
import { PROJECT_ID, mockStudioApi } from "./support/studio-mocks";

// Task-based checks for the workspace/project split: what a user sees first, how they reach the
// work that is waiting, and that workspace-wide tools are not presented as project tools.

test("the overview names each project's next step and opens the blocked shot that explains it", async ({
  page,
}) => {
  await mockStudioApi(page);
  await page.goto("/");

  const recent = page.getByRole("list", {
    name: /Recently updated projects|Cập nhật gần đây|Dự án gần đây/i,
  });
  const active = recent.getByRole("listitem", { name: "Test project" });
  // Mock shot-3 carries a lint blocker; shot-1 is the only shot with a selected take.
  await expect(active).toContainText(/Resolve 1 blocked shot|Xử lý 1 shot bị chặn/i);
  await expect(active.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
  await expect(recent.getByRole("listitem", { name: "Episode 02 - Glass Citadel" })).toContainText(
    /Import a script|Nhập kịch bản/i,
  );

  await active
    .getByRole("link", { name: /Open first blocked shot|Mở shot bị chặn đầu tiên/i })
    .click();
  await expect(page).toHaveURL(`/projects/${PROJECT_ID}/shot-board?shot=shot-3&tab=prompt`);
  const panel = page.getByTestId("shot-workspace-shot-3");
  await expect(
    panel.getByRole("tab", { name: /^(?:Prompt & lint|Prompt & kiểm tra)/i }),
  ).toHaveAttribute("aria-selected", "true");
});

test("project navigation shows the production steps in order and keeps workspace tools out", async ({
  page,
}) => {
  await mockStudioApi(page);
  await page.goto(`/projects/${PROJECT_ID}/shot-board`);

  const projectNav = page.getByRole("navigation", { name: /Project modules|Module dự án/i });
  await expect(
    projectNav.getByRole("list", { name: /Production steps|Các bước sản xuất/i }).getByRole("link"),
  ).toHaveText([
    "1Kịch bản & Story Bible",
    "2Shot Board",
    "3Hàng đợi sinh media",
    "4Timeline & Xuất video",
  ]);
  await expect(
    projectNav.getByRole("link", { name: /Connections|System|Kết nối|Hệ thống/ }),
  ).toHaveCount(0);

  const workspaceNav = page.getByRole("navigation", { name: /Workspace|Không gian làm việc/i });
  await workspaceNav.getByRole("link", { name: /System|Hệ thống/i }).click();
  await expect(page).toHaveURL("/system");
  await expect(
    page.getByText(/Workspace-wide tools|Công cụ toàn không gian làm việc/i),
  ).toBeVisible();
  await expect(page.getByRole("navigation", { name: /Project modules|Module dự án/i })).toHaveCount(
    0,
  );
});

for (const [from, to] of [
  [`/projects/${PROJECT_ID}/connections`, "/connections"],
  [`/projects/${PROJECT_ID}/system-ops`, `/projects/${PROJECT_ID}/settings`],
  [`/projects/${PROJECT_ID}/projects`, `/projects/${PROJECT_ID}/settings`],
] as const) {
  test(`a bookmarked pre-split URL ${from} redirects to ${to}`, async ({ page }) => {
    await mockStudioApi(page);
    await page.goto(from);
    await expect(page).toHaveURL(to);
  });
}

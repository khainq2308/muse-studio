import { expect, test } from "@playwright/test";
import { PROJECT_ID, mockStudioApi } from "./support/studio-mocks";

// Task-based checks for the workspace/project split: what a user sees first, how they reach the
// work that is waiting, and that workspace-wide tools are not presented as project tools.

test("the overview names each project's next step and opens the blocked shot that explains it", async ({
  page,
}) => {
  await mockStudioApi(page);
  await page.goto("/");

  const recent = page.getByRole("list", { name: "Recently updated projects" });
  const active = recent.getByRole("listitem", { name: "Test project" });
  // Mock shot-3 carries a lint blocker; shot-1 is the only shot with a selected take.
  await expect(active).toContainText("Resolve 1 blocked shot");
  await expect(active.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
  await expect(recent.getByRole("listitem", { name: "Episode 02 - Glass Citadel" })).toContainText(
    "Import a script",
  );

  await active.getByRole("link", { name: /Open first blocked shot/ }).click();
  await expect(page).toHaveURL(`/projects/${PROJECT_ID}/shot-board?shot=shot-3&tab=prompt`);
  const panel = page.getByTestId("shot-workspace-shot-3");
  await expect(panel.getByRole("tab", { name: /^Prompt & lint/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("project navigation shows the production steps in order and keeps workspace tools out", async ({
  page,
}) => {
  await mockStudioApi(page);
  await page.goto(`/projects/${PROJECT_ID}/shot-board`);

  const projectNav = page.getByRole("navigation", { name: "Project modules" });
  await expect(
    projectNav.getByRole("list", { name: "Production steps" }).getByRole("link"),
  ).toHaveText(["1Script & Story Bible", "2Shot Board", "3Generation Queue", "4Timeline & Export"]);
  await expect(projectNav.getByRole("link", { name: /Connections|System/ })).toHaveCount(0);

  const workspaceNav = page.getByRole("navigation", { name: "Workspace" });
  await workspaceNav.getByRole("link", { name: "System" }).click();
  await expect(page).toHaveURL("/system");
  await expect(page.getByText(/Workspace-wide tools/)).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Project modules" })).toHaveCount(0);
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

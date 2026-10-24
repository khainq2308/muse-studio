import { expect, test, type Page } from "@playwright/test";
import { PROJECT_ID, createdWorkflow, mockStudioApi } from "./support/studio-mocks";

/**
 * Viewport evidence: each workspace page and project module loaded with populated mocks, captured as a full-page
 * screenshot under test-results/ui-evidence/, and checked for horizontal page overflow.
 */

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
] as const;

interface PageCase {
  /** Screenshot name. */
  name: string;
  path: string;
  /** Project module rendered at `path`; absent for workspace pages. */
  module?: string;
  /** Waits until the populated state is on screen (and drives it there when needed). */
  ready: (page: Page) => Promise<void>;
}

const PAGES: PageCase[] = [
  {
    name: "overview",
    path: "/",
    ready: async (page) => {
      await expect(page.getByRole("list", { name: "Recently updated projects" })).toBeVisible();
      await expect(page.getByText("Next step").first()).toBeVisible();
    },
  },
  {
    name: "projects",
    path: "/projects",
    ready: async (page) => {
      await expect(page.getByText("Episode 02 - Glass Citadel")).toBeVisible();
    },
  },
  {
    name: "connections",
    path: "/connections",
    ready: async (page) => {
      await expect(page.getByText("Primary account", { exact: true })).toBeVisible();
    },
  },
  {
    name: "system",
    path: "/system",
    ready: async (page) => {
      await expect(page.getByRole("region", { name: "Observability summary" })).toBeVisible();
    },
  },
  {
    name: "settings",
    path: `/projects/${PROJECT_ID}/settings`,
    module: "settings",
    ready: async (page) => {
      await expect(page.getByText("Imported script revision v2")).toBeVisible();
      await expect(page.getByRole("region", { name: "Project backups" })).toContainText(
        "Project Backups (2)",
      );
    },
  },
  {
    name: "workflow-canvas",
    path: `/projects/${PROJECT_ID}/workflow-canvas`,
    module: "workflow-canvas",
    ready: async (page) => {
      await page.getByRole("button", { name: "Initialize M3 Pipeline DAG" }).click();
      await expect(page.locator(".react-flow__node")).toHaveCount(7);
    },
  },
  {
    name: "timeline-editor",
    path: `/projects/${PROJECT_ID}/timeline-editor`,
    module: "timeline-editor",
    ready: async (page) => {
      await expect(page.getByText("Shot: shot-1")).toBeVisible();
    },
  },
  {
    name: "shot-board",
    path: `/projects/${PROJECT_ID}/shot-board`,
    module: "shot-board",
    ready: async (page) => {
      // Shot 1 opens by default on its Takes tab (it has takes), with frame thumbnails loaded.
      const panel = page.getByTestId("shot-workspace-shot-1");
      await expect(panel.getByRole("tab", { name: /^Takes/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      // Frames load lazily near the viewport; on narrow screens the take list starts below it.
      const take = panel.getByTestId("take-take-1");
      await take.scrollIntoViewIfNeeded();
      await expect(take.getByRole("img", { name: "Take 1 first frame" })).toHaveAttribute(
        "src",
        /^blob:/,
      );
      await page.evaluate(() => window.scrollTo(0, 0));
    },
  },
  {
    name: "script-studio",
    path: `/projects/${PROJECT_ID}/script-studio`,
    module: "script-studio",
    ready: async (page) => {
      await expect(page.getByText("Detective Linh").first()).toBeVisible();
    },
  },
];

/** Describes up to 8 elements whose right edge extends past the viewport, for the report. */
async function overflowCulprits(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const limit = window.innerWidth + 1;
    const describe = (element: Element) => {
      const id = element.id ? `#${element.id}` : "";
      const testId = element.getAttribute("data-testid");
      const label = element.getAttribute("aria-label");
      const className =
        typeof element.className === "string"
          ? `.${element.className.trim().split(/\s+/).slice(0, 4).join(".")}`
          : "";
      const right = Math.round(element.getBoundingClientRect().right);
      return `${element.tagName.toLowerCase()}${id}${testId ? `[data-testid=${testId}]` : ""}${
        label ? `[aria-label="${label}"]` : ""
      }${className} right=${right}`;
    };
    const offenders = Array.from(document.body.querySelectorAll("*")).filter(
      (element) => element.getBoundingClientRect().right > limit,
    );
    // Report the outermost offenders only; their descendants overflow by inheritance.
    return offenders
      .filter((element) => !offenders.some((other) => other !== element && other.contains(element)))
      .slice(0, 8)
      .map(describe);
  });
}

for (const viewport of VIEWPORTS) {
  const size = `${viewport.width}x${viewport.height}`;
  test.describe(`viewport ${size}`, () => {
    test.use({ viewport });

    for (const { name, path, module, ready } of PAGES) {
      test(`${name} renders without horizontal overflow`, async ({ page }) => {
        await mockStudioApi(page, ({ method, pathname }) =>
          method === "POST" && pathname === `/api/v1/projects/${PROJECT_ID}/workflows`
            ? { status: 201, body: createdWorkflow }
            : undefined,
        );

        await page.goto(path);
        if (module) {
          await expect(page.getByTestId("project-module-route")).toHaveAttribute(
            "data-module",
            module,
          );
        }
        await ready(page);

        await page.screenshot({
          path: `test-results/ui-evidence/${name}-${size}.png`,
          fullPage: true,
        });

        const { scrollWidth, innerWidth } = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          innerWidth: window.innerWidth,
        }));
        const culprits = scrollWidth > innerWidth + 1 ? await overflowCulprits(page) : [];
        expect(
          scrollWidth,
          `${name} @ ${size}: documentElement.scrollWidth=${scrollWidth} > innerWidth=${innerWidth}. Overflowing elements:\n${culprits.join("\n")}`,
        ).toBeLessThanOrEqual(innerWidth + 1);
      });
    }
  });
}

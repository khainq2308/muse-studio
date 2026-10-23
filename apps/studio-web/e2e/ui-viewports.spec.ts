import { expect, test, type Page } from "@playwright/test";
import { PROJECT_ID, createdWorkflow, mockStudioApi } from "./support/studio-mocks";

/**
 * Viewport evidence: each production module loaded with populated mocks, captured as a full-page
 * screenshot under test-results/ui-evidence/, and checked for horizontal page overflow.
 */

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
] as const;

interface ModuleCase {
  module: string;
  /** Waits until the populated state is on screen (and drives it there when needed). */
  ready: (page: Page) => Promise<void>;
}

const MODULES: ModuleCase[] = [
  {
    module: "projects",
    ready: async (page) => {
      await expect(page.getByText("Imported script revision v2")).toBeVisible();
    },
  },
  {
    module: "connections",
    ready: async (page) => {
      await expect(page.getByText("Primary account", { exact: true })).toBeVisible();
    },
  },
  {
    module: "workflow-canvas",
    ready: async (page) => {
      await page.getByRole("button", { name: "Initialize M3 Pipeline DAG" }).click();
      await expect(page.locator(".react-flow__node")).toHaveCount(7);
    },
  },
  {
    module: "timeline-editor",
    ready: async (page) => {
      await expect(page.getByText("Shot: shot-1")).toBeVisible();
    },
  },
  {
    module: "system-ops",
    ready: async (page) => {
      await expect(page.getByRole("region", { name: "Project backups" })).toContainText(
        "Project Backups (2)",
      );
      await expect(page.getByRole("region", { name: "Observability summary" })).toBeVisible();
    },
  },
  {
    module: "shot-board",
    ready: async (page) => {
      await expect(page.getByTestId("shot-card-shot-1")).toBeVisible();
    },
  },
  {
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

    for (const { module, ready } of MODULES) {
      test(`${module} renders without horizontal overflow`, async ({ page }) => {
        await mockStudioApi(page, ({ method, pathname }) =>
          method === "POST" && pathname === `/api/v1/projects/${PROJECT_ID}/workflows`
            ? { status: 201, body: createdWorkflow }
            : undefined,
        );

        await page.goto(`/projects/${PROJECT_ID}/${module}`);
        await expect(page.getByTestId("project-module-route")).toHaveAttribute(
          "data-module",
          module,
        );
        await ready(page);

        await page.screenshot({
          path: `test-results/ui-evidence/${module}-${size}.png`,
          fullPage: true,
        });

        const { scrollWidth, innerWidth } = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          innerWidth: window.innerWidth,
        }));
        const culprits = scrollWidth > innerWidth + 1 ? await overflowCulprits(page) : [];
        expect(
          scrollWidth,
          `${module} @ ${size}: documentElement.scrollWidth=${scrollWidth} > innerWidth=${innerWidth}. Overflowing elements:\n${culprits.join("\n")}`,
        ).toBeLessThanOrEqual(innerWidth + 1);
      });
    }
  });
}

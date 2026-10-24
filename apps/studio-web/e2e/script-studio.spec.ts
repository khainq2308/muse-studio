import { expect, test, type Page } from "@playwright/test";

const testProject = {
  id: "test-project",
  name: "Test project",
  description: "",
  aspect_ratio: "16:9",
  asset_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  current_revision_num: 1,
  fps: 24,
  language: "vi",
  output_profile: "1080p_h264",
  style_preset: "2d_anime_cinematic",
};

const initialBible = [
  {
    id: "bib_char1",
    entry_type: "character",
    code_name: "char_linh",
    display_name: "Detective Linh",
    immutable_traits: "2D anime detective with sharp silver eyes",
    mutable_attributes: { current_outfit: "charcoal trench coat" },
    reference_asset_ids: [],
    requires_reference_lock: true,
  },
  {
    id: "bib_style1",
    entry_type: "style",
    code_name: "style_noir",
    display_name: "2D Anime Noir",
    immutable_traits: "hand-drawn cel shading with rain reflections",
    mutable_attributes: {
      color_palette: "teal and amber",
      negative_constraints: ["3d cgi", "deformed hands"],
    },
    reference_asset_ids: [],
    requires_reference_lock: false,
  },
];

interface Recorded {
  method: string;
  pathname: string;
  search: string;
  body: unknown;
}

async function mockApi(
  page: Page,
  overrides: (request: Recorded) => { status: number; body: unknown } | undefined,
) {
  const requests: Recorded[] = [];
  await page.route("**/healthz", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' }),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    let parsedBody: unknown;
    const rawBody = request.postData();
    if (rawBody) {
      try {
        parsedBody = JSON.parse(rawBody);
      } catch {
        parsedBody = rawBody;
      }
    }
    const recorded: Recorded = {
      method: request.method(),
      pathname: url.pathname,
      search: url.search,
      body: parsedBody,
    };
    requests.push(recorded);

    const custom = overrides(recorded);
    if (custom) {
      await route.fulfill({
        status: custom.status,
        contentType: "application/json",
        body: JSON.stringify(custom.body),
      });
      return;
    }

    const body =
      recorded.pathname === "/api/v1/projects"
        ? [testProject]
        : recorded.pathname === "/api/v1/projects/test-project/story-bible"
          ? initialBible
          : recorded.pathname === "/api/v1/projects/test-project/shots"
            ? []
            : recorded.pathname === "/api/v1/queue/tasks"
              ? {
                  items: [],
                  total: 0,
                  limit: 50,
                  offset: 0,
                  has_more: false,
                  summary: {
                    total_tasks: 0,
                    active_tasks: 0,
                    failed_tasks: 0,
                    awaiting_input_tasks: 0,
                  },
                }
              : null;
    await route.fulfill({
      status: body === null ? 404 : 200,
      contentType: "application/json",
      body: JSON.stringify(body ?? { detail: "Not mocked", category: "not_found" }),
    });
  });
  return requests;
}

test("imports a script with replace_existing confirmation and edits a Story Bible entry", async ({
  page,
}) => {
  let importCount = 0;
  const requests = await mockApi(page, ({ method, pathname }) => {
    if (method === "POST" && pathname === "/api/v1/projects/test-project/scripts/import") {
      importCount += 1;
      if (importCount === 1) {
        return {
          status: 409,
          body: {
            detail:
              "Re-applying a script would delete every existing shot together with its generated takes. Retry with replace_existing=true to confirm.",
            category: "conflict",
            retryable: false,
          },
        };
      }
      return {
        status: 201,
        body: {
          script_revision_id: "scr_e2e01",
          version_number: 2,
          raw_content_preserved: true,
          diff_summary: "Parsed 1 scene and 2 shots.",
          detected_characters: 1,
          detected_locations: 1,
          detected_scenes: 1,
          generated_shots: 2,
          warnings: [],
        },
      };
    }
    if (method === "POST" && pathname === "/api/v1/projects/test-project/story-bible") {
      return {
        status: 201,
        body: {
          id: "bib_style1",
          code_name: "style_noir",
          status: "recompiled_downstream_shots",
        },
      };
    }
    return undefined;
  });

  await page.goto("/projects/test-project/script-studio");

  await expect(page.getByRole("heading", { name: "Script & Story Bible" })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Project modules" });
  await expect(nav.getByRole("link", { name: "Script & Story Bible" })).toHaveAttribute(
    "aria-current",
    "page",
  );

  const importPanel = page.getByTestId("script-import-panel");
  await importPanel.getByRole("button", { name: "Insert sample script" }).click();
  await importPanel
    .getByRole("checkbox", { name: /Apply changes directly to Story Bible & shots/ })
    .check();
  await expect(importPanel.getByTestId("existing-pipeline-overwrite-warning")).toBeVisible();
  await importPanel.getByRole("button", { name: "Import & apply script" }).click();

  await expect(
    importPanel.getByText(/Retry with replace_existing=true to confirm/).first(),
  ).toBeVisible();

  await importPanel
    .getByRole("checkbox", { name: /Confirm replacing existing shots and generated takes/ })
    .check();
  await importPanel.getByRole("button", { name: "Import & apply script" }).click();

  await expect(page.getByTestId("script-import-result")).toContainText(
    "Imported script revision v2 (scr_e2e01)",
  );

  await page.getByRole("button", { name: "Edit 2D Anime Noir (style_noir)" }).click();
  const editor = page.getByTestId("story-bible-editor-style_noir");
  await editor.getByLabel("Color palette").fill("emerald and gold noir");
  await editor.getByRole("button", { name: "Save entry" }).click();

  await expect(editor.getByTestId("story-bible-upsert-result")).toContainText(
    "Saved style_noir (bib_style1). Downstream project shots were recompiled.",
  );

  const bibleSaves = requests.filter(
    (req) => req.method === "POST" && req.pathname.endsWith("/story-bible"),
  );
  expect(bibleSaves.map((req) => req.body)).toEqual([
    {
      entry_type: "style",
      code_name: "style_noir",
      display_name: "2D Anime Noir",
      immutable_traits: "hand-drawn cel shading with rain reflections",
      mutable_attributes: {
        color_palette: "emerald and gold noir",
        negative_constraints: ["3d cgi", "deformed hands"],
        forbidden_terms: [],
      },
      reference_asset_ids: [],
      requires_reference_lock: false,
    },
  ]);
});

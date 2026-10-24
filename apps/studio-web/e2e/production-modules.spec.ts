import { expect, test } from "@playwright/test";
import {
  PROJECT_ID,
  approvalResult,
  assembledFromCustomClips,
  backups,
  createdWorkflow,
  exportLaunched,
  exportStatus,
  mockStudioApi,
  orphanCleanup,
  testProject,
  waitingApprovalRun,
} from "./support/studio-mocks";

const ASPECT_RATIO_PATTERN = /^(16:9|9:16|1:1|21:9)$/;

test.describe("Projects and project settings", () => {
  test("creates a project from the workspace list and duplicates settings from the project", async ({
    page,
  }) => {
    const requests = await mockStudioApi(page, ({ method, pathname, body }) => {
      if (method === "POST" && pathname === "/api/v1/projects") {
        const request = body as Record<string, unknown>;
        return {
          status: 201,
          body: {
            ...testProject,
            ...request,
            id: "created-project",
            asset_count: 0,
            current_revision_num: 1,
          },
        };
      }
      if (method === "POST" && pathname === `/api/v1/projects/${PROJECT_ID}/duplicate`) {
        return {
          status: 201,
          body: {
            ...testProject,
            id: "duplicated-project",
            name: "Test project (copy)",
            asset_count: 0,
            current_revision_num: 1,
          },
        };
      }
      return undefined;
    });

    await page.goto("/projects");
    await expect(
      page.getByRole("heading", { name: /^(?:Projects|Dự án)$/i, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: /^(?:All projects|Tất cả dự án)/i }),
    ).toContainText("Episode 02 - Glass Citadel");

    await page.getByRole("button", { name: /^(?:New project|Dự án mới)$/i }).click();
    const form = page.getByRole("form", { name: /^(?:Create new project|Tạo dự án mới)$/i });
    await form.getByLabel(/^(?:Project name|Tên dự án)$/i).fill("Episode 03 - Vertical teaser");
    await expect(
      form.getByLabel(/^(?:Aspect ratio|Tỉ lệ khung hình)/i).locator("option"),
    ).toHaveText(["16:9", "9:16", "1:1", "21:9"]);
    await form.getByLabel(/^(?:Aspect ratio|Tỉ lệ khung hình)/i).selectOption("9:16");
    await form.getByRole("button", { name: /^(?:Create project|Tạo dự án)$/i }).click();

    await expect(page.getByRole("status")).toContainText(
      /(?:Created project Episode 03 - Vertical teaser|Đã tạo dự án Episode 03 - Vertical teaser)/i,
    );
    const creates = requests.filter(
      (request) => request.method === "POST" && request.pathname === "/api/v1/projects",
    );
    expect(creates).toHaveLength(1);
    const createBody = creates[0].body as Record<string, unknown>;
    expect(createBody.aspect_ratio).toMatch(ASPECT_RATIO_PATTERN);
    expect(createBody).toEqual({
      name: "Episode 03 - Vertical teaser",
      description: "",
      aspect_ratio: "9:16",
      fps: 24,
      language: "vi",
      style_preset: "2d_anime_cinematic",
      output_profile: "1080p_h264",
    });

    await page.goto(`/projects/${PROJECT_ID}/settings`);
    await expect(
      page.getByRole("heading", { name: /^(?:Project settings|Cài đặt dự án)$/i }),
    ).toBeVisible();
    const profile = page.getByRole("region", {
      name: /^(?:Active project profile|Hồ sơ dự án đang hoạt động)$/i,
    });
    await expect(profile).toContainText("Test project");
    await expect(profile).toContainText("16:9 · 24 fps");
    await expect(profile).toContainText("2d_anime_cinematic");
    const history = page.getByRole("region", { name: /^(?:Revision history|Lịch sử revision)/i });
    await expect(history).toContainText(/(?:Project Revision History|Lịch sử revision dự án)/i);
    await expect(history).toContainText(
      /(?:Imported script revision v2|Đã nhập revision kịch bản v2)/i,
    );

    await page.getByRole("button", { name: /^(?:Duplicate settings|Nhân bản cài đặt)$/i }).click();
    const duplicated = page.getByRole("status");
    await expect(duplicated).toContainText(
      /(?:Duplicated project settings as Test project|Đã nhân bản cài đặt dự án thành Test project)/i,
    );
    await expect(duplicated).toContainText(
      /(?:Only project settings are copied|Chỉ sao chép cấu hình cài đặt)/i,
    );
    expect(
      requests.filter((request) => request.pathname.endsWith("/duplicate")).map((r) => r.body),
    ).toEqual([undefined]);
  });
});

test.describe("Connections module", () => {
  test("lists connections and pool accounts; removing an account requires confirmation", async ({
    page,
  }) => {
    const requests = await mockStudioApi(page, ({ method, pathname }) =>
      method === "DELETE" && pathname === "/api/v1/connections/conn-live/accounts/acc-primary"
        ? { status: 204, body: null }
        : undefined,
    );

    await page.goto("/connections");

    await expect(page.getByRole("heading", { name: /^(?:Connections|Kết nối)$/i })).toBeVisible();
    await expect(page.getByText("Live gateway", { exact: true })).toBeVisible();
    await expect(page.getByText("Mock provider", { exact: true })).toBeVisible();
    await expect(page.getByText("Upstream returned HTTP 503")).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: /^(?:Live gateway — Account Pool|Live gateway — Nhóm tài khoản)$/i,
      }),
    ).toBeVisible();

    const primary = page.getByRole("listitem").filter({ hasText: "Primary account" });
    const backup = page.getByRole("listitem").filter({ hasText: "Backup account" });
    await expect(primary).toContainText(/priority 10|ưu tiên 10/i);
    await expect(primary).toContainText("proxy: proxy.example:8080");
    await expect(backup).toContainText("auth_error");
    await expect(backup).toContainText("HTTP 401: invalid API key");

    await primary.getByRole("button", { name: /^(?:Remove|Xóa)$/i }).click();
    const dialog = primary.getByRole("alertdialog");
    await expect(dialog).toContainText(
      /(?:Remove pool account "Primary account"\?|Xóa tài khoản "Primary account" khỏi nhóm\?)/i,
    );
    await expect(dialog.getByRole("button", { name: /^(?:Cancel|Hủy)$/i })).toBeFocused();
    await dialog.getByRole("button", { name: /^(?:Cancel|Hủy)$/i }).click();
    await expect(primary.getByRole("alertdialog")).toHaveCount(0);
    expect(requests.filter((request) => request.method === "DELETE")).toEqual([]);

    await primary.getByRole("button", { name: /^(?:Remove|Xóa)$/i }).click();
    await primary
      .getByRole("alertdialog")
      .getByRole("button", { name: /^(?:Remove account|Xóa tài khoản)$/i })
      .click();
    await expect
      .poll(() => requests.filter((request) => request.method === "DELETE").map((r) => r.pathname))
      .toEqual(["/api/v1/connections/conn-live/accounts/acc-primary"]);
  });
});

test.describe("Workflow Canvas module", () => {
  test("creates a workflow rendered as React Flow nodes and approving a gate requires confirmation", async ({
    page,
  }) => {
    const base = `/api/v1/projects/${PROJECT_ID}/workflows`;
    const requests = await mockStudioApi(page, ({ method, pathname }) => {
      if (method === "POST" && pathname === base) return { status: 201, body: createdWorkflow };
      if (method === "POST" && pathname === `${base}/wf_e2e01/runs`) {
        return { status: 201, body: waitingApprovalRun };
      }
      if (
        method === "POST" &&
        pathname === `${base}/wf_e2e01/runs/run_e2e01/nodes/n_approval/approve`
      ) {
        return { status: 200, body: approvalResult };
      }
      return undefined;
    });

    await page.goto(`/projects/${PROJECT_ID}/workflow-canvas`);
    await expect(
      page.getByRole("heading", { name: /^(?:Workflow Automation|Tự động hóa workflow)$/i }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Create pipeline workflow" }).click();
    const canvas = page.getByRole("region", { name: "Workflow DAG canvas" });
    await expect(canvas.locator(".react-flow__node")).toHaveCount(7);
    await expect(canvas.getByTestId("workflow-node-n_approval")).toContainText("ManualApproval");
    await expect(page.getByText("7 nodes · 6 edges")).toBeVisible();
    expect(
      requests.filter((r) => r.method === "POST" && r.pathname === base).map((r) => r.body),
    ).toEqual([
      {
        name: "Script to Batch Film Pipeline",
        description:
          "Typed DAG pipeline with Merkle cache keys and human-in-the-loop approval gates",
        use_default_m3_template: true,
      },
    ]);

    await page.getByLabel("Run mode").selectOption("full");
    const execute = page.getByRole("button", { name: "Execute (full)" });
    await expect(execute).toBeDisabled();
    await page
      .getByRole("checkbox", { name: /Confirm full DAG execution across all 7 nodes/ })
      .check();
    await execute.click();
    await expect(page.getByRole("region", { name: "Execution run summary" })).toContainText(
      "waiting approval",
    );
    expect(requests.filter((r) => r.pathname.endsWith("/runs")).map((r) => r.body)).toEqual([
      { run_mode: "full", force_rerun: false, start_node_id: null },
    ]);

    const approvals = () => requests.filter((r) => r.pathname.endsWith("/approve"));
    await page.getByRole("button", { name: "Review gate n_approval" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("Approve manual gate n_approval?");
    await expect(dialog).toContainText("Approval cannot be undone.");
    await dialog.getByRole("button", { name: /^(?:Cancel|Hủy)$/i }).click();
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    expect(approvals()).toEqual([]);

    await page.getByRole("button", { name: "Review gate n_approval" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Approve & resume" }).click();
    await expect(canvas.getByTestId("workflow-node-n_approval")).toHaveAttribute(
      "data-state",
      "succeeded",
    );
    expect(approvals().map((r) => r.pathname)).toEqual([
      `${base}/wf_e2e01/runs/run_e2e01/nodes/n_approval/approve`,
    ]);
  });
});

test.describe("Timeline Editor module", () => {
  test("renders clips, restricts resolutions to the aspect ratio and exports a matching pair", async ({
    page,
  }) => {
    const base = `/api/v1/projects/${PROJECT_ID}/timeline/exports`;
    let launchedRequest: { resolution: string; aspect_ratio: string; fps: number } | null = null;
    const requests = await mockStudioApi(page, ({ method, pathname, body }) => {
      if (method === "POST" && pathname === base) {
        launchedRequest = body as { resolution: string; aspect_ratio: string; fps: number };
        return { status: 202, body: exportLaunched(launchedRequest) };
      }
      if (method === "GET" && pathname === `${base}/exp_e2e01` && launchedRequest) {
        return { status: 200, body: exportStatus(launchedRequest) };
      }
      return undefined;
    });

    await page.goto(`/projects/${PROJECT_ID}/timeline-editor`);

    const ruler = page.getByRole("region", { name: "Timeline tracks" });
    await expect(ruler.getByText("Shot: shot-1")).toBeVisible();
    await expect(ruler.getByText("Shot: shot-2")).toBeVisible();
    await expect(ruler.getByText("Shot: shot-3")).toBeVisible();
    await expect(ruler).toContainText("Shot 4 has no selected take and was skipped.");

    const exportPanel = page.getByRole("region", { name: "MP4 export" });
    const aspect = exportPanel.getByLabel("Aspect ratio");
    const resolution = exportPanel.getByLabel("Resolution");
    await expect(resolution.locator("option")).toHaveText(["1920x1080", "1280x720"]);

    await aspect.selectOption("9:16");
    await expect(resolution.locator("option")).toHaveText(["1080x1920", "720x1280"]);
    await expect(resolution).toHaveValue("1080x1920");
    await resolution.selectOption("720x1280");

    const skipped = exportPanel.getByTestId("export-skipped-features");
    await expect(skipped).toBeVisible();
    await expect(skipped).toContainText("It will not include:");
    await expect(skipped.getByRole("listitem")).toHaveText([
      "clip audio (output carries a silent AAC track)",
      "subtitles",
      "audio tracks (background music)",
    ]);

    await exportPanel.getByRole("button", { name: "Export MP4", exact: true }).click();
    const status = page.getByRole("region", { name: "Export job status" });
    await expect(status).toContainText("succeeded");
    await expect(status).toContainText("subtitles, audio_tracks");

    const launches = requests.filter((r) => r.method === "POST" && r.pathname === base);
    expect(launches.map((r) => r.body)).toEqual([
      { sequence_id: "seq_e2e01", resolution: "720x1280", aspect_ratio: "9:16", fps: 24 },
    ]);
    const [width, height] = (launches[0].body as { resolution: string }).resolution
      .split("x")
      .map(Number);
    expect(height).toBeGreaterThan(width);
  });
});

test.describe("Timeline Editor clip editing", () => {
  test("trims, reorders with the keyboard and saves custom clips with carried-over settings", async ({
    page,
  }) => {
    const assemblePath = `/api/v1/projects/${PROJECT_ID}/timeline/assemble`;
    let saved: ReturnType<typeof assembledFromCustomClips> | null = null;
    const requests = await mockStudioApi(page, ({ method, pathname, body }) => {
      if (method === "POST" && pathname === assemblePath) {
        saved = assembledFromCustomClips(
          "seq_e2e02",
          body as Parameters<typeof assembledFromCustomClips>[1],
        );
        return { status: 201, body: saved };
      }
      if (method === "GET" && pathname === `/api/v1/projects/${PROJECT_ID}/timeline` && saved) {
        // TimelineSequenceOut: the assemble result without its count fields.
        const { sequence_id, name, aspect_ratio, fps, resolution, total_duration_seconds } = saved;
        const sequence = {
          sequence_id,
          name,
          aspect_ratio,
          fps,
          resolution,
          total_duration_seconds,
          warnings: saved.warnings,
          manifest: saved.manifest,
        };
        return { status: 200, body: sequence };
      }
      return undefined;
    });

    await page.goto(`/projects/${PROJECT_ID}/timeline-editor`);
    const tracks = page.getByRole("region", { name: "Timeline tracks" });
    const inspector = page.getByRole("region", { name: "Selected clip" });
    const edits = page.getByRole("region", { name: "Clip edits" });
    await expect(tracks.getByText("Shot: shot-1")).toBeVisible();
    await expect(edits.getByTestId("clip-edit-status")).toHaveText("Matches saved sequence");

    // Trim the second clip (5 s source) to 3.5 s.
    await tracks.getByRole("button", { name: /Shot: shot-2/ }).click();
    await inspector.getByLabel("Trim start (s)").fill("0.5");
    await inspector.getByLabel("Trim end (s)").fill("1");
    await expect(inspector.getByTestId("clip-effective-duration")).toHaveText("3.5s of 5s source");
    await expect(edits.getByTestId("clip-edit-status")).toHaveText("Unsaved changes");
    await expect(
      page
        .getByRole("region", { name: "MP4 export" })
        .getByRole("button", { name: "Export MP4", exact: true }),
    ).toBeDisabled();

    // Keyboard reorder through dnd-kit: pick up clip 3, move it left twice, drop it.
    const handle = tracks.getByRole("button", { name: "Reorder clip 3 (shot shot-3)" });
    await handle.focus();
    // Each step waits for dnd-kit's live-region announcement before the next key.
    const announcement = page.locator("[id^='DndLiveRegion']");
    await page.keyboard.press("Space");
    await expect(announcement).toHaveText(
      /Picked up clip 3 \(shot shot-3\)\.|clip 3 \(shot shot-3\) is over position 3\./,
    );
    await page.keyboard.press("ArrowLeft");
    await expect(announcement).toContainText("is over position 2.");
    await page.keyboard.press("ArrowLeft");
    await expect(announcement).toContainText("is over position 1.");
    await page.keyboard.press("Space");
    await expect(announcement).toContainText("dropped at position 1.");
    await expect(tracks.getByRole("button", { name: /Shot: shot-/ })).toHaveText([
      /shot-3/,
      /shot-1/,
      /shot-2/,
    ]);

    // Saved subtitles cannot be read back, so saving asks before dropping them.
    await edits.getByRole("button", { name: "Save clip edits" }).click();
    const confirm = edits.getByRole("alertdialog");
    await expect(confirm).toContainText("saved subtitles will be removed");
    await page.screenshot({
      path: "test-results/ui-evidence/timeline-editor-clip-edits-dirty.png",
      fullPage: true,
    });
    await confirm.getByRole("button", { name: "Save without subtitles" }).click();

    await expect(edits.getByTestId("clip-edit-status")).toHaveText("Matches saved sequence");
    await expect(tracks).toContainText("Total 00:00:11");

    const saves = requests.filter((r) => r.method === "POST" && r.pathname === assemblePath);
    expect(saves).toHaveLength(1);
    const body = saves[0].body as {
      name: string;
      aspect_ratio: string;
      fps: number;
      subtitle_content: string;
      audio_tracks: unknown[];
      custom_clips: Array<Record<string, unknown>>;
    };
    expect(body).toMatchObject({
      name: "Master Cut v1",
      aspect_ratio: "16:9",
      fps: 24,
      subtitle_content: "",
      audio_tracks: [
        {
          track_type: "music",
          name: "Main Score Track",
          volume: 0.8,
          fade_in_seconds: 0.5,
          fade_out_seconds: 1,
          muted: false,
          asset_id: null,
        },
      ],
    });
    expect(
      body.custom_clips.map((c) => ({
        shot_id: c.shot_id,
        take_id: c.take_id,
        asset_id: c.asset_id,
        order_index: c.order_index,
        start_time_seconds: c.start_time_seconds,
        trim_start_seconds: c.trim_start_seconds,
        trim_end_seconds: c.trim_end_seconds,
        transition_type: c.transition_type,
      })),
    ).toEqual([
      {
        shot_id: "shot-3",
        take_id: "take-3",
        asset_id: "ast-video-3",
        order_index: 1,
        start_time_seconds: null,
        trim_start_seconds: 0,
        trim_end_seconds: 0,
        transition_type: "hard_cut",
      },
      {
        shot_id: "shot-1",
        take_id: "take-1",
        asset_id: "ast-video-1",
        order_index: 2,
        start_time_seconds: null,
        trim_start_seconds: 0,
        trim_end_seconds: 0,
        transition_type: "hard_cut",
      },
      {
        shot_id: "shot-2",
        take_id: "take-2",
        asset_id: "ast-video-2",
        order_index: 3,
        start_time_seconds: null,
        trim_start_seconds: 0.5,
        trim_end_seconds: 1,
        transition_type: "hard_cut",
      },
    ]);
  });
});

test.describe("Project backups and System maintenance", () => {
  test("lists backups, confirms backup deletion and gates real orphan cleanup behind a dry-run", async ({
    page,
  }) => {
    const requests = await mockStudioApi(page, ({ method, pathname, body }) => {
      if (method === "DELETE" && pathname === "/api/v1/backups/bak-1") {
        return { status: 204, body: null };
      }
      if (method === "POST" && pathname === "/api/v1/maintenance/orphans/cleanup") {
        return {
          status: 200,
          body: orphanCleanup(body as { dry_run: boolean; retention_hours: number }),
        };
      }
      return undefined;
    });

    await page.goto(`/projects/${PROJECT_ID}/settings`);

    const backupsRegion = page.getByRole("region", { name: "Project backups" });
    await expect(backupsRegion).toContainText("Project Backups (2)");
    for (const backup of backups) {
      await expect(backupsRegion.getByText(backup.filename)).toBeVisible();
    }
    const deletes = () => requests.filter((r) => r.method === "DELETE");
    await backupsRegion
      .getByRole("button", { name: `Delete backup ${backups[0].filename}` })
      .click();
    const dialog = backupsRegion.getByRole("alertdialog");
    await expect(dialog).toContainText(`Delete backup ${backups[0].filename}?`);
    await dialog.getByRole("button", { name: /^(?:Cancel|Hủy)$/i }).click();
    await expect(backupsRegion.getByRole("alertdialog")).toHaveCount(0);
    expect(deletes()).toEqual([]);

    await backupsRegion
      .getByRole("button", { name: `Delete backup ${backups[0].filename}` })
      .click();
    await backupsRegion
      .getByRole("alertdialog")
      .getByRole("button", { name: "Delete backup", exact: true })
      .click();
    await expect.poll(() => deletes().map((r) => r.pathname)).toEqual(["/api/v1/backups/bak-1"]);

    // Orphan cleanup touches every project, so it lives on the workspace System page.
    await expect(page.getByRole("region", { name: "Orphan media maintenance" })).toHaveCount(0);
    await page.goto("/system");
    await expect(page.getByRole("region", { name: "Observability summary" })).toContainText(
      "2 archives",
    );
    const orphans = page.getByRole("region", { name: "Orphan media maintenance" });
    const realCleanup = orphans.getByRole("button", { name: "Delete Orphan Files" });
    await expect(realCleanup).toBeDisabled();
    await expect(orphans).toContainText(
      "Run a dry-run with the current retention window before deleting orphan files.",
    );

    await orphans.getByRole("button", { name: "Dry-Run Cleanup" }).click();
    await expect(orphans.getByTestId("dry-run-report")).toContainText(
      "Dry-run report: 2 orphan files would be deleted",
    );
    await expect(realCleanup).toBeEnabled();

    await realCleanup.click();
    const cleanupDialog = orphans.getByRole("alertdialog");
    await expect(cleanupDialog).toContainText(
      "Delete orphan files older than 24h across all projects?",
    );
    await cleanupDialog.getByRole("button", { name: /^(?:Cancel|Hủy)$/i }).click();
    await expect(orphans.getByRole("alertdialog")).toHaveCount(0);

    // Changing the retention window invalidates the preview and disables real cleanup again.
    await orphans.getByLabel("Retention window (hours)").fill("48");
    await expect(realCleanup).toBeDisabled();

    const cleanups = requests.filter((r) => r.pathname === "/api/v1/maintenance/orphans/cleanup");
    expect(cleanups.map((r) => r.body)).toEqual([{ dry_run: true, retention_hours: 24 }]);
  });
});

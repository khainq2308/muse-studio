import type { Page } from "@playwright/test";

/**
 * Populated API fixtures for browser evidence specs. Every response shape is derived from the
 * committed contract in apps/orchestrator/openapi.json (schema names noted per fixture), so the
 * mocks cannot drift into fields the real Orchestrator never returns.
 */

export const PROJECT_ID = "test-project";

/** ProjectOut */
export const testProject = {
  id: PROJECT_ID,
  name: "Test project",
  description: "Noir detective short",
  aspect_ratio: "16:9",
  asset_count: 3,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
  current_revision_num: 2,
  fps: 24,
  language: "vi",
  output_profile: "1080p_h264",
  style_preset: "2d_anime_cinematic",
};

/** ProjectOut */
export const secondProject = {
  ...testProject,
  id: "second-project",
  name: "Episode 02 - Glass Citadel",
  description: "",
  aspect_ratio: "9:16",
  asset_count: 0,
  current_revision_num: 1,
  fps: 30,
};

/** RevisionOut[] */
export const revisions = [
  {
    id: "rev-2",
    project_id: PROJECT_ID,
    revision_number: 2,
    commit_message: "Imported script revision v2",
    snapshot_json: { aspect_ratio: "16:9", fps: 24, style_preset: "2d_anime_cinematic", shots: [] },
    created_at: "2026-01-02T00:00:00Z",
  },
  {
    id: "rev-1",
    project_id: PROJECT_ID,
    revision_number: 1,
    commit_message: "Project created",
    snapshot_json: { aspect_ratio: "16:9", fps: 24 },
    created_at: "2026-01-01T00:00:00Z",
  },
];

/** ConnectionOut[] */
export const connections = [
  {
    id: "conn-live",
    name: "Live gateway",
    provider_type: "muse2api",
    base_url: "https://gateway.example",
    api_key_masked: "sk-****",
    proxy_display_host: null,
    timeout_seconds: 30,
    max_concurrency: 2,
    enabled: true,
    health_state: "healthy",
    latency_ms: 40,
    capabilities_snapshot: {
      models: [{ model_id: "muse-video-v1", modality: "video", verification_status: "unverified" }],
      video_first_frame: "unknown",
    },
    last_error_redacted: null,
    last_checked_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "conn-mock",
    name: "Mock provider",
    provider_type: "mock",
    base_url: "http://mock.local",
    api_key_masked: "****",
    proxy_display_host: null,
    timeout_seconds: 10,
    max_concurrency: 4,
    enabled: true,
    health_state: "degraded",
    latency_ms: null,
    capabilities_snapshot: {},
    last_error_redacted: "Upstream returned HTTP 503",
    last_checked_at: null,
  },
];

/** AccountOut[] for conn-live */
export const accounts = [
  {
    id: "acc-primary",
    connection_id: "conn-live",
    label: "Primary account",
    api_key_masked: "sk-****abcd",
    proxy_display_host: "proxy.example:8080",
    has_proxy: true,
    enabled: true,
    priority: 10,
    health_state: "healthy",
    latency_ms: 52.4,
    last_error_redacted: null,
    last_used_at: "2026-01-01T00:00:00Z",
    last_checked_at: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "acc-backup",
    connection_id: "conn-live",
    label: "Backup account",
    api_key_masked: "sk-****wxyz",
    proxy_display_host: null,
    has_proxy: false,
    enabled: false,
    priority: 100,
    health_state: "auth_error",
    latency_ms: null,
    last_error_redacted: "HTTP 401: invalid API key",
    last_used_at: null,
    last_checked_at: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  },
];

const m3Nodes = [
  ["n_script_in", "ScriptInput"],
  ["n_analyze", "ScriptAnalyze"],
  ["n_bible", "StoryBibleBuild"],
  ["n_scenes", "SceneBreakdown"],
  ["n_shots", "ShotBreakdown"],
  ["n_prompt", "PromptTemplate"],
  ["n_approval", "ManualApproval"],
] as const;

/** WorkflowCreateOut, mirroring WorkflowEngine.build_m3_default_template(). */
export const createdWorkflow = {
  id: "wf_e2e01",
  project_id: PROJECT_ID,
  name: "Script to Batch Film Pipeline",
  current_version: 1,
  execution_order: m3Nodes.map(([id]) => id),
  cache_keys: Object.fromEntries(
    m3Nodes.map(([id], index) => [id, `${String(index).repeat(8)}abcdef0123456789`]),
  ),
  nodes: m3Nodes.map(([node_id, node_type]) => ({
    node_id,
    node_type,
    config: {},
    inputs: {},
    outputs: {},
  })),
  edges: m3Nodes.slice(1).map(([target], index) => ({
    edge_id: `e${index + 1}`,
    source_node_id: m3Nodes[index][0],
    source_port: "out",
    target_node_id: target,
    target_port: "in",
  })),
  ui_layout: Object.fromEntries(m3Nodes.map(([id], index) => [id, { x: index * 240, y: 120 }])),
};

/** WorkflowExecutionOut stopped at the ManualApproval gate. */
export const waitingApprovalRun = {
  run_id: "run_e2e01",
  status: "waiting_approval",
  execution_mode: "simulated",
  execution_plan_order: createdWorkflow.execution_order,
  node_executions: m3Nodes.map(([node_id, node_type]) => ({
    node_id,
    node_type,
    state: node_type === "ManualApproval" ? "waiting_approval" : "succeeded",
    cache_key: createdWorkflow.cache_keys[node_id],
    cache_hit: false,
    logs: [`${node_type} simulated`],
    output_refs: {},
  })),
};

/** WorkflowApprovalOut */
export const approvalResult = {
  run_id: "run_e2e01",
  status: "succeeded",
  execution_mode: "simulated",
  node_executions: [
    { node_id: "n_approval", state: "succeeded", cache_hit: false, output_refs: {} },
  ],
};

function clip(index: number, shotId: string, duration: number, start: number) {
  /** TimelineClipOut */
  return {
    order_index: index,
    shot_id: shotId,
    take_id: `take-${index + 1}`,
    asset_id: `ast-video-${index + 1}`,
    start_time_seconds: start,
    source_duration_seconds: duration,
    trim_start_seconds: 0,
    trim_end_seconds: 0,
    effective_duration_seconds: duration,
    transition_type: "hard_cut",
    transition_duration_seconds: 0,
    volume: 1,
    fade_in_seconds: 0,
    fade_out_seconds: 0,
  };
}

/** TimelineSequenceOut with clips, subtitles and a music track. */
export const timeline = {
  sequence_id: "seq_e2e01",
  name: "Master Cut v1",
  aspect_ratio: "16:9",
  fps: 24,
  resolution: "1920x1080",
  total_duration_seconds: 13,
  warnings: [
    {
      code: "missing_selected_take",
      severity: "warning",
      message: "Shot 4 has no selected take and was skipped.",
      clip_index: null,
      shot_id: "shot-4",
    },
  ],
  manifest: {
    sequence_id: "seq_e2e01",
    project_id: PROJECT_ID,
    aspect_ratio: "16:9",
    fps: 24,
    resolution: "1920x1080",
    total_duration_seconds: 13,
    title_card: {},
    subtitle_format: "srt",
    has_subtitles: true,
    video_clips: [clip(0, "shot-1", 4, 0), clip(1, "shot-2", 5, 4), clip(2, "shot-3", 4, 9)],
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
  },
};

/** ExportLaunchOut for a given ExportLaunchIn. */
export function exportLaunched(request: { resolution: string; aspect_ratio: string; fps: number }) {
  return {
    export_id: "exp_e2e01",
    sequence_id: "seq_e2e01",
    state: "queued",
    aspect_ratio: request.aspect_ratio,
    fps: request.fps,
    resolution: request.resolution,
    unsupported_features: ["subtitles", "audio_tracks"],
  };
}

/** ExportJobStatusOut (terminal, so polling stops). */
export function exportStatus(request: { resolution: string; aspect_ratio: string; fps: number }) {
  return {
    ...exportLaunched(request),
    state: "succeeded",
    video_codec: "h264",
    audio_codec: "aac",
    message: "FFprobe verified the exported MP4.",
    output_asset_id: "ast_export01",
    download_url: null,
    ffmpeg_args: null,
    ffprobe_verification: null,
  };
}

/** BackupOut[] */
export const backups = [
  {
    id: "bak-1",
    project_id: PROJECT_ID,
    filename: "test-project-20260102T000000Z.zip",
    size_bytes: 5_242_880,
    sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    manifest_json: {
      created_at: "2026-01-02T00:00:00Z",
      excluded_tables: [],
      format_version: "1",
      media_files: 3,
      missing_media: [],
      project_id: PROJECT_ID,
      project_name: "Test project",
      tables: { shots: 3 },
    },
    created_at: "2026-01-02T00:00:00Z",
  },
  {
    id: "bak-2",
    project_id: PROJECT_ID,
    filename: "test-project-20260101T000000Z.zip",
    size_bytes: 1_048_576,
    sha256: "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
    manifest_json: {
      created_at: "2026-01-01T00:00:00Z",
      excluded_tables: [],
      format_version: "1",
      media_files: 0,
      missing_media: [],
      project_id: PROJECT_ID,
      project_name: "Test project",
      tables: {},
    },
    created_at: "2026-01-01T00:00:00Z",
  },
];

/** MetricsSummaryOut */
export const metricsSummary = {
  service: {
    name: "muse-orchestrator",
    version: "0.9.0",
    environment: "test",
    uptime_seconds: 3600,
    started_at: 1767225600,
  },
  projects: { total: 2 },
  connections: { total: 2, by_health: { healthy: 1, degraded: 1 }, accounts_total: 2 },
  queue: { depth: 1, generation_tasks_by_state: { succeeded: 4 }, generation_tasks_total: 5 },
  assets: { total: 3, total_bytes: 9_437_184 },
  exports: { total: 1, by_state: { succeeded: 1 } },
  backups: { total: 2, total_bytes: 6_291_456 },
  storage: { media_root: "/data/media", total_bytes: 12_582_912, file_count: 7 },
};

/** StructuredLogOut[] */
export const systemLogs = [
  {
    id: "log-1",
    correlation_id: "corr-1",
    level: "info",
    event: "backup.created",
    project_id: PROJECT_ID,
    task_id: null,
    payload_redacted: { backup_id: "bak-1" },
    created_at: "2026-01-02T00:00:00Z",
  },
  {
    id: "log-2",
    correlation_id: "corr-2",
    level: "error",
    event: "provider.request_failed",
    project_id: PROJECT_ID,
    task_id: "task-1",
    payload_redacted: { status: 503 },
    created_at: "2026-01-02T00:05:00Z",
  },
];

/** OrphanCleanupOut */
export function orphanCleanup(request: { dry_run: boolean; retention_hours: number }) {
  const orphans = [
    { rel_path: "projects/old/ast_orphan1.mp4", size_bytes: 2_097_152, age_hours: 72.5 },
    { rel_path: "projects/old/ast_orphan2.png", size_bytes: 524_288, age_hours: 48 },
  ];
  return {
    dry_run: request.dry_run,
    retention_hours: request.retention_hours,
    scanned_orphans: orphans.length,
    deleted: request.dry_run ? 0 : orphans.length,
    freed_bytes: request.dry_run ? 0 : 2_621_440,
    errors: [],
    orphans: request.dry_run ? orphans : [],
  };
}

function makeShot(overrides: Record<string, unknown>) {
  /** ShotOut (copied from e2e/shot-board.spec.ts) */
  return {
    id: "shot-1",
    scene_id: "scene-1",
    sequence_index: 1,
    title: "Hero enters",
    subject_codes: ["HERO"],
    location_code: "LOC_HALL",
    shot_size: "wide",
    camera_motion: "static",
    composition: "Hero framed in the doorway",
    action: "The hero walks into the hall.",
    dialogue: "",
    duration_seconds: 4,
    requires_reference_lock: false,
    reference_asset_ids: [],
    prompt_final: "wide static shot of the hero walking into the hall",
    negative_prompt: "",
    prompt_hash: "hash-1",
    lint_report: [],
    continuity_mode: "standard",
    previous_shot_id: null,
    next_shot_id: "shot-2",
    first_frame_asset_id: null,
    last_frame_asset_id: null,
    selected_take_id: null,
    approval_state: "draft",
    generation_state: "not_started",
    ...overrides,
  };
}

export const shots = [
  makeShot({}),
  makeShot({
    id: "shot-2",
    sequence_index: 2,
    title: "Chase",
    action: "The hero runs after the thief.",
    continuity_mode: "frame_chain",
    previous_shot_id: "shot-1",
    next_shot_id: null,
    approval_state: "needs_review",
  }),
  makeShot({
    id: "shot-3",
    scene_id: "scene-2",
    sequence_index: 3,
    title: "Rooftop",
    next_shot_id: null,
    lint_report: [
      {
        code: "MISSING_REFERENCE",
        severity: "blocker",
        message: "Reference image required for HERO.",
        field: "reference_asset_ids",
      },
    ],
  }),
];

/** StoryBibleEntryOut[] (copied from e2e/script-studio.spec.ts) */
export const storyBible = [
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

/** QueueTaskListOut (empty) */
export const emptyQueue = {
  items: [],
  total: 0,
  limit: 50,
  offset: 0,
  has_more: false,
  summary: { total_tasks: 0, active_tasks: 0, failed_tasks: 0, awaiting_input_tasks: 0 },
};

export interface Recorded {
  method: string;
  pathname: string;
  search: string;
  body: unknown;
}

export type MockResponse = { status: number; body: unknown } | undefined;

const P = `/api/v1/projects/${PROJECT_ID}`;

/** Populated GET responses for every module page. */
function defaultResponse({ method, pathname }: Recorded): MockResponse {
  if (method !== "GET") return undefined;
  const table: Record<string, unknown> = {
    "/api/v1/projects": [testProject, secondProject],
    [`${P}/revisions`]: revisions,
    "/api/v1/connections": connections,
    "/api/v1/connections/conn-live/accounts": accounts,
    "/api/v1/connections/conn-mock/accounts": [],
    [`${P}/timeline`]: timeline,
    [`${P}/backups`]: backups,
    "/api/v1/metrics/summary": metricsSummary,
    "/api/v1/system/logs": systemLogs,
    [`${P}/shots`]: shots,
    [`${P}/story-bible`]: storyBible,
    "/api/v1/queue/tasks": emptyQueue,
  };
  return pathname in table ? { status: 200, body: table[pathname] } : undefined;
}

/**
 * Routes `/healthz` and `/api/**`. `overrides` wins over the populated defaults; anything
 * unmatched returns a 404 in the backend's error shape so a missing mock is visible.
 */
export async function mockStudioApi(
  page: Page,
  overrides: (request: Recorded) => MockResponse = () => undefined,
): Promise<Recorded[]> {
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

    const response = overrides(recorded) ?? defaultResponse(recorded);
    if (response && response.status === 204) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.fulfill({
      status: response?.status ?? 404,
      contentType: "application/json",
      body: JSON.stringify(response?.body ?? { detail: "Not mocked", category: "not_found" }),
    });
  });
  return requests;
}

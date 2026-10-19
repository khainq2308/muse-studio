export type ApprovalState = "draft" | "needs_review" | "approved" | "rejected";
export type GenerationState =
  | "not_started"
  | "blocked_reference"
  | "blocked_upstream_frame"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "canceled";

export interface Project {
  id: string;
  name: string;
  description: string;
  aspect_ratio: string;
  fps: number;
  style_preset: string;
  language: string;
  output_profile: string;
  current_revision_num: number;
  asset_count: number;
  created_at: string;
  updated_at: string;
}

export interface Connection {
  id: string;
  name: string;
  provider_type: "muse2api" | "mock";
  base_url: string;
  api_key_masked: string;
  proxy_display_host: string | null;
  timeout_seconds: number;
  max_concurrency: number;
  enabled: boolean;
  health_state: string;
  latency_ms: number | null;
  capabilities_snapshot: Record<string, unknown>;
  last_error_redacted: string | null;
  last_checked_at: string | null;
}

export interface ConnectionAccount {
  id: string;
  connection_id: string;
  label: string;
  api_key_masked: string;
  proxy_display_host: string | null;
  has_proxy: boolean;
  enabled: boolean;
  priority: number;
  health_state: string;
  latency_ms: number | null;
  last_error_redacted: string | null;
  last_used_at: string | null;
  last_checked_at: string | null;
  created_at: string;
}

export interface BibleEntry {
  id: string;
  entry_type: "character" | "location" | "style" | "prop";
  code_name: string;
  display_name: string;
  immutable_traits: string;
  mutable_attributes: Record<string, unknown>;
  reference_asset_ids: string[];
  requires_reference_lock: boolean;
}

export interface LintIssue {
  code: string;
  severity: string;
  message: string;
  field?: string | null;
}

export interface Shot {
  id: string;
  scene_id: string | null;
  sequence_index: number;
  title: string;
  subject_codes: string[];
  location_code: string;
  shot_size: string;
  camera_motion: string;
  composition: string;
  action: string;
  dialogue?: string;
  duration_seconds: number;
  requires_reference_lock: boolean;
  reference_asset_ids: string[];
  prompt_final: string;
  negative_prompt: string;
  prompt_hash: string;
  lint_report: LintIssue[];
  continuity_mode: string;
  previous_shot_id: string | null;
  next_shot_id: string | null;
  first_frame_asset_id: string | null;
  last_frame_asset_id: string | null;
  selected_take_id: string | null;
  approval_state: ApprovalState;
  generation_state: GenerationState;
}

export interface QueueTask {
  id: string;
  project_id: string;
  scene_id: string | null;
  shot_id: string;
  connection_id: string;
  task_type: "image" | "video" | string;
  model_id: string;
  idempotency_key: string;
  priority: number;
  state: string;
  external_task_id: string | null;
  account_used: string | null;
  attempt_count: number;
  max_attempts: number;
  poll_count: number;
  max_polls: number;
  next_poll_delay_seconds: number;
  error_category: string | null;
  retryable: boolean;
  error_message_redacted: string | null;
  result_asset_id: string | null;
  take_id: string | null;
  history_log: string[];
}

export interface ShotTake {
  id: string;
  shot_id: string;
  take_number: number;
  task_id: string;
  asset_id: string;
  model_id: string;
  prompt_hash: string;
  account_used: string | null;
  first_frame_asset_id: string | null;
  last_frame_asset_id: string | null;
  continuity_flagged: boolean;
  director_notes: string;
  is_selected: boolean;
}

export interface TimelineInfo {
  sequence_id: string;
  name: string;
  aspect_ratio: string;
  fps: number;
  resolution: string;
  total_duration_seconds: number;
  warnings: Array<unknown>;
  manifest: Record<string, unknown>;
}

export interface ExportResult {
  export_id: string;
  sequence_id: string;
  state: string;
  output_asset_id?: string;
  download_url?: string;
  unsupported_features?: string[];
  ffprobe_verification?: Record<string, unknown>;
}

export interface ScriptImportResult {
  script_revision_id: string;
  version_number: number;
  raw_content_preserved: boolean;
  diff_summary: string;
  detected_characters: number;
  detected_locations: number;
  detected_scenes: number;
  generated_shots: number;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

const DEFAULT_API_URL = "http://localhost:8000";

export function createStudioApi(adminToken: string) {
  const baseUrl = String((import.meta as ImportMeta & { env?: Record<string, string> }).env?.VITE_ORCHESTRATOR_URL || DEFAULT_API_URL)
    .replace(/\/+$/, "");

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (!(init.body instanceof FormData) && init.body !== undefined) {
      headers.set("Content-Type", "application/json");
    }
    if (adminToken.trim()) headers.set("X-Studio-Admin-Token", adminToken.trim());

    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, { ...init, headers });
    } catch {
      throw new ApiError("Không kết nối được Orchestrator. Kiểm tra API và URL cấu hình.", 0);
    }

    if (!response.ok) {
      let message = `Request thất bại (${response.status}).`;
      try {
        const payload = await response.json() as { detail?: unknown; message?: unknown };
        const detail = payload.detail ?? payload.message;
        if (typeof detail === "string") message = detail;
        else if (detail !== undefined) message = JSON.stringify(detail);
      } catch {
        // Some gateway errors do not carry a JSON response body.
      }
      throw new ApiError(message, response.status);
    }
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  }

  return {
    request,
    assetDownloadUrl: (assetId: string) => `${baseUrl}/api/v1/assets/${encodeURIComponent(assetId)}/download`,
    listProjects: () => request<Project[]>("/api/v1/projects"),
    createProject: (input: Pick<Project, "name"> & Partial<Pick<Project, "description" | "aspect_ratio" | "fps" | "style_preset" | "language" | "output_profile">>) =>
      request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify(input) }),

    listConnections: () => request<Connection[]>("/api/v1/connections"),
    createConnection: (input: {
      name: string;
      provider_type: "muse2api" | "mock";
      base_url: string;
      api_key?: string;
      proxy_display_host?: string;
      timeout_seconds: number;
      max_concurrency: number;
      enabled: boolean;
    }) => request<Connection>("/api/v1/connections", { method: "POST", body: JSON.stringify(input) }),
    testConnection: (id: string) => request<Connection>(`/api/v1/connections/${encodeURIComponent(id)}/test`),
    listAccounts: (connectionId: string) =>
      request<ConnectionAccount[]>(`/api/v1/connections/${encodeURIComponent(connectionId)}/accounts`),
    createAccount: (connectionId: string, input: {
      label: string;
      api_key: string;
      proxy_url?: string;
      enabled: boolean;
      priority: number;
    }) => request<ConnectionAccount>(`/api/v1/connections/${encodeURIComponent(connectionId)}/accounts`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
    testAccount: (connectionId: string, accountId: string) =>
      request<ConnectionAccount>(`/api/v1/connections/${encodeURIComponent(connectionId)}/accounts/${encodeURIComponent(accountId)}/test`, { method: "POST" }),
    deleteAccount: (connectionId: string, accountId: string) =>
      request<void>(`/api/v1/connections/${encodeURIComponent(connectionId)}/accounts/${encodeURIComponent(accountId)}`, { method: "DELETE" }),

    listBible: (projectId: string) => request<BibleEntry[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/story-bible`),
    upsertBibleEntry: (projectId: string, input: Omit<BibleEntry, "id">) =>
      request<{ id: string; code_name: string; status: string }>(`/api/v1/projects/${encodeURIComponent(projectId)}/story-bible`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    importScript: (projectId: string, input: { raw_content: string; source_format: string; auto_apply_proposal: boolean }) =>
      request<ScriptImportResult>(`/api/v1/projects/${encodeURIComponent(projectId)}/scripts/import`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    listShots: (projectId: string) => request<Shot[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/shots`),
    updateShot: (projectId: string, shotId: string, input: Partial<Pick<Shot,
      "shot_size" | "camera_motion" | "composition" | "action" | "duration_seconds" |
      "continuity_mode" | "previous_shot_id" | "first_frame_asset_id" |
      "requires_reference_lock" | "reference_asset_ids" | "approval_state"
    >>) => request<{ id: string; prompt_final: string; prompt_hash: string; lint_report: LintIssue[]; approval_state: ApprovalState; generation_state: GenerationState }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}`,
      { method: "PATCH", body: JSON.stringify(input) },
    ),

    listTasks: (projectId: string) =>
      request<QueueTask[]>(`/api/v1/queue/tasks?project_id=${encodeURIComponent(projectId)}`),
    enqueueTask: (input: { shot_id: string; connection_id: string; task_type: "image" | "video"; model_id: string; priority: number; max_polls: number }) =>
      request<QueueTask & { created_new?: boolean }>("/api/v1/queue/tasks", { method: "POST", body: JSON.stringify(input) }),
    taskAction: (taskId: string, action: "pause" | "resume" | "cancel") =>
      request<QueueTask>(`/api/v1/queue/tasks/${encodeURIComponent(taskId)}/${action}`, { method: "POST" }),
    listTakes: (shotId: string) => request<ShotTake[]>(`/api/v1/queue/shots/${encodeURIComponent(shotId)}/takes`),
    selectTake: (shotId: string, takeId: string) =>
      request<{ shot_id: string; selected_take_id: string; last_frame_asset_id: string | null; propagated_downstream_shots: string[] }>(
        `/api/v1/queue/shots/${encodeURIComponent(shotId)}/takes/${encodeURIComponent(takeId)}/select`,
        { method: "POST" },
      ),

    getTimeline: (projectId: string) =>
      request<TimelineInfo>(`/api/v1/projects/${encodeURIComponent(projectId)}/timeline`),
    assembleTimeline: (projectId: string, input: {
      name: string;
      allow_missing_shots: boolean;
      default_transition: "hard_cut" | "cross_dissolve";
      transition_duration_seconds: number;
    }) => request<TimelineInfo>(`/api/v1/projects/${encodeURIComponent(projectId)}/timeline/assemble`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
    exportTimeline: (projectId: string, sequenceId: string) =>
      request<ExportResult>(`/api/v1/projects/${encodeURIComponent(projectId)}/timeline/exports`, {
        method: "POST",
        body: JSON.stringify({ sequence_id: sequenceId }),
      }),
  };
}

export type StudioApi = ReturnType<typeof createStudioApi>;

import type {
  ApprovalState,
  ConnectionAccountDto,
  ConnectionDto,
  ExportResultDto,
  GenerationState,
  LintIssueDto,
  ProjectDto,
  QueueTaskDto,
  ScriptImportResultDto,
  ShotDto,
  ShotTakeDto,
  StoryBibleEntryDto,
  TimelineInfoDto,
} from "../../../../packages/contracts/src";

export type { ApprovalState, GenerationState };
export type Project = ProjectDto;
export type Connection = ConnectionDto;
export type ConnectionAccount = ConnectionAccountDto;
export type BibleEntry = StoryBibleEntryDto;
export type LintIssue = LintIssueDto;
export type Shot = ShotDto;
export type QueueTask = QueueTaskDto;
export type ShotTake = ShotTakeDto;
export type TimelineInfo = TimelineInfoDto;
export type ExportResult = ExportResultDto;
export type ScriptImportResult = ScriptImportResultDto;

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

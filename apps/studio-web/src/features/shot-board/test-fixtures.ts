import type {
  BatchLaunchResult,
  Connection,
  EnqueuedTask,
  ProjectShot,
  ShotReview,
} from "@/features/shot-board/state";
import type {
  CompareShotTakes,
  ContinuityFlagResult,
  ShotTake,
  ShotTakeSelection,
  TakeComparison,
} from "@/features/shot-board/takes";

import type { QueueTask, QueueTaskPage } from "@/entities/task/state";

/** Test-only builders typed from the generated OpenAPI schema. */
export function makeShot(overrides: Partial<ProjectShot> = {}): ProjectShot {
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
    next_shot_id: null,
    first_frame_asset_id: null,
    last_frame_asset_id: null,
    selected_take_id: null,
    approval_state: "draft",
    generation_state: "not_started",
    ...overrides,
  };
}

export function makeConnection(overrides: Partial<Connection> = {}): Connection {
  return {
    id: "conn-mock",
    name: "Mock studio",
    provider_type: "mock",
    base_url: "http://mock.local",
    api_key_masked: "****",
    proxy_display_host: null,
    timeout_seconds: 30,
    max_concurrency: 2,
    enabled: true,
    health_state: "healthy",
    latency_ms: 12,
    capabilities_snapshot: {
      models: [
        { model_id: "muse-video-mock-v1", modality: "video", verification_status: "mock-only" },
        { model_id: "muse-image-mock-v1", modality: "image", verification_status: "mock-only" },
      ],
    },
    last_error_redacted: null,
    last_checked_at: null,
    ...overrides,
  };
}

export function makeReview(overrides: Partial<ShotReview> = {}): ShotReview {
  return {
    id: "shot-1",
    prompt_final: "close-up handheld shot of the hero running",
    prompt_hash: "hash-2",
    lint_report: [],
    approval_state: "draft",
    generation_state: "not_started",
    ...overrides,
  };
}

export function makeEnqueuedTask(overrides: Partial<EnqueuedTask> = {}): EnqueuedTask {
  return {
    id: "task-1",
    project_id: "project-1",
    scene_id: "scene-1",
    shot_id: "shot-1",
    connection_id: "conn-mock",
    task_type: "video",
    model_id: "muse-video-mock-v1",
    idempotency_key: "server-key",
    priority: 10,
    state: "queued",
    external_task_id: null,
    account_used: null,
    attempt_count: 0,
    max_attempts: 3,
    poll_count: 0,
    max_polls: 60,
    next_poll_delay_seconds: 2,
    error_category: null,
    retryable: true,
    error_message_redacted: null,
    result_asset_id: null,
    take_id: null,
    history_log: [],
    created_new: true,
    ...overrides,
  };
}

export function makeBatchResult(overrides: Partial<BatchLaunchResult> = {}): BatchLaunchResult {
  return {
    batch_id: "bat_0001",
    total_shots: 3,
    enqueued_count: 1,
    skipped_unchanged_count: 1,
    blocked_count: 1,
    enqueued_task_ids: ["task-1"],
    skipped_shot_ids: ["shot-3"],
    blocked_shot_ids: ["shot-2"],
    warnings: [],
    ...overrides,
  };
}

export function makeTake(overrides: Partial<ShotTake> = {}): ShotTake {
  return {
    id: "take-1",
    shot_id: "shot-1",
    take_number: 1,
    task_id: "task-1",
    asset_id: "asset-video-1",
    model_id: "muse-video-mock-v1",
    prompt_hash: "hash-1",
    account_used: null,
    first_frame_asset_id: null,
    last_frame_asset_id: null,
    continuity_flagged: false,
    director_notes: "",
    is_selected: false,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function toComparison(take: ShotTake): TakeComparison {
  return {
    take_id: take.id,
    take_number: take.take_number,
    asset_id: take.asset_id,
    first_frame_asset_id: take.first_frame_asset_id,
    last_frame_asset_id: take.last_frame_asset_id,
    model_id: take.model_id,
    prompt_hash: take.prompt_hash,
    account_used: take.account_used,
    continuity_flagged: take.continuity_flagged,
    director_notes: take.director_notes,
    is_selected: take.is_selected,
  };
}

export function makeComparison(a: ShotTake, b: ShotTake): CompareShotTakes {
  return {
    shot_id: a.shot_id,
    same_prompt_hash: a.prompt_hash === b.prompt_hash,
    same_model: a.model_id === b.model_id,
    take_a: toComparison(a),
    take_b: toComparison(b),
  };
}

export function makeSelection(overrides: Partial<ShotTakeSelection> = {}): ShotTakeSelection {
  return {
    shot_id: "shot-1",
    selected_take_id: "take-1",
    last_frame_asset_id: null,
    propagated_downstream_shots: [],
    ...overrides,
  };
}

export function makeFlagResult(
  overrides: Partial<ContinuityFlagResult> = {},
): ContinuityFlagResult {
  return {
    shot_id: "shot-1",
    take_id: "take-1",
    continuity_flagged: true,
    director_notes: "Hair changes side.",
    regenerated_task_id: null,
    ...overrides,
  };
}

export function makeQueueTask(overrides: Partial<QueueTask> = {}): QueueTask {
  // A listed task is an enqueued task without the enqueue-only `created_new` flag.
  const task: Partial<EnqueuedTask> = makeEnqueuedTask();
  delete task.created_new;
  return { ...(task as Omit<EnqueuedTask, "created_new">), pending_input: null, ...overrides };
}

/** `GET /api/v1/queue/tasks` page; the summary defaults to counts derived from `items`. */
export function makeTaskPage(
  items: QueueTask[] = [],
  summary: Partial<QueueTaskPage["summary"]> = {},
): QueueTaskPage {
  const active = items.filter((task) =>
    ["queued", "running", "awaiting_input"].includes(task.state),
  ).length;
  return {
    items,
    total: items.length,
    limit: 100,
    offset: 0,
    has_more: false,
    summary: {
      total_tasks: items.length,
      active_tasks: active,
      failed_tasks: items.filter((task) => task.state === "failed").length,
      awaiting_input_tasks: items.filter((task) => task.state === "awaiting_input").length,
      ...summary,
    },
  };
}

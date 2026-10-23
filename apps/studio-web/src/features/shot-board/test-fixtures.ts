import type {
  BatchLaunchResult,
  Connection,
  EnqueuedTask,
  ProjectShot,
  ShotReview,
} from "@/features/shot-board/state";

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

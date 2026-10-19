export type ShotApprovalState = 'draft' | 'needs_review' | 'approved' | 'rejected';

export type ShotGenerationState =
  | 'not_started'
  | 'blocked_reference'
  | 'blocked_upstream_frame'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled';

export type NodeExecutionState =
  | 'idle'
  | 'ready'
  | 'blocked'
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'succeeded'
  | 'failed'
  | 'canceled'
  | 'skipped';

export type ConnectionHealthState = 'unknown' | 'healthy' | 'degraded' | 'auth_error' | 'unavailable';

export type ProviderType = 'muse2api' | 'mock';
export type TaskType = 'image' | 'video';
export type ShotEntryType = 'character' | 'location' | 'style' | 'prop';
export type ApprovalState = ShotApprovalState;
export type GenerationState = ShotGenerationState;

export interface ProjectDto {
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

export interface ConnectionDto {
  id: string;
  name: string;
  provider_type: ProviderType;
  base_url: string;
  api_key_masked: string;
  proxy_display_host: string | null;
  timeout_seconds: number;
  max_concurrency: number;
  enabled: boolean;
  health_state: ConnectionHealthState;
  latency_ms: number | null;
  capabilities_snapshot: Record<string, unknown>;
  last_error_redacted: string | null;
  last_checked_at: string | null;
}

export interface ConnectionAccountDto {
  id: string;
  connection_id: string;
  label: string;
  api_key_masked: string;
  proxy_display_host: string | null;
  has_proxy: boolean;
  enabled: boolean;
  priority: number;
  health_state: ConnectionHealthState;
  latency_ms: number | null;
  last_error_redacted: string | null;
  last_used_at: string | null;
  last_checked_at: string | null;
  created_at: string;
}

export interface StoryBibleEntryDto {
  id: string;
  entry_type: ShotEntryType;
  code_name: string;
  display_name: string;
  immutable_traits: string;
  mutable_attributes: Record<string, unknown>;
  reference_asset_ids: string[];
  requires_reference_lock: boolean;
}

export interface LintIssueDto {
  code: string;
  severity: string;
  message: string;
  field?: string | null;
}

export interface ShotDto {
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
  lint_report: LintIssueDto[];
  continuity_mode: string;
  previous_shot_id: string | null;
  next_shot_id: string | null;
  first_frame_asset_id: string | null;
  last_frame_asset_id: string | null;
  selected_take_id: string | null;
  approval_state: ShotApprovalState;
  generation_state: ShotGenerationState;
}

export interface QueueTaskDto {
  id: string;
  project_id: string;
  scene_id: string | null;
  shot_id: string;
  connection_id: string;
  task_type: TaskType | string;
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

export interface ShotTakeDto {
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

export interface TimelineInfoDto {
  sequence_id: string;
  name: string;
  aspect_ratio: string;
  fps: number;
  resolution: string;
  total_duration_seconds: number;
  warnings: unknown[];
  manifest: Record<string, unknown>;
}

export interface ExportResultDto {
  export_id: string;
  sequence_id: string;
  state: string;
  output_asset_id?: string;
  download_url?: string;
  unsupported_features?: string[];
  ffprobe_verification?: Record<string, unknown>;
}

export interface ScriptImportResultDto {
  script_revision_id: string;
  version_number: number;
  raw_content_preserved: boolean;
  diff_summary: string;
  detected_characters: number;
  detected_locations: number;
  detected_scenes: number;
  generated_shots: number;
}


import { groupShotsByScene as groupShotsByEntityScene } from "@/entities/shot/order";
import { ApiError } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import i18n from "@/lib/i18n";

export type ProjectShot = components["schemas"]["ProjectShotOut"];
export type ShotUpdate = components["schemas"]["ShotUpdateIn"];
export type ShotReview = components["schemas"]["ShotReviewOut"];
export type ShotApprovalState = components["schemas"]["ShotApprovalState"];
export type ShotGenerationState = components["schemas"]["ShotGenerationState"];
export type ShotLintIssue = components["schemas"]["PipelineLintIssueOut"];
export type ShotContinuityMode = NonNullable<ShotUpdate["continuity_mode"]>;
export type EnqueueTaskRequest = components["schemas"]["EnqueueTaskIn"];
export type EnqueuedTask = components["schemas"]["EnqueuedQueueTaskOut"];
export type GenerationTaskType = EnqueueTaskRequest["task_type"];
export type BatchLaunchRequest = components["schemas"]["BatchLaunchIn"];
export type BatchLaunchResult = components["schemas"]["BatchLaunchOut"];
export type BatchWarning = components["schemas"]["BatchWarningOut"];
export type Connection = components["schemas"]["ConnectionOut"];

/** Editable `ShotUpdateIn` fields offered by the shot board edit panel. */
export type ShotEditField =
  | "action"
  | "duration_seconds"
  | "shot_size"
  | "camera_motion"
  | "composition"
  | "continuity_mode"
  | "previous_shot_id"
  | "approval_state";

export const SHOT_APPROVAL_STATES = [
  "draft",
  "needs_review",
  "approved",
  "rejected",
] as const satisfies readonly ShotApprovalState[];

export const SHOT_CONTINUITY_MODES = [
  "standard",
  "frame_chain",
] as const satisfies readonly ShotContinuityMode[];

export const SHOT_APPROVAL_LABELS: Record<ShotApprovalState, string> = {
  draft: "Draft",
  needs_review: "Needs review",
  approved: "Approved",
  rejected: "Rejected",
};

const SHOT_APPROVAL_TONES: Record<ShotApprovalState, string> = {
  draft: "border border-border bg-secondary text-secondary-foreground",
  needs_review: "border border-warning/30 bg-warning/10 text-warning",
  approved: "border border-success/30 bg-success/10 text-success",
  rejected: "border border-destructive/30 bg-destructive/10 text-destructive",
};

export const SHOT_GENERATION_LABELS: Record<ShotGenerationState, string> = {
  not_started: "Not started",
  blocked_reference: "Blocked: reference missing",
  blocked_upstream_frame: "Blocked: upstream frame missing",
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  canceled: "Canceled",
};

const SHOT_GENERATION_TONES: Record<ShotGenerationState, string> = {
  not_started: "border border-border bg-muted text-muted-foreground",
  blocked_reference: "border border-destructive/30 bg-destructive/10 text-destructive",
  blocked_upstream_frame: "border border-destructive/30 bg-destructive/10 text-destructive",
  queued: "border border-primary/30 bg-primary/10 text-primary",
  running: "border border-warning/30 bg-warning/10 text-warning",
  succeeded: "border border-success/30 bg-success/10 text-success",
  failed: "border border-destructive/30 bg-destructive/10 text-destructive",
  canceled: "border border-border bg-muted text-muted-foreground",
};

export const SHOT_CONTINUITY_LABELS: Record<ShotContinuityMode, string> = {
  standard: "Standard",
  frame_chain: "Frame chain",
};

const UNKNOWN_TONE = "border border-border bg-muted text-muted-foreground";

function hasKey<T extends string>(record: Record<T, string>, key: string): key is T {
  return Object.prototype.hasOwnProperty.call(record, key);
}

/** A newer backend may add a state before the client is regenerated; show the raw value. */
export function shotApprovalLabel(state: ShotApprovalState): string {
  return hasKey(SHOT_APPROVAL_LABELS, state)
    ? i18n.t(`shot-board:states.approval.${state}`, { defaultValue: SHOT_APPROVAL_LABELS[state] })
    : `Unknown approval (${String(state) || "empty"})`;
}

export function shotApprovalTone(state: ShotApprovalState): string {
  return hasKey(SHOT_APPROVAL_TONES, state) ? SHOT_APPROVAL_TONES[state] : UNKNOWN_TONE;
}

export function shotGenerationLabel(state: ShotGenerationState): string {
  return hasKey(SHOT_GENERATION_LABELS, state)
    ? i18n.t(`shot-board:states.generation.${state}`, {
        defaultValue: SHOT_GENERATION_LABELS[state],
      })
    : `Unknown state (${String(state) || "empty"})`;
}

export function shotGenerationTone(state: ShotGenerationState): string {
  return hasKey(SHOT_GENERATION_TONES, state) ? SHOT_GENERATION_TONES[state] : UNKNOWN_TONE;
}

/** `ProjectShotOut.continuity_mode` is an untyped string in the contract; label known values. */
export function shotContinuityLabel(mode: string): string {
  return hasKey(SHOT_CONTINUITY_LABELS, mode)
    ? i18n.t(`shot-board:states.continuity.${mode}`, { defaultValue: SHOT_CONTINUITY_LABELS[mode] })
    : mode || "Unknown";
}

export function isKnownContinuityMode(mode: string): mode is ShotContinuityMode {
  return hasKey(SHOT_CONTINUITY_LABELS, mode);
}

export function shotLintBlockers(issues: readonly ShotLintIssue[]): ShotLintIssue[] {
  return issues.filter((issue) => issue.severity === "blocker");
}

export function shotLintWarnings(issues: readonly ShotLintIssue[]): ShotLintIssue[] {
  return issues.filter((issue) => issue.severity === "warning");
}

/** Short operator-facing label: "Shot 3 · Hero enters". */
export function shotLabel(shot: Pick<ProjectShot, "sequence_index" | "title">): string {
  const title = shot.title.trim();
  return title ? `Shot ${shot.sequence_index} · ${title}` : `Shot ${shot.sequence_index}`;
}

export interface ShotSceneGroup {
  sceneId: string;
  shots: ProjectShot[];
}

/**
 * Group shots by scene, keeping scenes in the order of their first shot and shots in
 * `sequence_index` order. The API returns shots ordered by sequence index but does not
 * promise it, so sort defensively. Shared with the overview through `entities/shot/order`.
 */
export function groupShotsByScene(shots: readonly ProjectShot[]): ShotSceneGroup[] {
  return groupShotsByEntityScene(shots);
}

/** Backend `ShotUpdateIn` bounds (`MAX_SHOT_DURATION_SECONDS` and column lengths). */
export const SHOT_LIMITS = {
  maxDurationSeconds: 600,
  shotSizeMaxChars: 64,
  cameraMotionMaxChars: 64,
  compositionMaxChars: 2_000,
  actionMaxChars: 4_000,
} as const;

/** Values held by the edit form; mapped to a `ShotUpdateIn` patch containing changes only. */
export interface ShotEditValues {
  action: string;
  duration_seconds: number;
  shot_size: string;
  camera_motion: string;
  composition: string;
  continuity_mode: ShotContinuityMode;
  /** Empty string means "no previous shot" (sent as null). */
  previous_shot_id: string;
  approval_state: ShotApprovalState;
}

export function shotEditDefaults(shot: ProjectShot): ShotEditValues {
  return {
    action: shot.action,
    duration_seconds: shot.duration_seconds,
    shot_size: shot.shot_size,
    camera_motion: shot.camera_motion,
    composition: shot.composition,
    continuity_mode: isKnownContinuityMode(shot.continuity_mode)
      ? shot.continuity_mode
      : "standard",
    previous_shot_id: shot.previous_shot_id ?? "",
    approval_state: shot.approval_state,
  };
}

/**
 * Build a PATCH body with only the fields that differ from the stored shot. The backend
 * applies `exclude_unset`, so unchanged fields must be omitted rather than resent.
 */
export function buildShotUpdate(shot: ProjectShot, values: ShotEditValues): ShotUpdate {
  const update: ShotUpdate = {};
  const action = values.action.trim();
  if (action !== shot.action.trim()) update.action = action;
  if (values.duration_seconds !== shot.duration_seconds) {
    update.duration_seconds = values.duration_seconds;
  }
  const shotSize = values.shot_size.trim();
  if (shotSize !== shot.shot_size.trim()) update.shot_size = shotSize;
  const cameraMotion = values.camera_motion.trim();
  if (cameraMotion !== shot.camera_motion.trim()) update.camera_motion = cameraMotion;
  const composition = values.composition.trim();
  if (composition !== shot.composition.trim()) update.composition = composition;
  if (values.continuity_mode !== shot.continuity_mode) {
    update.continuity_mode = values.continuity_mode;
  }
  const previousShotId = values.previous_shot_id || null;
  if (previousShotId !== shot.previous_shot_id) update.previous_shot_id = previousShotId;
  if (values.approval_state !== shot.approval_state) update.approval_state = values.approval_state;
  return update;
}

/** Shots that may precede `shot`: the backend only accepts a predecessor in the same scene. */
export function previousShotCandidates(
  shot: ProjectShot,
  shots: readonly ProjectShot[],
): ProjectShot[] {
  return shots
    .filter((candidate) => candidate.id !== shot.id && candidate.scene_id === shot.scene_id)
    .sort((left, right) => left.sequence_index - right.sequence_index);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const SHOT_EDIT_FIELDS: readonly ShotEditField[] = [
  "action",
  "duration_seconds",
  "shot_size",
  "camera_motion",
  "composition",
  "continuity_mode",
  "previous_shot_id",
  "approval_state",
];

function isShotEditField(value: unknown): value is ShotEditField {
  return typeof value === "string" && (SHOT_EDIT_FIELDS as readonly string[]).includes(value);
}

export interface ShotUpdateErrorMapping {
  /** Messages for fields the edit form shows inline. */
  fieldErrors: Partial<Record<ShotEditField, string>>;
  /** Message shown above the form; null when every problem maps to a field. */
  formError: string | null;
}

/** Field names the backend mentions in its plain-text application errors. */
const FIELD_HINTS: ReadonlyArray<[ShotEditField, RegExp]> = [
  ["previous_shot_id", /previous_shot_id/i],
  ["approval_state", /approve|blocker/i],
];

/**
 * Map a failed `PATCH /api/v1/projects/{project_id}/shots/{shot_id}` to form errors.
 * Request validation (422) returns `detail` as a list of `{loc, msg}` items; application
 * errors (409/422) return a plain-text `detail`.
 */
export function mapShotUpdateError(error: unknown): ShotUpdateErrorMapping {
  if (!(error instanceof ApiError)) {
    return {
      fieldErrors: {},
      formError:
        error instanceof Error
          ? i18n.t("shot-board:errors.saveShotFailed", {
              message: error.message,
              defaultValue: `Could not save the shot: ${error.message}`,
            })
          : i18n.t("shot-board:errors.saveShotFailed", {
              message: "",
              defaultValue: "Could not save the shot.",
            }),
    };
  }
  if (error.status === 401 || error.status === 403) {
    return {
      fieldErrors: {},
      formError:
        error.status === 403
          ? i18n.t("common:errors.actionPermissionDenied", {
              action: "lưu shot",
              defaultValue: "The current session does not have permission to edit shots.",
            })
          : i18n.t("common:errors.actionTokenRejected", {
              action: "lưu shot",
              defaultValue:
                "The admin token was rejected while saving the shot. Update the token and try again.",
            }),
    };
  }
  if (error.status === 404) {
    return {
      fieldErrors: {},
      formError: i18n.t("shot-board:errors.shotNotFound", {
        defaultValue: "This shot no longer exists in the project. Refresh the shot list.",
      }),
    };
  }

  const fieldErrors: Partial<Record<ShotEditField, string>> = {};
  const unmapped: string[] = [];
  if (Array.isArray(error.detail)) {
    for (const item of error.detail) {
      if (!isRecord(item)) continue;
      const message = typeof item.msg === "string" ? item.msg : "Invalid value.";
      const location = Array.isArray(item.loc) ? item.loc : [];
      const field = location[0] === "body" ? location[1] : undefined;
      if (isShotEditField(field) && !fieldErrors[field]) fieldErrors[field] = message;
      else unmapped.push(field ? `${String(field)}: ${message}` : message);
    }
  } else if (error.status === 409 || error.status === 422) {
    const field = FIELD_HINTS.find(([, pattern]) => pattern.test(error.message))?.[0];
    if (field) fieldErrors[field] = error.message;
    else unmapped.push(error.message);
  } else {
    unmapped.push(error.message);
  }

  const hasFieldErrors = Object.keys(fieldErrors).length > 0;
  let formError: string | null = null;
  if (unmapped.length > 0) {
    formError = i18n.t("shot-board:errors.saveShotFailed", {
      message: unmapped.join("; "),
      defaultValue: `The shot was not saved: ${unmapped.join("; ")}`,
    });
  } else if (!hasFieldErrors) {
    formError = i18n.t("shot-board:errors.saveShotFailed", {
      message: error.message,
      defaultValue: `The shot was not saved: ${error.message}`,
    });
  }
  return { fieldErrors, formError };
}

/** A model discovered by the last connection test (`capabilities_snapshot.models`). */
export interface ConnectionModelOption {
  modelId: string;
  modality: string;
  verificationStatus: string;
}

/**
 * `capabilities_snapshot` is generic JSON in the OpenAPI contract. Read the `models` list
 * written by the connection test defensively and ignore anything malformed.
 */
export function connectionModelOptions(connection: Connection): ConnectionModelOption[] {
  const models = connection.capabilities_snapshot.models;
  if (!Array.isArray(models)) return [];
  const options: ConnectionModelOption[] = [];
  for (const model of models) {
    if (!isRecord(model) || typeof model.model_id !== "string" || !model.model_id.trim()) continue;
    options.push({
      modelId: model.model_id,
      modality: typeof model.modality === "string" ? model.modality : "unknown",
      verificationStatus:
        typeof model.verification_status === "string" ? model.verification_status : "unknown",
    });
  }
  return options;
}

/** Discovered models for a task type; models of an unknown modality are offered for both. */
export function modelOptionsForTaskType(
  connection: Connection | undefined,
  taskType: GenerationTaskType,
): ConnectionModelOption[] {
  if (!connection) return [];
  return connectionModelOptions(connection).filter(
    (model) => model.modality === taskType || model.modality === "unknown",
  );
}

/** The mock provider never calls a paid upstream; every other provider may consume quota. */
export function isMockConnection(connection: Pick<Connection, "provider_type">): boolean {
  return connection.provider_type === "mock";
}

/**
 * Backend defaults for `EnqueueTaskIn` (generated types mark defaulted fields required).
 * `idempotency_key` is deliberately omitted so the backend deduplicates by content, and
 * `force_retry` defaults to false: only the "Start a new task" confirmation sets it
 * (`buildEnqueueRequest({ forceRetry: true })`).
 */
export const ENQUEUE_DEFAULTS = { priority: 10, max_polls: 60, force_retry: false } as const;

export function buildEnqueueRequest(input: {
  shotId: string;
  connectionId: string;
  modelId: string;
  taskType: GenerationTaskType;
  /** Start a new task even when an identical request already ran; needs explicit consent. */
  forceRetry?: boolean;
}): EnqueueTaskRequest {
  return {
    shot_id: input.shotId,
    connection_id: input.connectionId,
    model_id: input.modelId.trim(),
    task_type: input.taskType,
    ...ENQUEUE_DEFAULTS,
    force_retry: input.forceRetry ?? false,
  };
}

export type BatchScope = { kind: "project" } | { kind: "scene"; sceneId: string };

export function buildBatchLaunchRequest(input: {
  connectionId: string;
  modelId: string;
  scope: BatchScope;
  onlyChanged: boolean;
}): BatchLaunchRequest {
  return {
    connection_id: input.connectionId,
    model_id: input.modelId.trim(),
    only_changed: input.onlyChanged,
    ...(input.scope.kind === "scene" ? { scene_id: input.scope.sceneId } : {}),
  };
}

/** Shots a batch would target, mirroring the backend scene filter. */
export function batchTargetShots(shots: readonly ProjectShot[], scope: BatchScope): ProjectShot[] {
  return scope.kind === "scene"
    ? shots.filter((shot) => shot.scene_id === scope.sceneId)
    : [...shots];
}

/** Operator-facing message for a failed enqueue or batch launch. */
export function generationErrorMessage(error: unknown, action: "enqueue" | "batch"): string {
  const errorKey =
    action === "batch" ? "shot-board:errors.batchFailed" : "shot-board:errors.enqueueFailed";
  const defaultSubject =
    action === "batch" ? "The batch was not launched" : "The shot was not queued";
  if (!(error instanceof ApiError)) {
    const msg = error instanceof Error ? error.message : "";
    return i18n.t(errorKey, {
      message: msg,
      defaultValue: msg ? `${defaultSubject}: ${msg}` : `${defaultSubject}.`,
    });
  }
  if (error.status === 401) {
    return i18n.t("common:errors.actionTokenRejected", {
      action: action === "batch" ? "khởi tạo đợt sinh" : "đưa shot vào hàng đợi",
      defaultValue: `${defaultSubject}: the admin token was rejected. Update the token and try again.`,
    });
  }
  if (error.status === 403) {
    return i18n.t("common:errors.actionPermissionDenied", {
      action: action === "batch" ? "khởi tạo đợt sinh" : "bắt đầu sinh",
      defaultValue: `${defaultSubject}: the current session does not have permission to start generation.`,
    });
  }
  if (
    action === "batch" &&
    error.status === 503 &&
    isRecord(error.detail) &&
    error.detail.code === "queue_dispatch_unavailable"
  ) {
    const batchId = typeof error.detail.batch_id === "string" ? ` (${error.detail.batch_id})` : "";
    return `The batch${batchId} was saved, but some tasks could not be scheduled yet: ${error.message}`;
  }
  return i18n.t(errorKey, {
    message: error.message,
    defaultValue: `${defaultSubject}: ${error.message}`,
  });
}

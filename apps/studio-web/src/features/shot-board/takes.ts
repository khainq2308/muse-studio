import { ApiError } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { shotLabel, type ProjectShot } from "@/features/shot-board/state";
import i18n from "@/lib/i18n";

export type ShotTake = components["schemas"]["ShotTakeOut"];
export type ShotTakeSelection = components["schemas"]["ShotTakeSelectionOut"];
export type CompareShotTakes = components["schemas"]["CompareShotTakesOut"];
export type TakeComparison = components["schemas"]["TakeComparisonOut"];
export type ContinuityFlagRequest = components["schemas"]["ContinuityFlagIn"];
export type ContinuityFlagResult = components["schemas"]["ContinuityFlagOut"];

/**
 * In-browser preview bounds. The asset download is buffered into memory (no range/seek), so
 * a preview is only fetched when the operator opens it and is refused above these sizes.
 */
export const FRAME_PREVIEW_MAX_BYTES = 20 * 1024 * 1024;
export const TAKE_PREVIEW_MAX_BYTES = 200 * 1024 * 1024;

export function takeLabel(take: Pick<ShotTake, "take_number">): string {
  return i18n.t("shot-board:takes.takeLabel", {
    number: take.take_number,
    defaultValue: `Take ${take.take_number}`,
  });
}

/** Parse an ISO-8601 `ShotTakeOut.created_at` string, returning `null` if unparseable. */
export function parseTakeCreatedAt(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp) : null;
}

/** Takes in take-number order (the API orders them, but do not depend on it). */
export function sortTakes(takes: readonly ShotTake[]): ShotTake[] {
  return [...takes].sort(
    (left, right) => left.take_number - right.take_number || left.id.localeCompare(right.id),
  );
}

export interface ContinuityFlagInput {
  take: Pick<ShotTake, "model_id">;
  flagged: boolean;
  directorNotes: string;
  /** Present only when the operator confirmed an immediate regeneration. */
  regenerate?: { connectionId: string; modelId: string };
}

/**
 * Build a `ContinuityFlagIn` body. With regeneration the connection and model are always sent
 * explicitly, because the backend would otherwise fall back to the original task's connection
 * and a hard-coded default model. Without it no connection is sent; the generated type still
 * requires `model_id` (it has a server default), so the take's own model is echoed — the
 * backend reads `model_id` only when `regenerate_now` is true.
 */
export function buildContinuityFlagRequest(input: ContinuityFlagInput): ContinuityFlagRequest {
  if (input.regenerate) {
    return {
      continuity_flagged: input.flagged,
      director_notes: input.directorNotes.trim(),
      regenerate_now: true,
      connection_id: input.regenerate.connectionId,
      model_id: input.regenerate.modelId.trim(),
    };
  }
  return {
    continuity_flagged: input.flagged,
    director_notes: input.directorNotes.trim(),
    regenerate_now: false,
    model_id: input.take.model_id,
  };
}

/** Operator-facing labels for the shots a take selection re-pointed (unknown IDs stay raw). */
export function downstreamShotLabels(
  shotIds: readonly string[],
  shots: readonly ProjectShot[],
): string[] {
  const byId = new Map(shots.map((shot) => [shot.id, shot]));
  return shotIds.map((id) => {
    const shot = byId.get(id);
    return shot ? shotLabel(shot) : id;
  });
}

/**
 * Product decision: a predecessor whose `sequence_index` is not lower than the shot's own
 * is allowed by the backend but almost always a mistake, so the UI warns without blocking.
 * The script parser numbers shots across the whole project, but the backend only accepts a
 * previous shot from the same scene, so only same-scene pairs are compared.
 */
export function isPredecessorOutOfOrder(
  shot: Pick<ProjectShot, "sequence_index" | "scene_id">,
  previousShot: Pick<ProjectShot, "sequence_index" | "scene_id"> | undefined,
): boolean {
  return (
    previousShot !== undefined &&
    previousShot.scene_id === shot.scene_id &&
    previousShot.sequence_index >= shot.sequence_index
  );
}

export const PREDECESSOR_ORDER_WARNING = "Previous shot comes after this shot in the sequence";

export type TakeAction = "select" | "flag" | "load" | "compare" | "preview";

const ACTION_KEYS: Record<TakeAction, string> = {
  select: "shot-board:errors.takeSelectFailed",
  flag: "shot-board:errors.takeFlagFailed",
  load: "shot-board:errors.loadTakesFailed",
  compare: "shot-board:errors.compareTakesFailed",
  preview: "shot-board:errors.previewTakeFailed",
};

const ACTION_SUBJECTS: Record<TakeAction, string> = {
  select: "The take was not selected",
  flag: "The continuity flag was not saved",
  load: "Could not load takes",
  compare: "Could not compare the takes",
  preview: "Could not load the preview",
};

/** Message for a failed take request: auth, 400/404/409/422 and size limits stay inline. */
export function takeActionErrorMessage(error: unknown, action: TakeAction): string {
  const errorKey = ACTION_KEYS[action];
  const subject = ACTION_SUBJECTS[action];
  if (!(error instanceof ApiError)) {
    const msg = error instanceof Error ? error.message : "";
    return i18n.t(errorKey, {
      message: msg,
      defaultValue: msg ? `${subject}: ${msg}` : `${subject}.`,
    });
  }
  if (error.status === 401) {
    return i18n.t("common:errors.actionTokenRejected", {
      action: action,
      defaultValue: `${subject}: the admin token was rejected. Update the token and try again.`,
    });
  }
  if (error.status === 403) {
    return i18n.t("common:errors.actionPermissionDenied", {
      action: action,
      defaultValue: `${subject}: the current session does not have permission for this action.`,
    });
  }
  if (error.category === "too_large") {
    return i18n.t(errorKey, {
      message: error.message,
      defaultValue: `${subject}: ${error.message}`,
    });
  }
  if (error.category === "timeout") {
    return i18n.t(errorKey, {
      message: i18n.t("common:errors.timeoutGeneric", {
        defaultValue: "the request timed out. Try again.",
      }),
      defaultValue: `${subject}: the request timed out. Try again.`,
    });
  }
  if (error.status === 404) {
    const missing =
      action === "preview"
        ? "the asset no longer exists"
        : action === "flag"
          ? `the shot, take or connection was not found (${error.message})`
          : `the shot or take was not found (${error.message})`;
    return i18n.t(errorKey, {
      message: `${missing}. Refresh and try again.`,
      defaultValue: `${subject}: ${missing}. Refresh and try again.`,
    });
  }
  return i18n.t(errorKey, {
    message: error.message,
    defaultValue: `${subject}: ${error.message}`,
  });
}

/** Longer bound for a flag that also enqueues a regeneration (the server does more work). */
export const REGENERATE_REQUEST_TIMEOUT_MS = 60_000;

export interface FlagErrorFeedback {
  message: string;
  /**
   * The request may have succeeded on the server (flag saved, regeneration task created):
   * the operator must check the Queue Monitor and take list instead of retrying blindly.
   */
  outcomeUnknown: boolean;
}

/** No HTTP answer, or a server failure: the server may have processed the request. */
function isUncertainFailure(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status === 0 || error.category === "timeout" || error.status >= 500;
}

/**
 * Feedback for a failed `flag-continuity` request. The backend saves the flag before it
 * enqueues the regeneration with `force_retry`, so after an uncertain failure a retry could
 * start a second paid generation, and a 422 after regeneration may follow a saved flag.
 */
export function flagErrorFeedback(error: unknown, regenerate: boolean): FlagErrorFeedback {
  if (regenerate && isUncertainFailure(error)) {
    return {
      message: i18n.t("shot-board:errors.flagOutcomeUnknown", {
        defaultValue:
          "The outcome is unknown: the continuity flag and a regeneration task may already exist. " +
          "Check the Generation Queue and refresh the take list before retrying, otherwise a second " +
          "generation may be started.",
      }),
      outcomeUnknown: true,
    };
  }
  if (regenerate && error instanceof ApiError && error.status === 422) {
    return {
      message: i18n.t("shot-board:errors.flagRefusedRegen", {
        message: error.message.replace(/\.+$/, ""),
        defaultValue:
          `The regeneration was refused: ${error.message.replace(/\.+$/, "")}. ` +
          "The continuity flag may already be " +
          "saved; refresh the take list to check it before retrying.",
      }),
      outcomeUnknown: true,
    };
  }
  return { message: takeActionErrorMessage(error, "flag"), outcomeUnknown: false };
}

import { ApiError } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { shotLabel, type ProjectShot } from "@/features/shot-board/state";

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
  return `Take ${take.take_number}`;
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
 */
export function isPredecessorOutOfOrder(
  shot: Pick<ProjectShot, "sequence_index">,
  previousShot: Pick<ProjectShot, "sequence_index"> | undefined,
): boolean {
  return previousShot !== undefined && previousShot.sequence_index >= shot.sequence_index;
}

export const PREDECESSOR_ORDER_WARNING = "Previous shot comes after this shot in the sequence";

export type TakeAction = "select" | "flag" | "load" | "compare" | "preview";

const ACTION_SUBJECTS: Record<TakeAction, string> = {
  select: "The take was not selected",
  flag: "The continuity flag was not saved",
  load: "Could not load takes",
  compare: "Could not compare the takes",
  preview: "Could not load the preview",
};

/** Message for a failed take request: auth, 400/404/409/422 and size limits stay inline. */
export function takeActionErrorMessage(error: unknown, action: TakeAction): string {
  const subject = ACTION_SUBJECTS[action];
  if (!(error instanceof ApiError)) {
    return error instanceof Error ? `${subject}: ${error.message}` : `${subject}.`;
  }
  if (error.status === 401) {
    return `${subject}: the admin token was rejected. Update the token and try again.`;
  }
  if (error.status === 403) {
    return `${subject}: the current session does not have permission for this action.`;
  }
  if (error.category === "too_large") return `${subject}: ${error.message}`;
  if (error.category === "timeout") return `${subject}: the request timed out. Try again.`;
  if (error.status === 404) {
    const missing =
      action === "preview"
        ? "the asset no longer exists"
        : action === "flag"
          ? `the shot, take or connection was not found (${error.message})`
          : `the shot or take was not found (${error.message})`;
    return `${subject}: ${missing}. Refresh and try again.`;
  }
  return `${subject}: ${error.message}`;
}

import type { components } from "@/lib/api/schema";
import { ApiError } from "@/lib/api/client";

/**
 * Local, unsaved clip edits for the timeline editor. The draft is UI state derived from the saved
 * sequence (`manifest.video_clips`); it is only sent to the server as `custom_clips` when the user
 * saves. Placement and bounds mirror the backend:
 * - `ClipEditItemIn` bounds and its `trim_start + trim_end < source_duration` validator
 *   (apps/orchestrator/muse_orchestrator/api/timeline.py);
 * - sequential placement in `TimelineValidationService.analyze_clips_and_detect_anomalies` and
 *   the gap/overlap/dissolve rules of `build_export_plan`
 *   (apps/orchestrator/muse_orchestrator/services/timeline_service.py).
 */

type TimelineClip = components["schemas"]["TimelineClipOut"];
type TimelineAudioTrack = components["schemas"]["TimelineAudioTrackOut"];
export type ClipEditItem = components["schemas"]["ClipEditItemIn"];
export type AudioTrackItem = components["schemas"]["AudioTrackItemIn"];
export type ClipTransition = TimelineClip["transition_type"];

/** Backend `_GAP_TOLERANCE_SECONDS`: smaller start differences are treated as touching clips. */
export const GAP_TOLERANCE_SECONDS = 0.05;
/** Backend `_MIN_TRANSITION_MARGIN_SECONDS`: a dissolve must be this much shorter than its clips. */
export const TRANSITION_MARGIN_SECONDS = 0.05;
/** ClipEditItemIn bounds. */
export const MAX_TRIM_SECONDS = 600;
export const MIN_SOURCE_DURATION_SECONDS = 0.2;
export const MAX_SOURCE_DURATION_SECONDS = 600;
export const MAX_CLIP_TRANSITION_SECONDS = 5;
export const MAX_START_TIME_SECONDS = 86_400;
/** The backend floors effective duration at 0.1 s; the editor requires at least that much media. */
export const MIN_EFFECTIVE_DURATION_SECONDS = 0.1;
/** Gaps at or below the export tolerance are not rendered, so the editor asks for 0 or ≥ 0.1 s. */
export const MIN_GAP_SECONDS = 0.1;

export interface DraftClip {
  /** Stable identity for selection and drag-and-drop; never sent to the server. */
  key: string;
  /** 1-based position in the saved sequence, for labels. */
  savedPosition: number;
  shotId: string | null;
  takeId: string | null;
  assetId: string | null;
  sourceDurationSeconds: number;
  trimStartSeconds: number;
  trimEndSeconds: number;
  transitionType: ClipTransition;
  transitionDurationSeconds: number;
  /** Black gap before this clip (seconds after the previous clip ends; for the first clip, after 0). */
  gapBeforeSeconds: number;
  /** True once the user typed a gap in this draft; such a gap moves with the clip on reorder. */
  gapSetByUser: boolean;
}

export interface ClipDraft {
  clips: DraftClip[];
  /** Saved clips whose start overlapped the previous clip without a matching dissolve. */
  normalizedOverlaps: number[];
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/** Backend effective duration: `max(0.1, round(source - trim_start - trim_end, 3))`. */
export function effectiveDuration(clip: DraftClip): number {
  const raw = clip.sourceDurationSeconds - clip.trimStartSeconds - clip.trimEndSeconds;
  if (!Number.isFinite(raw)) return MIN_EFFECTIVE_DURATION_SECONDS;
  return Math.max(MIN_EFFECTIVE_DURATION_SECONDS, round3(raw));
}

/** The first clip always opens with a hard cut; the backend/exporter ignore a dissolve there. */
export function transitionFor(clip: DraftClip, index: number): ClipTransition {
  return index === 0 ? "hard_cut" : clip.transitionType;
}

function dissolveFor(clip: DraftClip, index: number): number {
  if (transitionFor(clip, index) !== "cross_dissolve") return 0;
  const value = clip.transitionDurationSeconds;
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}

/**
 * Builds the draft from the saved clips. Each saved absolute start is turned back into a gap
 * relative to where sequential placement would put the clip, so trimming or reordering earlier
 * clips keeps later clips attached instead of leaving stale absolute starts behind.
 */
export function draftFromClips(saved: readonly TimelineClip[]): ClipDraft {
  const ordered = [...saved].sort((a, b) => a.order_index - b.order_index);
  const normalizedOverlaps: number[] = [];
  let total = 0;
  const clips = ordered.map((clip, index): DraftClip => {
    const draft: DraftClip = {
      key: `clip-${index}-${clip.order_index}-${clip.take_id ?? clip.shot_id ?? "none"}`,
      savedPosition: index + 1,
      shotId: clip.shot_id ?? null,
      takeId: clip.take_id ?? null,
      assetId: clip.asset_id ?? null,
      sourceDurationSeconds: clip.source_duration_seconds,
      trimStartSeconds: clip.trim_start_seconds,
      trimEndSeconds: clip.trim_end_seconds,
      transitionType: index === 0 ? "hard_cut" : clip.transition_type,
      transitionDurationSeconds:
        index === 0 || clip.transition_type !== "cross_dissolve"
          ? 0
          : clip.transition_duration_seconds,
      gapBeforeSeconds: 0,
      gapSetByUser: false,
    };
    const sequentialStart =
      index === 0 ? 0 : Math.max(0, round3(total - dissolveFor(draft, index)));
    const lead = round3(clip.start_time_seconds - sequentialStart);
    if (lead > GAP_TOLERANCE_SECONDS) {
      draft.gapBeforeSeconds = lead;
    } else if (lead < -GAP_TOLERANCE_SECONDS) {
      normalizedOverlaps.push(index + 1);
    }
    const start =
      draft.gapBeforeSeconds > 0 ? round3(total + draft.gapBeforeSeconds) : sequentialStart;
    total = Math.max(total, round3(start + effectiveDuration(draft)));
    return draft;
  });
  return { clips, normalizedOverlaps };
}

export interface ClipLayout {
  startSeconds: number;
  effectiveSeconds: number;
  /** Absolute start sent to the server, or null for sequential placement. */
  explicitStartSeconds: number | null;
}

/** Positions on the track, computed exactly as the backend will when the draft is saved. */
export function layoutClips(clips: readonly DraftClip[]): {
  layouts: ClipLayout[];
  totalSeconds: number;
} {
  let total = 0;
  const layouts = clips.map((clip, index): ClipLayout => {
    const effectiveSeconds = effectiveDuration(clip);
    const gap = Number.isFinite(clip.gapBeforeSeconds) ? Math.max(0, clip.gapBeforeSeconds) : 0;
    let startSeconds: number;
    let explicitStartSeconds: number | null = null;
    if (gap > 0) {
      startSeconds = round3(total + gap);
      explicitStartSeconds = startSeconds;
    } else {
      startSeconds = index === 0 ? 0 : Math.max(0, round3(total - dissolveFor(clip, index)));
    }
    total = Math.max(total, round3(startSeconds + effectiveSeconds));
    return { startSeconds, effectiveSeconds, explicitStartSeconds };
  });
  return { layouts, totalSeconds: total };
}

export interface ClipFieldErrors {
  trimStart?: string;
  trimEnd?: string;
  trims?: string;
  transition?: string;
  gap?: string;
  source?: string;
}

function hasNumber(value: number): boolean {
  return typeof value === "number" && Number.isFinite(value);
}

function formatLimit(value: number): string {
  return `${Number(Math.max(0, value).toFixed(2))} s`;
}

/** Live validation mirroring the backend bounds; an empty object means the clip can be saved. */
export function validateClip(
  clips: readonly DraftClip[],
  index: number,
  layouts: readonly ClipLayout[] = layoutClips(clips).layouts,
): ClipFieldErrors {
  const clip = clips[index];
  const errors: ClipFieldErrors = {};
  if (!clip) return errors;
  const source = clip.sourceDurationSeconds;
  if (
    !hasNumber(source) ||
    source < MIN_SOURCE_DURATION_SECONDS ||
    source > MAX_SOURCE_DURATION_SECONDS
  ) {
    errors.source = `The saved source duration must be between ${MIN_SOURCE_DURATION_SECONDS} and ${MAX_SOURCE_DURATION_SECONDS} s; this clip cannot be re-saved.`;
  }
  if (!hasNumber(clip.trimStartSeconds)) errors.trimStart = "Enter a number of seconds.";
  else if (clip.trimStartSeconds < 0 || clip.trimStartSeconds > MAX_TRIM_SECONDS) {
    errors.trimStart = `Trim start must be between 0 and ${MAX_TRIM_SECONDS} s.`;
  }
  if (!hasNumber(clip.trimEndSeconds)) errors.trimEnd = "Enter a number of seconds.";
  else if (clip.trimEndSeconds < 0 || clip.trimEndSeconds > MAX_TRIM_SECONDS) {
    errors.trimEnd = `Trim end must be between 0 and ${MAX_TRIM_SECONDS} s.`;
  }
  if (!errors.trimStart && !errors.trimEnd && hasNumber(source)) {
    const remaining = round3(source - clip.trimStartSeconds - clip.trimEndSeconds);
    if (remaining < MIN_EFFECTIVE_DURATION_SECONDS) {
      errors.trims = `Trim start + trim end must leave at least ${MIN_EFFECTIVE_DURATION_SECONDS} s of the ${formatLimit(source)} source (max combined trim ${formatLimit(source - MIN_EFFECTIVE_DURATION_SECONDS)}).`;
    }
  }

  const gap = clip.gapBeforeSeconds;
  if (!hasNumber(gap)) errors.gap = "Enter a number of seconds (0 for no gap).";
  else if (gap < 0) errors.gap = "The gap cannot be negative.";
  else if (gap > 0 && gap < MIN_GAP_SECONDS) {
    errors.gap = `Use 0 or at least ${MIN_GAP_SECONDS} s; shorter gaps are not rendered.`;
  } else if ((layouts[index]?.startSeconds ?? 0) > MAX_START_TIME_SECONDS) {
    errors.gap = `The clip would start after ${MAX_START_TIME_SECONDS} s.`;
  }

  if (index > 0 && clip.transitionType === "cross_dissolve") {
    const duration = clip.transitionDurationSeconds;
    const previous = clips[index - 1];
    const limit = Math.min(
      MAX_CLIP_TRANSITION_SECONDS,
      effectiveDuration(clip) - TRANSITION_MARGIN_SECONDS,
      previous ? effectiveDuration(previous) - TRANSITION_MARGIN_SECONDS : Infinity,
    );
    if (!hasNumber(duration)) errors.transition = "Enter a dissolve duration in seconds.";
    else if (duration <= 0) errors.transition = "A cross dissolve needs a duration above 0 s.";
    else if (duration > MAX_CLIP_TRANSITION_SECONDS) {
      errors.transition = `A cross dissolve can last at most ${MAX_CLIP_TRANSITION_SECONDS} s.`;
    } else if (duration > limit) {
      errors.transition = `The dissolve must be shorter than this clip and the previous clip (max ${formatLimit(limit)}).`;
    } else if (hasNumber(gap) && gap > 0) {
      errors.transition =
        "A cross dissolve needs the clip to follow the previous clip directly. Set the gap to 0 or use a hard cut.";
    }
  }
  return errors;
}

export function validateDraft(clips: readonly DraftClip[]): ClipFieldErrors[] {
  const { layouts } = layoutClips(clips);
  return clips.map((_clip, index) => validateClip(clips, index, layouts));
}

export function errorCount(errors: ClipFieldErrors): number {
  return Object.values(errors).filter(Boolean).length;
}

/**
 * Serializes the draft as `custom_clips`, renumbering `order_index` 1..n in draft order. Clips
 * without a gap are sent with `start_time_seconds: null` so the backend places them right after
 * the previous clip. Clip audio is not rendered by the exporter, so volume/fades are the defaults.
 */
export function toCustomClips(clips: readonly DraftClip[]): ClipEditItem[] {
  const { layouts } = layoutClips(clips);
  return clips.map((clip, index) => {
    const transition = transitionFor(clip, index);
    return {
      shot_id: clip.shotId,
      take_id: clip.takeId,
      asset_id: clip.assetId,
      order_index: index + 1,
      start_time_seconds: layouts[index]?.explicitStartSeconds ?? null,
      source_duration_seconds: clip.sourceDurationSeconds,
      trim_start_seconds: clip.trimStartSeconds,
      trim_end_seconds: clip.trimEndSeconds,
      transition_type: transition,
      transition_duration_seconds:
        transition === "cross_dissolve" ? clip.transitionDurationSeconds : 0,
      volume: 1,
      fade_in_seconds: 0,
      fade_out_seconds: 0,
    };
  });
}

export function isDraftDirty(saved: readonly DraftClip[], draft: readonly DraftClip[]): boolean {
  if (saved.length !== draft.length) return true;
  if (saved.some((clip, index) => clip.key !== draft[index]?.key)) return true;
  return JSON.stringify(toCustomClips(saved)) !== JSON.stringify(toCustomClips(draft));
}

export type ClipPatch = Partial<
  Pick<
    DraftClip,
    | "trimStartSeconds"
    | "trimEndSeconds"
    | "transitionType"
    | "transitionDurationSeconds"
    | "gapBeforeSeconds"
  >
>;

export function updateClip(
  clips: readonly DraftClip[],
  key: string,
  patch: ClipPatch,
): DraftClip[] {
  return clips.map((clip) => {
    if (clip.key !== key) return clip;
    const next = { ...clip, ...patch };
    if ("gapBeforeSeconds" in patch) next.gapSetByUser = true;
    if (patch.transitionType === "hard_cut") next.transitionDurationSeconds = 0;
    if (
      patch.transitionType === "cross_dissolve" &&
      patch.transitionDurationSeconds === undefined &&
      !(clip.transitionDurationSeconds > 0)
    ) {
      // A sensible starting value when switching to a dissolve; the user can change it.
      next.transitionDurationSeconds = 0.5;
    }
    return next;
  });
}

/**
 * Moves a clip. A moved clip loses a gap it inherited from the saved sequence (that offset was
 * absolute and belonged to its old neighbours) and is placed right after its new neighbour; a gap
 * the user typed in this draft moves with it.
 */
export function moveClip(clips: readonly DraftClip[], from: number, to: number): DraftClip[] {
  if (from === to || from < 0 || to < 0 || from >= clips.length || to >= clips.length) {
    return [...clips];
  }
  const next = [...clips];
  const [moved] = next.splice(from, 1);
  if (!moved) return [...clips];
  next.splice(to, 0, moved.gapSetByUser ? moved : { ...moved, gapBeforeSeconds: 0 });
  return next;
}

export function removeClip(clips: readonly DraftClip[], key: string): DraftClip[] {
  return clips.filter((clip) => clip.key !== key);
}

/** Saved audio track entries, re-sent unchanged so saving clip edits does not drop them. */
export function carryOverAudioTracks(tracks: readonly TimelineAudioTrack[]): AudioTrackItem[] {
  return tracks.map((track) => ({
    track_type: track.track_type,
    name: track.name,
    asset_id: track.asset_id ?? null,
    volume: track.volume,
    fade_in_seconds: track.fade_in_seconds,
    fade_out_seconds: track.fade_out_seconds,
    muted: track.muted,
  }));
}

const CUSTOM_CLIP_DETAIL = /^custom_clips\[(\d+)\]:\s*(.*)$/s;

/**
 * Finds which draft position a 422 refers to: ownership errors are strings such as
 * `custom_clips[2]: ...`, schema errors are Pydantic items with `loc: ["body", "custom_clips", 2]`.
 */
export function clipErrorFromApi(error: unknown): { index: number; message: string } | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const { detail } = error;
  if (typeof detail === "string") {
    const match = CUSTOM_CLIP_DETAIL.exec(detail.trim());
    return match ? { index: Number(match[1]), message: match[2] ?? "" } : null;
  }
  if (Array.isArray(detail)) {
    for (const item of detail) {
      if (typeof item !== "object" || item === null) continue;
      const { loc, msg } = item as { loc?: unknown; msg?: unknown };
      if (!Array.isArray(loc)) continue;
      const at = loc.indexOf("custom_clips");
      const index = at >= 0 ? loc[at + 1] : undefined;
      if (typeof index === "number") {
        return { index, message: typeof msg === "string" ? msg : "Rejected by the server." };
      }
    }
  }
  return null;
}

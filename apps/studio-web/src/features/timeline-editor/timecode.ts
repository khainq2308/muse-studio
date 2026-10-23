/** Timecode and ruler helpers for the read-only sequence view. UI-only; not an API contract. */

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * Formats seconds as `HH:MM:SS`, or `HH:MM:SS:FF` when `fps` is given. Hours are not capped,
 * minutes and seconds wrap at 60, and frames are floored to the frame the time falls in.
 */
export function formatTimecode(seconds: number, fps?: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  const rate = fps !== undefined && Number.isFinite(fps) && fps > 0 ? fps : null;
  // Work in whole frames (or whole seconds) so rounding can never produce SS=60 or FF=fps.
  const unitsPerSecond = rate ?? 1;
  const totalUnits = Math.floor(safe * unitsPerSecond + 1e-6);
  const wholeSeconds = rate ? Math.floor(totalUnits / rate) : totalUnits;
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor(wholeSeconds / 60) % 60;
  const secs = wholeSeconds % 60;
  const base = `${pad2(hours)}:${pad2(minutes)}:${pad2(secs)}`;
  if (!rate) return base;
  const frames = Math.floor(totalUnits - wholeSeconds * rate);
  return `${base}:${pad2(frames)}`;
}

/** Candidate ruler steps in seconds, smallest first. */
const RULER_STEPS_SECONDS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600] as const;

/** Minimum horizontal distance between two ruler labels (an `HH:MM:SS` label is ~50px). */
export const MIN_TICK_SPACING_PX = 72;

/** Smallest clip width the track view aims for, so shot/take ids stay readable. */
const TARGET_MIN_CLIP_PX = 160;
const MIN_PIXELS_PER_SECOND = 16;
const MAX_PIXELS_PER_SECOND = 240;

/**
 * Horizontal scale for the track view: clips are drawn proportional to their duration, scaled
 * so the shortest clip is still about TARGET_MIN_CLIP_PX wide (within sane bounds).
 */
export function pixelsPerSecondFor(clipDurations: readonly number[]): number {
  const positive = clipDurations.filter((duration) => Number.isFinite(duration) && duration > 0);
  if (positive.length === 0) return 40;
  const shortest = Math.min(...positive);
  return Math.min(
    MAX_PIXELS_PER_SECOND,
    Math.max(MIN_PIXELS_PER_SECOND, Math.ceil(TARGET_MIN_CLIP_PX / shortest)),
  );
}

/**
 * Ruler tick positions (seconds) from 0 up to `totalSeconds`, using the smallest step whose
 * on-screen spacing is at least MIN_TICK_SPACING_PX so labels never overlap.
 */
export function rulerTicks(totalSeconds: number, pixelsPerSecond: number): number[] {
  const total = Number.isFinite(totalSeconds) ? Math.max(0, totalSeconds) : 0;
  const scale = Number.isFinite(pixelsPerSecond) && pixelsPerSecond > 0 ? pixelsPerSecond : 1;
  const step =
    RULER_STEPS_SECONDS.find((candidate) => candidate * scale >= MIN_TICK_SPACING_PX) ??
    RULER_STEPS_SECONDS[RULER_STEPS_SECONDS.length - 1];
  const ticks: number[] = [];
  for (let tick = 0; tick <= total + 1e-6; tick += step) ticks.push(tick);
  return ticks;
}

import type { components } from "@/lib/api/schema";

export type QueueTask = components["schemas"]["QueueTaskOut"];
export type QueueTaskPage = components["schemas"]["QueueTaskPageOut"];
export type QueueTaskState = components["schemas"]["QueueTaskState"];
export type QueueTaskFilter = QueueTaskState | "all";
export type QueueTaskAction = "pause" | "resume" | "cancel";

export const QUEUE_TASKS_PAGE_SIZE = 50;

export const QUEUE_TASK_STATES = [
  "queued",
  "paused",
  "running",
  "succeeded",
  "failed",
  "poll_timeout",
  "canceled",
] as const satisfies readonly QueueTaskState[];

export const QUEUE_TERMINAL_TASK_STATES: readonly QueueTaskState[] = [
  "succeeded",
  "failed",
  "poll_timeout",
  "canceled",
];

export const QUEUE_TASK_STATE_LABELS: Record<QueueTaskState, string> = {
  queued: "Queued",
  paused: "Paused",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  poll_timeout: "Timed out",
  canceled: "Canceled",
};

export const QUEUE_TASK_STATE_TONES: Record<QueueTaskState, string> = {
  queued: "border border-primary/30 bg-primary/10 text-primary",
  paused: "border border-border bg-secondary text-secondary-foreground",
  running: "border border-warning/30 bg-warning/10 text-warning",
  succeeded: "border border-success/30 bg-success/10 text-success",
  failed: "border border-destructive/30 bg-destructive/10 text-destructive",
  poll_timeout: "border border-warning/30 bg-warning/10 text-warning",
  canceled: "border border-border bg-muted text-muted-foreground",
};

const UNKNOWN_STATE_TONE = "border border-border bg-muted text-muted-foreground";

/**
 * Label for a task state. A newer backend may add a state before the client is regenerated;
 * show the raw value rather than an empty badge.
 */
export function queueTaskStateLabel(state: QueueTaskState): string {
  return Object.prototype.hasOwnProperty.call(QUEUE_TASK_STATE_LABELS, state)
    ? QUEUE_TASK_STATE_LABELS[state]
    : `Unknown state (${String(state) || "empty"})`;
}

export function queueTaskStateTone(state: QueueTaskState): string {
  return Object.prototype.hasOwnProperty.call(QUEUE_TASK_STATE_TONES, state)
    ? QUEUE_TASK_STATE_TONES[state]
    : UNKNOWN_STATE_TONE;
}

export const QUEUE_POLL_WINDOW_MS = 30 * 60 * 1_000;

export function isQueueTaskActive(state: QueueTaskState): boolean {
  return state === "queued" || state === "running";
}

export function getQueuePollIntervalMs(elapsedMs: number): number | false {
  if (elapsedMs >= QUEUE_POLL_WINDOW_MS) return false;
  if (elapsedMs < 60_000) return 2_000;
  if (elapsedMs < 5 * 60_000) return 5_000;
  return 10_000;
}

/** Slowest backoff step; used while a retryable refresh error is being ridden out. */
export const QUEUE_ERROR_POLL_INTERVAL_MS = 10_000;

export type QueueQueryStatus = "pending" | "error" | "success";

/**
 * Poll while at least one task can still progress, with a bounded 30-minute window.
 * Paused tasks do not keep polling; terminal tasks never keep polling by themselves.
 * After a retryable error (network failure, timeout, 429, transient 5xx) polling continues at
 * the slowest interval so a brief outage does not freeze the monitor; a non-retryable error
 * (auth, validation, not found) stops polling until the user refreshes or retries.
 */
export function getQueueRefetchInterval(
  states: readonly QueueTaskState[] | undefined,
  elapsedMs: number,
  queryStatus: QueueQueryStatus = states === undefined ? "pending" : "success",
  projectActiveCount?: number,
  errorIsRetryable = false,
): number | false {
  if (queryStatus === "error") {
    if (!errorIsRetryable) return false;
    const interval = getQueueRefetchInterval(
      states,
      elapsedMs,
      states === undefined ? "pending" : "success",
      projectActiveCount,
    );
    return interval === false ? false : Math.max(interval, QUEUE_ERROR_POLL_INTERVAL_MS);
  }

  const interval = getQueuePollIntervalMs(elapsedMs);
  if (interval === false) return false;
  if (projectActiveCount !== undefined) return projectActiveCount > 0 ? interval : false;
  if (states && !states.some(isQueueTaskActive)) return false;
  return interval;
}

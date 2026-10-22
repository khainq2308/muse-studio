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

/**
 * Poll while at least one task can still progress, with a bounded 30-minute window.
 * Paused tasks do not keep polling; terminal tasks never keep polling by themselves.
 */
export type QueueQueryStatus = "pending" | "error" | "success";

export function getQueueRefetchInterval(
  states: readonly QueueTaskState[] | undefined,
  elapsedMs: number,
  queryStatus: QueueQueryStatus = states === undefined ? "pending" : "success",
  projectActiveCount?: number,
): number | false {
  if (queryStatus === "error") return false;

  const interval = getQueuePollIntervalMs(elapsedMs);
  if (interval === false) return false;
  if (projectActiveCount !== undefined) return projectActiveCount > 0 ? interval : false;
  if (states && !states.some(isQueueTaskActive)) return false;
  return interval;
}

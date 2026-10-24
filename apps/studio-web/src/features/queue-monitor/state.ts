import type { QueueTaskState } from "@/entities/task/state";
import type { components } from "@/lib/api/schema";

export {
  QUEUE_ERROR_POLL_INTERVAL_MS,
  QUEUE_POLL_WINDOW_MS,
  QUEUE_TASK_STATE_LABELS,
  QUEUE_TASK_STATE_TONES,
  QUEUE_TERMINAL_TASK_STATES,
  getQueuePollIntervalMs,
  getQueueRefetchInterval,
  isQueueTaskActive,
  queueTaskStateLabel,
  queueTaskStateTone,
  type QueueQueryStatus,
  type QueueTask,
  type QueueTaskPage,
  type QueueTaskState,
} from "@/entities/task/state";
export type QueueTaskFilter = QueueTaskState | "all";
export type QueueTaskAction = "pause" | "resume" | "cancel";
export type QueuePendingInput = components["schemas"]["PendingInputOut"];
export type QueuePendingInputKind = QueuePendingInput["kind"];
export type QueueTaskReply = components["schemas"]["TaskReplyIn"];

export const QUEUE_TASKS_PAGE_SIZE = 50;

export const QUEUE_TASK_STATES = [
  "queued",
  "paused",
  "running",
  "awaiting_input",
  "succeeded",
  "failed",
  "poll_timeout",
  "canceled",
] as const satisfies readonly QueueTaskState[];

/** Backend `TaskReplyIn` length limits (`ANSWER_TEXT_MAX_CHARS` / `ANSWER_CHOICE_MAX_CHARS`). */
export const QUEUE_REPLY_TEXT_MAX_CHARS = 4_000;
export const QUEUE_REPLY_CHOICE_MAX_CHARS = 200;

export const QUEUE_PENDING_INPUT_KIND_LABELS: Record<QueuePendingInputKind, string> = {
  question: "Question",
  approval: "Approval requested",
  stalled: "Generation stalled",
};

export function queuePendingInputKindLabel(kind: QueuePendingInputKind): string {
  return Object.prototype.hasOwnProperty.call(QUEUE_PENDING_INPUT_KIND_LABELS, kind)
    ? QUEUE_PENDING_INPUT_KIND_LABELS[kind]
    : "Question";
}

/**
 * A free-text answer is offered for questions and stalls, and for any prompt without options.
 * An approval with options is answered by choosing one of them.
 */
export function pendingInputAcceptsText(pendingInput: QueuePendingInput): boolean {
  return pendingInput.kind !== "approval" || pendingInput.options.length === 0;
}

/** Options the backend can accept as a `choice` (1–200 characters). */
export function replyableOptions(pendingInput: QueuePendingInput): string[] {
  return pendingInput.options.filter(
    (option) => option.length >= 1 && option.length <= QUEUE_REPLY_CHOICE_MAX_CHARS,
  );
}

/** `asked_at` is a Unix timestamp in seconds; null or invalid values have no display time. */
export function pendingInputAskedAt(askedAt: number | null): Date | null {
  if (askedAt === null || !Number.isFinite(askedAt) || askedAt <= 0) return null;
  const date = new Date(askedAt * 1_000);
  return Number.isNaN(date.getTime()) ? null : date;
}

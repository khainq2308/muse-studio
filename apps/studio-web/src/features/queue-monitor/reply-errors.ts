import { ApiError } from "@/lib/api/client";

/** Operator-facing message for a failed `POST /api/v1/queue/tasks/{task_id}/reply`. */
export function queueReplyErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return "This question is no longer waiting for an answer. The queue was refreshed to show the task's current state.";
    }
    if (error.status === 404) {
      return "This task no longer exists. The queue was refreshed.";
    }
    if (error.status === 422) {
      return `The answer was rejected: ${error.message}`;
    }
    if (error.status === 401 || error.status === 403) {
      return "The admin token was rejected while sending the answer. Update the token and try again.";
    }
    return `Could not send the answer: ${error.message}`;
  }
  return error instanceof Error
    ? `Could not send the answer: ${error.message}`
    : "Could not send the answer.";
}

import { ApiError } from "@/lib/api/client";

export type AuthApiError = ApiError & { readonly status: 401 | 403 };

const NON_RETRYABLE_HTTP_STATUSES = new Set([400, 401, 403, 404, 422]);
/** Gateway/availability failures that are usually transient even without envelope metadata. */
const TRANSIENT_SERVER_STATUSES = new Set([502, 503, 504]);
const MAX_RETRIES = 1;
const MAX_RETRY_DELAY_MS = 30_000;

export function isAuthError(error: unknown): error is AuthApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export function authErrorMessage(
  error: unknown,
  activeAdminToken: string,
  resource: string,
): string {
  if (!isAuthError(error)) {
    if (error instanceof ApiError && error.category === "timeout") {
      return `Loading ${resource} timed out. Check the Orchestrator and retry.`;
    }
    return `Could not load ${resource}. Check the Orchestrator and retry.`;
  }

  if (error.status === 403) {
    return `The current session does not have permission to read ${resource}. Connect with an admin token that has the required permission.`;
  }

  if (!activeAdminToken.trim()) return `Admin token required to read ${resource}.`;
  return `The configured admin token was rejected while reading ${resource}. Update the token and reconnect.`;
}

/**
 * Message for a failed write action (create, update, delete, run). Unlike {@link authErrorMessage},
 * it surfaces the server's validation detail so a 422 explains which input was rejected.
 */
export function actionErrorMessage(
  error: unknown,
  activeAdminToken: string,
  action: string,
): string {
  if (isAuthError(error)) {
    if (error.status === 403) {
      return `The current session does not have permission to ${action}.`;
    }
    if (!activeAdminToken.trim()) return `Admin token required to ${action}.`;
    return `The configured admin token was rejected while trying to ${action}. Update the token and reconnect.`;
  }
  if (error instanceof ApiError) {
    if (error.category === "timeout") {
      return `Could not confirm whether the request to ${action} finished (timed out). Refresh before retrying.`;
    }
    return `Could not ${action}: ${error.message}`;
  }
  return error instanceof Error ? `Could not ${action}: ${error.message}` : `Could not ${action}.`;
}

/**
 * Whether a failed request may succeed if repeated unchanged: network failures, timeouts,
 * rate limits and transient gateway errors. Auth, validation and not-found errors are final.
 */
export function isRetryableApiError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  if (NON_RETRYABLE_HTTP_STATUSES.has(error.status)) return false;
  if (error.retryable !== undefined) return error.retryable;
  if (error.status === 429) return true;
  if (error.status >= 500) return TRANSIENT_SERVER_STATUSES.has(error.status);
  return true;
}

export function shouldRetryApiRequest(failureCount: number, error: unknown): boolean {
  if (failureCount >= MAX_RETRIES) return false;
  return isRetryableApiError(error);
}

/** TanStack Query retryDelay: honor Retry-After (bounded), else exponential backoff. */
export function apiRetryDelay(failureCount: number, error: unknown): number {
  if (error instanceof ApiError && error.retryAfterMs !== undefined) {
    return Math.min(Math.max(0, error.retryAfterMs), MAX_RETRY_DELAY_MS);
  }
  return Math.min(1000 * 2 ** failureCount, MAX_RETRY_DELAY_MS);
}

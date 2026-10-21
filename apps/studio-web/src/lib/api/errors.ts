import { ApiError } from "@/lib/api/client";

export type AuthApiError = ApiError & { readonly status: 401 | 403 };

const NON_RETRYABLE_HTTP_STATUSES = new Set([400, 401, 403, 404, 422]);

export function isAuthError(error: unknown): error is AuthApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export function authErrorMessage(error: unknown, activeAdminToken: string, resource: string): string {
  if (!isAuthError(error)) {
    return `Could not load ${resource}. Check the Orchestrator and retry.`;
  }

  if (error.status === 403) {
    return `The current session does not have permission to read ${resource}. Connect with an admin token that has the required permission.`;
  }

  if (!activeAdminToken.trim()) return `Admin token required to read ${resource}.`;
  return `The configured admin token was rejected while reading ${resource}. Update the token and reconnect.`;
}

export function shouldRetryApiRequest(failureCount: number, error: unknown): boolean {
  if (
    error instanceof ApiError &&
    (error.retryable === false || NON_RETRYABLE_HTTP_STATUSES.has(error.status))
  ) {
    return false;
  }

  return failureCount < 1;
}

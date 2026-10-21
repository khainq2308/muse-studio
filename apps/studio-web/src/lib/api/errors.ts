import { ApiError } from "@/lib/api/client";

export function isAuthError(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export function authErrorMessage(
  error: unknown,
  activeAdminToken: string,
  resource: string,
): string {
  if (error instanceof ApiError && error.status === 403) {
    return `The current session does not have permission to read ${resource}. Connect with an admin token that has the required permission.`;
  }

  if (error instanceof ApiError && error.status === 401) {
    if (!activeAdminToken.trim()) return `Admin token required to read ${resource}.`;
    return `The configured admin token was rejected while reading ${resource}. Update the token and reconnect.`;
  }

  return `Could not load ${resource}. Check the Orchestrator and retry.`;
}

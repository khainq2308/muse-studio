import { useQuery } from "@tanstack/react-query";
import { ApiError, apiGet } from "../../lib/api/client";
import { queryKeys } from "../../lib/api/queryKeys";
import { useSessionStore } from "../../stores/session-store";

/**
 * Transitional summary used by the legacy dashboard.
 * Phase 0b will replace API-bound DTOs with generated OpenAPI types.
 */
export interface ProjectSummary {
  id: string;
  name: string;
  aspect_ratio: string;
  fps: number;
  style_preset: string;
  current_revision_num: number;
  asset_count: number;
}

function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && [400, 401, 403, 404, 422].includes(error.status)) return false;
  return failureCount < 1;
}

export function useProjects() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projects(sessionVersion),
    queryFn: ({ signal }) => apiGet<ProjectSummary[]>("/api/v1/projects", { signal }),
    retry: shouldRetry,
  });
}

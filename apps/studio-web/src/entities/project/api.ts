import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { useSessionStore } from "@/lib/stores/session-store";

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

export function useProjects() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projects(sessionVersion),
    queryFn: ({ signal }) => apiGet<ProjectSummary[]>("/api/v1/projects", { signal }),
  });
}

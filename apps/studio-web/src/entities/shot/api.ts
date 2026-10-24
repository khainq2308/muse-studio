import { queryOptions, useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

export type ProjectShot = components["schemas"]["ProjectShotOut"];

/**
 * Project shot list (`GET /api/v1/projects/{project_id}/shots`). Shared by Script Studio, the
 * Shot Board and the workspace overview, so every consumer reads and invalidates one cache entry.
 */
export function projectShotsQueryOptions(sessionVersion: number, projectId: string) {
  return queryOptions({
    queryKey: queryKeys.projectShots(sessionVersion, projectId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/shots", {
        signal,
        pathParams: { project_id: projectId },
      }),
  });
}

export function useProjectShots(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery(projectShotsQueryOptions(sessionVersion, projectId));
}

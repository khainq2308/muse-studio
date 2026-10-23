import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

export type ProjectRevision = components["schemas"]["RevisionOut"];

/** Revision history of one project (`GET /api/v1/projects/{project_id}/revisions`). */
export function useProjectRevisions(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projectRevisions(sessionVersion, projectId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/revisions", {
        signal,
        pathParams: { project_id: projectId },
      }),
    enabled: Boolean(projectId),
  });
}

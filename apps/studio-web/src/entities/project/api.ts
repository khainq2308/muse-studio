import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { useSessionStore } from "@/lib/stores/session-store";

export function useProjects() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projects(sessionVersion),
    queryFn: ({ signal }) => apiGet("/api/v1/projects", { signal }),
  });
}

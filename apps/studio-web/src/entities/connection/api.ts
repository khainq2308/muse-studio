import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { useSessionStore } from "@/lib/stores/session-store";

export function useConnections() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.connections(sessionVersion),
    queryFn: ({ signal }) => apiGet("/api/v1/connections", { signal }),
  });
}

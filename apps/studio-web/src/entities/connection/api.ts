import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { useSessionStore } from "@/lib/stores/session-store";

/**
 * Transitional summary used by the legacy dashboard.
 * Phase 0b will replace API-bound DTOs with generated OpenAPI types.
 */
export interface ConnectionSummary {
  id: string;
  name: string;
  provider_type: string;
  base_url: string;
  health_state: string;
  latency_ms: number | null;
}

export function useConnections() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.connections(sessionVersion),
    queryFn: ({ signal }) => apiGet<ConnectionSummary[]>("/api/v1/connections", { signal }),
  });
}

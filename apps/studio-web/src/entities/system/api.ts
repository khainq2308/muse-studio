import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";

export interface HealthResponse {
  status: string;
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => apiGet<HealthResponse>("/healthz", { signal }),
    refetchInterval: 30_000,
    retry: 1,
  });
}

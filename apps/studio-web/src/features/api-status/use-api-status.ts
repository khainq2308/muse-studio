import { useHealth } from "@/entities/system/api";
import { resolveApiStatus } from "@/features/api-status/status";

export function useApiStatus() {
  const healthQuery = useHealth();
  return resolveApiStatus({
    isPending: healthQuery.isPending,
    isError: healthQuery.isError,
    healthStatus: healthQuery.data?.status,
  });
}

import { useQuery } from "@tanstack/react-query";
import { ApiError, apiGetBlob } from "@/lib/api/client";
import { shouldRetryApiRequest } from "@/lib/api/errors";
import { queryKeys } from "@/lib/api/queryKeys";
import { useSessionStore } from "@/lib/stores/session-store";

/** Media failures that repeating unchanged cannot fix, or that would re-download a large body. */
export function retryAssetMedia(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError) {
    if (error.category === "timeout" || error.category === "too_large") return false;
    if (error.status === 404) return false;
  }
  return shouldRetryApiRequest(failureCount, error);
}

/**
 * Bytes of one asset for an on-demand preview (`GET /api/v1/assets/{asset_id}/download`).
 * Asset IDs are immutable, so the Blob never goes stale. `gcTime` controls how long the
 * Blob stays in memory after the last viewer unmounts: video previews use 0 so closing a
 * preview releases it immediately. Timeouts, over-limit bodies and missing (404) assets
 * are not retried automatically; the operator can retry explicitly.
 */
export function useAssetMedia(
  assetId: string | null,
  maxBytes: number,
  options: { enabled?: boolean; gcTime?: number } = {},
) {
  const { enabled = true, gcTime = 30_000 } = options;
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.assetMedia(sessionVersion, assetId ?? "", maxBytes),
    queryFn: ({ signal }) =>
      apiGetBlob("/api/v1/assets/{asset_id}/download", {
        signal,
        maxBytes,
        pathParams: { asset_id: assetId ?? "" },
      }),
    enabled: enabled && Boolean(assetId),
    staleTime: Infinity,
    gcTime,
    retry: retryAssetMedia,
  });
}

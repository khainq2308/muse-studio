import type { QueryClient } from "@tanstack/react-query";

interface SessionVersionStore {
  subscribe: (
    listener: (
      state: { sessionVersion: number },
      previousState: { sessionVersion: number },
    ) => void,
  ) => () => void;
}

/**
 * Remove session-scoped query data whenever the session version changes.
 * Health/status queries under the "system" key are not session-scoped and are kept.
 * In-flight fetches are cancelled first so a late response cannot repopulate the cache.
 */
export function registerSessionCacheReset(
  queryClient: QueryClient,
  store: SessionVersionStore,
): () => void {
  return store.subscribe((state, previousState) => {
    if (state.sessionVersion === previousState.sessionVersion) return;
    const filters = {
      predicate: (query: { queryKey: readonly unknown[] }) => query.queryKey[0] !== "system",
    };
    void queryClient.cancelQueries(filters);
    queryClient.removeQueries(filters);
  });
}

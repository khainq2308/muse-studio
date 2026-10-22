import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";
import { create } from "zustand";
import { registerSessionCacheReset } from "@/lib/api/session-cache";

function makeStore() {
  return create<{ sessionVersion: number; bump: () => void }>((set) => ({
    sessionVersion: 0,
    bump: () => set((state) => ({ sessionVersion: state.sessionVersion + 1 })),
  }));
}

describe("registerSessionCacheReset", () => {
  let queryClient: QueryClient;
  afterEach(() => queryClient.clear());

  it("removes session-scoped queries on session change and keeps system queries", () => {
    queryClient = new QueryClient();
    const store = makeStore();
    registerSessionCacheReset(queryClient, store);
    queryClient.setQueryData(["system", "health"], { status: "ok" });
    queryClient.setQueryData(["queue", 0, "project-a"], { total: 1 });

    store.setState({ sessionVersion: 0 });
    expect(queryClient.getQueryData(["queue", 0, "project-a"])).toEqual({ total: 1 });

    store.getState().bump();
    expect(queryClient.getQueryData(["queue", 0, "project-a"])).toBeUndefined();
    expect(queryClient.getQueryData(["system", "health"])).toEqual({ status: "ok" });
  });

  it("does not let an in-flight fetch repopulate the cache after a session bump", async () => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = makeStore();
    registerSessionCacheReset(queryClient, store);

    let resolveFetch: (value: unknown) => void = () => undefined;
    const pending = queryClient
      .fetchQuery({
        queryKey: ["projects"],
        queryFn: () =>
          new Promise((resolve) => {
            resolveFetch = resolve;
          }),
      })
      .catch(() => undefined);

    store.getState().bump();
    resolveFetch({ secret: "previous-session" });
    await pending;

    expect(queryClient.getQueryData(["projects"])).toBeUndefined();
    expect(queryClient.getQueryCache().find({ queryKey: ["projects"] })).toBeUndefined();
  });
});

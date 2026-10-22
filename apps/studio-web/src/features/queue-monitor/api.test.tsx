import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QUEUE_POLL_WINDOW_MS, type QueueTaskFilter, type QueueTaskState } from "@/features/queue-monitor/state";

const mocks = vi.hoisted(() => ({
  useQuery: vi.fn(),
  useMutation: vi.fn(),
  useQueryClient: vi.fn(),
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  invalidateQueries: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: mocks.useQuery,
  useMutation: mocks.useMutation,
  useQueryClient: mocks.useQueryClient,
}));

vi.mock("@/lib/api/client", () => ({
  apiGet: mocks.apiGet,
  apiPost: mocks.apiPost,
}));

import { useQueueTaskAction, useQueueTasks } from "@/features/queue-monitor/api";

type QueryOptionsShape = {
  refetchInterval: (query: {
    state: {
      data?: Array<{ state: QueueTaskState }>;
      status: "pending" | "error" | "success";
    };
  }) => number | false;
};

type MutationOptionsShape = {
  onSuccess?: (...args: never[]) => unknown;
};

describe("Queue Monitor query hooks", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("stops polling on errors and resets the polling window after elapsed time or key changes", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-22T00:00:00.000Z"));

    let options: QueryOptionsShape | undefined;
    mocks.useQuery.mockImplementation((value: unknown) => {
      options = value as QueryOptionsShape;
      return {} as never;
    });

    const initialProps: { projectId: string; stateFilter: QueueTaskFilter } = {
      projectId: "project-1",
      stateFilter: "all",
    };
    const { result, rerender } = renderHook(
      ({ projectId, stateFilter }) => useQueueTasks(projectId, stateFilter),
      { initialProps },
    );

    const runningQuery = {
      state: { data: [{ state: "running" as const }], status: "success" as const },
    };
    const failedQuery = {
      state: { data: undefined, status: "error" as const },
    };

    expect(options?.refetchInterval(runningQuery)).toBe(2_000);
    expect(options?.refetchInterval(failedQuery)).toBe(false);

    vi.setSystemTime(new Date(Date.now() + QUEUE_POLL_WINDOW_MS + 1));
    expect(options?.refetchInterval(runningQuery)).toBe(false);

    act(() => result.current.resetPollingWindow());
    expect(options?.refetchInterval(runningQuery)).toBe(2_000);

    vi.setSystemTime(new Date(Date.now() + QUEUE_POLL_WINDOW_MS + 1));
    rerender({ projectId: "project-2", stateFilter: "all" });
    expect(options?.refetchInterval(runningQuery)).toBe(2_000);

    vi.setSystemTime(new Date(Date.now() + QUEUE_POLL_WINDOW_MS + 1));
    rerender({ projectId: "project-2", stateFilter: "failed" });
    expect(options?.refetchInterval(runningQuery)).toBe(2_000);
  });

  it("restarts polling after a successful queue action mutation", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-22T00:00:00.000Z"));

    mocks.useQueryClient.mockReturnValue({ invalidateQueries: mocks.invalidateQueries });
    let mutationOptions: MutationOptionsShape | undefined;
    mocks.useMutation.mockImplementation((value: unknown) => {
      mutationOptions = value as MutationOptionsShape;
      return {} as never;
    });

    const resetWindow = vi.fn();
    renderHook(() => useQueueTaskAction("project-1", resetWindow));

    expect(mutationOptions?.onSuccess).toBeTypeOf("function");
    await act(async () => {
      await mutationOptions?.onSuccess?.();
    });
    expect(resetWindow).toHaveBeenCalledTimes(1);
    expect(mocks.invalidateQueries).toHaveBeenCalledTimes(1);
  });
});

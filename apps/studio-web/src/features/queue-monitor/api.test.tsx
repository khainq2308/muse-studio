import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  QUEUE_POLL_WINDOW_MS,
  QUEUE_TASKS_PAGE_SIZE,
  type QueueTaskFilter,
  type QueueTaskState,
} from "@/features/queue-monitor/state";

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
  queryKey: unknown[];
  queryFn: (context: { signal: AbortSignal }) => unknown;
  refetchInterval: (query: {
    state: {
      data?: {
        items: Array<{ state: QueueTaskState }>;
        summary: { active_tasks: number };
      };
      status: "pending" | "error" | "success";
    };
  }) => number | false;
};

type MutationOptionsShape = {
  onSettled?: (...args: never[]) => unknown;
};

describe("Queue Monitor query hooks", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("stops polling on errors, resets the window, and requests bounded pages", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-22T00:00:00.000Z"));

    let options: QueryOptionsShape | undefined;
    mocks.useQuery.mockImplementation((value: unknown) => {
      options = value as QueryOptionsShape;
      return {} as never;
    });
    mocks.apiGet.mockResolvedValue({
      items: [],
      total: 0,
      limit: QUEUE_TASKS_PAGE_SIZE,
      offset: 100,
      has_more: false,
      summary: { total_tasks: 0, active_tasks: 0, failed_tasks: 0 },
    });

    const initialProps: { projectId: string; stateFilter: QueueTaskFilter; offset: number } = {
      projectId: "project-1",
      stateFilter: "all",
      offset: 100,
    };
    const { result, rerender } = renderHook(
      ({ projectId, stateFilter, offset }) => useQueueTasks(projectId, stateFilter, offset),
      { initialProps },
    );

    const runningQuery = {
      state: {
        data: { items: [{ state: "running" as const }], summary: { active_tasks: 2 } },
        status: "success" as const,
      },
    };
    const failedQuery = {
      state: { data: undefined, status: "error" as const },
    };

    expect(options?.refetchInterval(runningQuery)).toBe(2_000);
    expect(options?.refetchInterval(failedQuery)).toBe(false);
    expect(options?.queryKey).toContain(100);

    await act(async () => {
      await options?.queryFn({ signal: new AbortController().signal });
    });
    expect(mocks.apiGet).toHaveBeenCalledWith(
      "/api/v1/queue/tasks",
      expect.objectContaining({
        query: {
          project_id: "project-1",
          limit: QUEUE_TASKS_PAGE_SIZE,
          offset: 100,
        },
      }),
    );

    vi.setSystemTime(new Date(Date.now() + QUEUE_POLL_WINDOW_MS + 1));
    expect(options?.refetchInterval(runningQuery)).toBe(false);

    act(() => result.current.resetPollingWindow());
    expect(options?.refetchInterval(runningQuery)).toBe(2_000);

    vi.setSystemTime(new Date(Date.now() + QUEUE_POLL_WINDOW_MS + 1));
    rerender({ projectId: "project-2", stateFilter: "all", offset: 0 });
    expect(options?.refetchInterval(runningQuery)).toBe(2_000);

    vi.setSystemTime(new Date(Date.now() + QUEUE_POLL_WINDOW_MS + 1));
    rerender({ projectId: "project-2", stateFilter: "failed", offset: 0 });
    expect(options?.refetchInterval(runningQuery)).toBe(2_000);

    vi.setSystemTime(new Date(Date.now() + QUEUE_POLL_WINDOW_MS + 1));
    rerender({ projectId: "project-2", stateFilter: "running", offset: 0 });
    expect(
      options?.refetchInterval({
        state: {
          data: { items: [{ state: "succeeded" }], summary: { active_tasks: 3 } },
          status: "success",
        },
      }),
    ).toBe(2_000);
  });

  it("invalidates and restarts polling after every queue action result", async () => {
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

    expect(mutationOptions?.onSettled).toBeTypeOf("function");
    await act(async () => {
      await mutationOptions?.onSettled?.();
    });
    expect(resetWindow).toHaveBeenCalledTimes(1);
    expect(mocks.invalidateQueries).toHaveBeenCalledTimes(1);
  });
});

import * as ReactQuery from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
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
}));

vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-query")>()),
  useQuery: mocks.useQuery,
  useMutation: mocks.useMutation,
  useQueryClient: mocks.useQueryClient,
}));

vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  apiGet: mocks.apiGet,
  apiPost: mocks.apiPost,
}));

import { useQueueTaskAction, useQueueTasks } from "@/features/queue-monitor/api";
import { ApiError } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { useSessionStore } from "@/lib/stores/session-store";

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
      error?: unknown;
    };
  }) => number | false;
};

describe("Queue Monitor query hooks", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("polls slowly on retryable errors, stops on final errors, and requests bounded pages", async () => {
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
    const unauthorizedQuery = {
      state: {
        data: undefined,
        status: "error" as const,
        error: new ApiError({ status: 401, detail: "Unauthorized" }),
      },
    };
    const timedOutRefreshQuery = {
      state: {
        ...runningQuery.state,
        status: "error" as const,
        error: new ApiError({ status: 0, detail: "timeout", category: "timeout", retryable: true }),
      },
    };

    expect(options?.refetchInterval(runningQuery)).toBe(2_000);
    expect(options?.refetchInterval(unauthorizedQuery)).toBe(false);
    expect(options?.refetchInterval(timedOutRefreshQuery)).toBe(10_000);
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

  it("invalidates the project the action started on, even after switching projects", async () => {
    const actual = await vi.importActual<typeof ReactQuery>("@tanstack/react-query");
    mocks.useMutation.mockImplementation(actual.useMutation);
    mocks.useQueryClient.mockImplementation(actual.useQueryClient);

    const queryClient = new actual.QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const sessionVersion = useSessionStore.getState().sessionVersion;
    const keyA = queryKeys.queueTasks(sessionVersion, "project-a", "all", 0);
    const keyB = queryKeys.queueTasks(sessionVersion, "project-b", "all", 0);
    queryClient.setQueryData(keyA, { total: 1 });
    queryClient.setQueryData(keyB, { total: 1 });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    let resolvePost: (value: unknown) => void = () => undefined;
    mocks.apiPost.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = resolve;
      }),
    );

    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(actual.QueryClientProvider, { client: queryClient }, children);
    const resetWindow = vi.fn();
    const { result, rerender } = renderHook(
      ({ projectId }) => useQueueTaskAction(projectId, resetWindow),
      { wrapper, initialProps: { projectId: "project-a" } },
    );

    act(() => result.current.mutate({ taskId: "task-1", action: "pause" }));
    rerender({ projectId: "project-b" });
    await act(async () => {
      resolvePost({});
    });

    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledTimes(1));
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.queueTasksPrefix(sessionVersion, "project-a"),
    });
    expect(resetWindow).toHaveBeenCalledTimes(1);
    expect(mocks.apiPost).toHaveBeenCalledWith("/api/v1/queue/tasks/{task_id}/pause", undefined, {
      pathParams: { task_id: "task-1" },
    });
    expect(queryClient.getQueryState(keyA)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(keyB)?.isInvalidated).toBe(false);
  });

  it("tracks every concurrently pending task action, not just the latest one", async () => {
    const actual = await vi.importActual<typeof ReactQuery>("@tanstack/react-query");
    mocks.useMutation.mockImplementation(actual.useMutation);
    mocks.useQueryClient.mockImplementation(actual.useQueryClient);
    const queryClient = new actual.QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const resolvers = new Map<string, (value: unknown) => void>();
    mocks.apiPost.mockImplementation(
      (_path: string, _body: unknown, options: { pathParams: { task_id: string } }) =>
        new Promise((resolve) => resolvers.set(options.pathParams.task_id, resolve)),
    );
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(actual.QueryClientProvider, { client: queryClient }, children);
    const { result } = renderHook(() => useQueueTaskAction("project-a"), { wrapper });

    act(() => result.current.mutate({ taskId: "task-a", action: "pause" }));
    act(() => result.current.mutate({ taskId: "task-b", action: "cancel" }));
    await waitFor(() =>
      expect([...result.current.pendingTaskIds].sort()).toEqual(["task-a", "task-b"]),
    );

    await act(async () => resolvers.get("task-b")?.({}));
    await waitFor(() => expect([...result.current.pendingTaskIds]).toEqual(["task-a"]));
    await act(async () => resolvers.get("task-a")?.({}));
    await waitFor(() => expect(result.current.pendingTaskIds.size).toBe(0));
  });
});

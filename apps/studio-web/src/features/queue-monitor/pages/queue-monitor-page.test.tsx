import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QueueTask, QueueTaskFilter, QueueTaskPage } from "@/features/queue-monitor/state";

const mocks = vi.hoisted(() => ({
  useQueueTasks: vi.fn(),
  useQueueTaskAction: vi.fn(),
  useQueueTaskReply: vi.fn(),
}));

vi.mock("@/features/queue-monitor/api", () => ({
  useQueueTasks: mocks.useQueueTasks,
  useQueueTaskAction: mocks.useQueueTaskAction,
  useQueueTaskReply: mocks.useQueueTaskReply,
}));

import { QueueMonitorPage } from "@/features/queue-monitor/pages/queue-monitor-page";
import { ApiError } from "@/lib/api/client";
import { useSessionStore } from "@/lib/stores/session-store";

function makeTask(): QueueTask {
  return {
    account_used: null,
    attempt_count: 1,
    connection_id: "connection-1",
    error_category: null,
    error_message_redacted: null,
    external_task_id: null,
    history_log: ["Task accepted."],
    id: "task-running-1",
    idempotency_key: "idempotency-1",
    max_attempts: 3,
    max_polls: 60,
    model_id: "muse-video-mock-v1",
    next_poll_delay_seconds: 2,
    poll_count: 2,
    priority: 10,
    project_id: "project-1",
    result_asset_id: null,
    retryable: true,
    scene_id: null,
    shot_id: "shot-1",
    state: "running",
    take_id: null,
    task_type: "video",
  };
}

function makePage(overrides: Partial<QueueTaskPage> = {}): QueueTaskPage {
  return {
    items: [makeTask()],
    total: 1,
    limit: 50,
    offset: 0,
    has_more: false,
    summary: { total_tasks: 1, active_tasks: 1, failed_tasks: 0 },
    ...overrides,
  };
}

function setup(
  overrides: {
    query?: Record<string, unknown>;
    mutation?: Record<string, unknown>;
  } = {},
) {
  const query = {
    data: makePage(),
    isPending: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn().mockResolvedValue(undefined),
    resetPollingWindow: vi.fn(),
    ...overrides.query,
  };
  const mutation = {
    isPending: false,
    variables: undefined,
    error: null,
    mutate: vi.fn(),
    reset: vi.fn(),
    pendingTaskIds: new Set<string>(),
    ...overrides.mutation,
  };

  mocks.useQueueTasks.mockReturnValue(query);
  mocks.useQueueTaskAction.mockReturnValue(mutation);
  return { query, mutation };
}

afterEach(() => cleanup());

function makeReplyMutation(overrides: Record<string, unknown> = {}) {
  return {
    isPending: false,
    error: null,
    mutate: vi.fn(),
    reset: vi.fn(),
    pendingTaskIds: new Set<string>(),
    ...overrides,
  };
}

function makeAwaitingTask(id = "task-awaiting-1"): QueueTask {
  return {
    ...makeTask(),
    id,
    state: "awaiting_input",
    pending_input: {
      kind: "question",
      text: "Which style should the shot use?",
      options: ["Cinematic", "Anime"],
      asked_at: 1761200000.5,
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.useQueueTaskReply.mockReturnValue(makeReplyMutation());
});

describe("QueueMonitorPage", () => {
  it("shows project-wide summary counts and exposes the summary group", () => {
    setup({
      query: {
        data: makePage({
          total: 65,
          has_more: true,
          summary: { total_tasks: 80, active_tasks: 12, failed_tasks: 4 },
        }),
      },
    });
    render(<QueueMonitorPage projectId="project-1" />);

    expect(screen.getByRole("group", { name: "Queue summary" })).toBeTruthy();
    expect(screen.getByText("Tasks in project")).toBeTruthy();
    expect(screen.getByText("Active (queued, running, awaiting input)")).toBeTruthy();
    expect(screen.getByText("Failed / timed out")).toBeTruthy();
    expect(screen.getByText("80")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
  });

  it("moves to the next bounded page and resets a prior action error", () => {
    const { mutation } = setup({
      query: { data: makePage({ total: 65, has_more: true }) },
      mutation: { error: new Error("write failed") },
    });
    mutation.reset.mockImplementation(() => {
      mutation.error = null;
    });
    render(<QueueMonitorPage projectId="project-1" />);

    expect(screen.getByRole("alert").textContent).toContain("write failed");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    expect(mutation.reset).toHaveBeenCalledTimes(1);
    expect(mocks.useQueueTasks).toHaveBeenLastCalledWith("project-1", "all", 50);
  });

  it("returns to a valid page when the filtered result set shrinks", async () => {
    const query = {
      data: makePage({ total: 65, has_more: true }),
      isPending: false,
      isError: false,
      isFetching: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
      resetPollingWindow: vi.fn(),
    };
    const mutation = {
      isPending: false,
      variables: undefined,
      error: null,
      mutate: vi.fn(),
      reset: vi.fn(),
      pendingTaskIds: new Set<string>(),
    };
    let resultSetShrank = false;
    mocks.useQueueTasks.mockImplementation(
      (_projectId: string, _filter: QueueTaskFilter, offset: number) => {
        if (offset >= 50) resultSetShrank = true;
        if (!resultSetShrank) return query;
        return {
          ...query,
          data:
            offset === 0
              ? makePage({ total: 20, has_more: false })
              : makePage({ items: [], total: 20, offset, has_more: false }),
        };
      },
    );
    mocks.useQueueTaskAction.mockReturnValue(mutation);
    render(<QueueMonitorPage projectId="project-1" />);

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() => {
      expect(mocks.useQueueTasks).toHaveBeenLastCalledWith("project-1", "all", 0);
    });
    expect(screen.queryByText(/Showing 51–50 of 20 matching tasks/)).toBeNull();
  });

  it("clears a prior action error when the session changes", () => {
    const { mutation } = setup({ mutation: { error: new Error("write failed") } });
    mutation.reset.mockImplementation(() => {
      mutation.error = null;
    });
    const { rerender } = render(<QueueMonitorPage projectId="project-1" />);
    expect(mutation.reset).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("write failed");

    act(() => useSessionStore.getState().setAdminToken("session-change-" + Date.now()));
    expect(mutation.reset).toHaveBeenCalledTimes(1);
    rerender(<QueueMonitorPage projectId="project-1" />);
    expect(screen.queryByText(/Could not update queue task: write failed/)).toBeNull();
  });

  it("clears a prior action error when the state filter changes", () => {
    const { mutation } = setup({ mutation: { error: new Error("write failed") } });
    mutation.reset.mockImplementation(() => {
      mutation.error = null;
    });
    render(<QueueMonitorPage projectId="project-1" />);

    expect(screen.getByRole("alert").textContent).toContain("write failed");
    fireEvent.change(screen.getByLabelText("Filter by state"), {
      target: { value: "failed" },
    });

    expect(mutation.reset).toHaveBeenCalledTimes(1);
    expect(mocks.useQueueTasks).toHaveBeenLastCalledWith("project-1", "failed", 0);
    expect(screen.queryByText(/Could not update queue task: write failed/)).toBeNull();
  });

  it("clears action errors and resets the polling window when Refresh is clicked", () => {
    const { query, mutation } = setup({ mutation: { error: new Error("write failed") } });
    mutation.reset.mockImplementation(() => {
      mutation.error = null;
    });
    render(<QueueMonitorPage projectId="project-1" />);

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(mutation.reset).toHaveBeenCalledTimes(1);
    expect(query.resetPollingWindow).toHaveBeenCalledTimes(1);
    expect(query.refetch).toHaveBeenCalledWith({ cancelRefetch: true });
  });

  it("keeps Refresh and pagination usable while a request is in flight", () => {
    const { query } = setup({
      query: { isFetching: true, data: makePage({ total: 65, has_more: true }) },
    });
    render(<QueueMonitorPage projectId="project-1" />);

    const refresh = screen.getByRole("button", { name: "Refresh" });
    expect(refresh).toBeEnabled();
    expect(refresh).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled();

    fireEvent.click(refresh);
    expect(query.refetch).toHaveBeenCalledWith({ cancelRefetch: true });
  });

  it("keeps showing loaded tasks with an inline alert when a background refresh fails", () => {
    setup({ query: { isError: true, error: new Error("offline") } });
    render(<QueueMonitorPage projectId="project-1" />);

    expect(screen.getByTestId("queue-task-task-running-1")).toBeTruthy();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Refresh failed; showing the last loaded results/,
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("disables only tasks whose actions are pending", () => {
    setup({ mutation: { pendingTaskIds: new Set(["task-running-1"]) } });
    render(<QueueMonitorPage projectId="project-1" />);

    expect(screen.getByRole("button", { name: "Pause task task-running-1" })).toBeDisabled();
  });

  it("retries a failed queue query and restarts its polling window", () => {
    const { query, mutation } = setup({
      query: { data: undefined, isError: true, error: new Error("offline") },
    });
    render(<QueueMonitorPage projectId="project-1" />);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(mutation.reset).toHaveBeenCalledTimes(1);
    expect(query.resetPollingWindow).toHaveBeenCalledTimes(1);
    expect(query.refetch).toHaveBeenCalledTimes(1);
  });

  it("passes pause actions through to the mutation hook", () => {
    const { mutation } = setup();
    render(<QueueMonitorPage projectId="project-1" />);

    fireEvent.click(screen.getByRole("button", { name: "Pause task task-running-1" }));

    expect(mutation.mutate).toHaveBeenCalledWith({
      taskId: "task-running-1",
      action: "pause",
    });
  });

  describe("operator questions", () => {
    function setupAwaiting(items: QueueTask[] = [makeAwaitingTask(), makeTask()]) {
      const reply = makeReplyMutation();
      mocks.useQueueTaskReply.mockReturnValue(reply);
      const result = setup({ query: { data: makePage({ items, total: items.length }) } });
      return { ...result, reply };
    }

    it("counts tasks awaiting input on the page and offers the awaiting filter", () => {
      setupAwaiting();
      render(<QueueMonitorPage projectId="project-1" />);

      expect(screen.getByTestId("queue-awaiting-input-banner")).toHaveTextContent(
        "1 task on this page is waiting for your answer.",
      );
      fireEvent.click(screen.getByRole("button", { name: "Show only awaiting input" }));
      expect(mocks.useQueueTasks).toHaveBeenLastCalledWith("project-1", "awaiting_input", 0);
      expect(screen.getByLabelText("Filter by state")).toHaveValue("awaiting_input");
    });

    it("hides the banner when no task on the page awaits input", () => {
      setup();
      render(<QueueMonitorPage projectId="project-1" />);
      expect(screen.queryByTestId("queue-awaiting-input-banner")).toBeNull();
    });

    it("sends a chosen option through the reply hook", () => {
      const { reply } = setupAwaiting();
      render(<QueueMonitorPage projectId="project-1" />);

      fireEvent.click(screen.getByRole("button", { name: "Cinematic" }));
      expect(reply.mutate).toHaveBeenCalledWith(
        { taskId: "task-awaiting-1", reply: { choice: "Cinematic" } },
        expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
      );
    });

    it("disables the question while its reply is in flight", () => {
      const reply = makeReplyMutation({ pendingTaskIds: new Set(["task-awaiting-1"]) });
      mocks.useQueueTaskReply.mockReturnValue(reply);
      setup({ query: { data: makePage({ items: [makeAwaitingTask()] }) } });
      render(<QueueMonitorPage projectId="project-1" />);

      expect(screen.getByRole("button", { name: "Cinematic" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Cancel task task-awaiting-1" })).toBeDisabled();
    });

    it("shows a 422 validation error next to the still-pending question", () => {
      const { reply } = setupAwaiting();
      render(<QueueMonitorPage projectId="project-1" />);

      fireEvent.click(screen.getByRole("button", { name: "Anime" }));
      const callbacks = reply.mutate.mock.calls[0][1] as { onError: (error: unknown) => void };
      act(() =>
        callbacks.onError(
          new ApiError({ status: 422, detail: "Choice is not one of the pending options." }),
        ),
      );

      const question = screen.getByTestId("queue-task-question-task-awaiting-1");
      expect(within(question).getByRole("alert")).toHaveTextContent(
        "The answer was rejected: Choice is not one of the pending options.",
      );
      expect(screen.getAllByRole("alert")).toHaveLength(1);
    });

    it("explains a 409 at page level once the task is no longer awaiting input", () => {
      const { reply, query } = setupAwaiting();
      const { rerender } = render(<QueueMonitorPage projectId="project-1" />);

      fireEvent.click(screen.getByRole("button", { name: "Anime" }));
      const callbacks = reply.mutate.mock.calls[0][1] as { onError: (error: unknown) => void };
      // The hook invalidates the queue on settle; the refreshed task is running again.
      query.data = makePage({
        items: [{ ...makeAwaitingTask(), state: "running", pending_input: null }],
      });
      act(() =>
        callbacks.onError(
          new ApiError({
            status: 409,
            detail: "Task is not awaiting input.",
            category: "input_not_pending",
            retryable: false,
          }),
        ),
      );
      rerender(<QueueMonitorPage projectId="project-1" />);

      expect(screen.queryByTestId("queue-task-question-task-awaiting-1")).toBeNull();
      expect(screen.getByRole("alert")).toHaveTextContent(
        "This question is no longer waiting for an answer. The queue was refreshed",
      );

      fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });
});

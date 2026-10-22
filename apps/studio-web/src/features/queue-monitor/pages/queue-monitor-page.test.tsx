import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QueueTask, QueueTaskPage } from "@/features/queue-monitor/state";

const mocks = vi.hoisted(() => ({
  useQueueTasks: vi.fn(),
  useQueueTaskAction: vi.fn(),
}));

vi.mock("@/features/queue-monitor/api", () => ({
  useQueueTasks: mocks.useQueueTasks,
  useQueueTaskAction: mocks.useQueueTaskAction,
}));

import { QueueMonitorPage } from "@/features/queue-monitor/pages/queue-monitor-page";

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
    ...overrides.mutation,
  };

  mocks.useQueueTasks.mockReturnValue(query);
  mocks.useQueueTaskAction.mockReturnValue(mutation);
  return { query, mutation };
}

afterEach(() => cleanup());

beforeEach(() => {
  vi.clearAllMocks();
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
    expect(screen.getByText("Queued / running")).toBeTruthy();
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
    expect(query.refetch).toHaveBeenCalledTimes(1);
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
});

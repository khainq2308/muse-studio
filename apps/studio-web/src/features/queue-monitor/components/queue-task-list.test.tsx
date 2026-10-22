import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueueTaskList } from "@/features/queue-monitor/components/queue-task-list";
import type { QueueTask, QueueTaskState } from "@/features/queue-monitor/state";

function makeTask(state: QueueTaskState, overrides: Partial<QueueTask> = {}): QueueTask {
  return {
    account_used: null,
    attempt_count: 1,
    connection_id: "connection-1",
    error_category: null,
    error_message_redacted: null,
    external_task_id: null,
    history_log: ["Task accepted."],
    id: `task-${state}`,
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
    state,
    take_id: null,
    task_type: "video",
    ...overrides,
  };
}

afterEach(() => cleanup());

describe("QueueTaskList", () => {
  it("renders generated task fields and does not offer controls for terminal tasks", () => {
    const task = makeTask("succeeded", { result_asset_id: "asset-1" });
    render(<QueueTaskList tasks={[task]} busyTaskId={null} onAction={vi.fn()} />);

    expect(screen.getByText(task.id)).toBeTruthy();
    expect(screen.getByText("Succeeded")).toBeTruthy();
    expect(screen.getByText("asset-1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: `Pause task ${task.id}` })).toBeNull();
    expect(screen.queryByRole("button", { name: `Resume task ${task.id}` })).toBeNull();
    expect(screen.queryByRole("button", { name: `Cancel task ${task.id}` })).toBeNull();
  });

  it("offers pause and cancel for active tasks, but requires confirmation before cancel", () => {
    const task = makeTask("running");
    const onAction = vi.fn();
    render(<QueueTaskList tasks={[task]} busyTaskId={null} onAction={onAction} />);

    fireEvent.click(screen.getByRole("button", { name: `Pause task ${task.id}` }));
    expect(onAction).toHaveBeenCalledWith(task.id, "pause");

    fireEvent.click(screen.getByRole("button", { name: `Cancel task ${task.id}` }));
    expect(onAction).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancel" }));
    expect(onAction).toHaveBeenLastCalledWith(task.id, "cancel");
  });

  it("offers resume for paused tasks and disables controls while the task action is pending", () => {
    const task = makeTask("paused");
    render(<QueueTaskList tasks={[task]} busyTaskId={task.id} onAction={vi.fn()} />);

    expect(screen.getByRole("button", { name: `Resume task ${task.id}` })).toBeDisabled();
    expect(screen.queryByRole("button", { name: `Pause task ${task.id}` })).toBeNull();
  });
});

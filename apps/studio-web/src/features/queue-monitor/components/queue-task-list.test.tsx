import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueueTaskList } from "@/features/queue-monitor/components/queue-task-list";
import type { QueuePendingInput, QueueTask, QueueTaskState } from "@/features/queue-monitor/state";

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
    render(
      <QueueTaskList tasks={[task]} busyTaskIds={new Set()} onAction={vi.fn()} onReply={vi.fn()} />,
    );

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
    render(
      <QueueTaskList
        tasks={[task]}
        busyTaskIds={new Set()}
        onAction={onAction}
        onReply={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: `Pause task ${task.id}` }));
    expect(onAction).toHaveBeenCalledWith(task.id, "pause");

    fireEvent.click(screen.getByRole("button", { name: `Cancel task ${task.id}` }));
    expect(onAction).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancel" }));
    expect(onAction).toHaveBeenLastCalledWith(task.id, "cancel");
  });

  it("offers resume for paused tasks and disables controls while the task action is pending", () => {
    const task = makeTask("paused");
    render(
      <QueueTaskList
        tasks={[task]}
        busyTaskIds={new Set([task.id])}
        onAction={vi.fn()}
        onReply={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: `Resume task ${task.id}` })).toBeDisabled();
    expect(screen.queryByRole("button", { name: `Pause task ${task.id}` })).toBeNull();
  });

  it("moves focus to Keep task on open and back to Cancel when dismissed", () => {
    const task = makeTask("running");
    render(
      <QueueTaskList tasks={[task]} busyTaskIds={new Set()} onAction={vi.fn()} onReply={vi.fn()} />,
    );

    const cancel = screen.getByRole("button", { name: `Cancel task ${task.id}` });
    cancel.focus();
    fireEvent.click(cancel);

    const dialog = screen.getByRole("alertdialog", {
      name: `Confirm cancellation for task ${task.id}`,
    });
    expect(dialog).toHaveAccessibleDescription(/Cancel this task\?/);
    expect(screen.getByRole("button", { name: "Keep task" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "Keep task" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByRole("button", { name: `Cancel task ${task.id}` })).toHaveFocus();
  });

  it("dismisses the cancel confirmation with Escape without cancelling", () => {
    const task = makeTask("queued");
    const onAction = vi.fn();
    render(
      <QueueTaskList
        tasks={[task]}
        busyTaskIds={new Set()}
        onAction={onAction}
        onReply={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: `Cancel task ${task.id}` }));
    fireEvent.keyDown(screen.getByRole("button", { name: "Keep task" }), { key: "Escape" });

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByRole("button", { name: `Cancel task ${task.id}` })).toHaveFocus();
    expect(onAction).not.toHaveBeenCalled();
  });

  it("keeps every concurrently busy task disabled", () => {
    const first = makeTask("running", { id: "task-a" });
    const second = makeTask("running", { id: "task-b" });
    const third = makeTask("running", { id: "task-c" });
    render(
      <QueueTaskList
        tasks={[first, second, third]}
        busyTaskIds={new Set(["task-a", "task-b"])}
        onAction={vi.fn()}
        onReply={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Pause task task-a" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pause task task-b" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pause task task-c" })).toBeEnabled();
  });

  it("labels a task state that this client does not know instead of an empty badge", () => {
    const task = makeTask("running", { state: "archived" as unknown as QueueTaskState });
    render(
      <QueueTaskList tasks={[task]} busyTaskIds={new Set()} onAction={vi.fn()} onReply={vi.fn()} />,
    );

    expect(screen.getByTestId("queue-task-state")).toHaveTextContent("Unknown state (archived)");
  });

  describe("awaiting input", () => {
    function awaitingTask(pendingInput: Partial<QueuePendingInput> = {}): QueueTask {
      return makeTask("awaiting_input", {
        pending_input: {
          kind: "question",
          text: "Which style should the shot use?",
          options: ["Cinematic", "Anime"],
          asked_at: 1761200000.5,
          ...pendingInput,
        },
      });
    }

    it("shows the question, kind, asked time and badge, keeps cancel but never offers pause", () => {
      const task = awaitingTask();
      const onAction = vi.fn();
      render(
        <QueueTaskList
          tasks={[task]}
          busyTaskIds={new Set()}
          onAction={onAction}
          onReply={vi.fn()}
        />,
      );

      expect(screen.getByTestId("queue-task-state")).toHaveTextContent("Awaiting input");
      const question = screen.getByRole("region", { name: "Question" });
      expect(question).toHaveAccessibleDescription("Which style should the shot use?");
      const askedTime = question.querySelector("time");
      expect(askedTime?.getAttribute("datetime")).toBe(new Date(1761200000500).toISOString());
      expect(screen.queryByRole("button", { name: `Pause task ${task.id}` })).toBeNull();
      expect(screen.queryByRole("button", { name: `Resume task ${task.id}` })).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: `Cancel task ${task.id}` }));
      fireEvent.click(screen.getByRole("button", { name: "Confirm cancel" }));
      expect(onAction).toHaveBeenCalledWith(task.id, "cancel");
    });

    it("sends an option as a choice and a trimmed free-text answer as text", () => {
      const task = awaitingTask();
      const onReply = vi.fn();
      render(
        <QueueTaskList
          tasks={[task]}
          busyTaskIds={new Set()}
          onAction={vi.fn()}
          onReply={onReply}
        />,
      );

      const options = screen.getByRole("group", { name: "Answer options" });
      fireEvent.click(within(options).getByRole("button", { name: "Anime" }));
      expect(onReply).toHaveBeenLastCalledWith(task.id, { choice: "Anime" }, expect.any(Function));

      const send = screen.getByRole("button", { name: "Send answer" });
      expect(send).toBeDisabled();
      const textbox = screen.getByRole("textbox", { name: "Or write an answer" });
      fireEvent.change(textbox, { target: { value: "   " } });
      expect(send).toBeDisabled();
      fireEvent.change(textbox, { target: { value: "  Noir, high contrast  " } });
      fireEvent.click(send);
      expect(onReply).toHaveBeenLastCalledWith(
        task.id,
        { text: "Noir, high contrast" },
        expect.any(Function),
      );

      // The draft is cleared only when the backend accepted the answer.
      expect(textbox).toHaveValue("  Noir, high contrast  ");
      const onSuccess = onReply.mock.calls[onReply.mock.calls.length - 1][2] as () => void;
      act(() => onSuccess());
      expect(textbox).toHaveValue("");
    });

    it("answers an approval with its options only, but allows text when it has no options", () => {
      const approval = awaitingTask({ kind: "approval", options: ["Yes", "No"] });
      const { rerender } = render(
        <QueueTaskList
          tasks={[approval]}
          busyTaskIds={new Set()}
          onAction={vi.fn()}
          onReply={vi.fn()}
        />,
      );
      expect(screen.getByRole("region", { name: "Approval requested" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Yes" })).toBeEnabled();
      expect(screen.queryByRole("textbox")).toBeNull();

      rerender(
        <QueueTaskList
          tasks={[awaitingTask({ kind: "approval", options: [] })]}
          busyTaskIds={new Set()}
          onAction={vi.fn()}
          onReply={vi.fn()}
        />,
      );
      expect(screen.getByRole("textbox", { name: "Your answer" })).toBeTruthy();
    });

    it("offers free text for a stalled prompt alongside its options", () => {
      render(
        <QueueTaskList
          tasks={[awaitingTask({ kind: "stalled", options: ["Keep waiting", "Give up"] })]}
          busyTaskIds={new Set()}
          onAction={vi.fn()}
          onReply={vi.fn()}
        />,
      );
      expect(screen.getByRole("region", { name: "Generation stalled" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Keep waiting" })).toBeTruthy();
      expect(screen.getByRole("textbox", { name: "Or write an answer" })).toBeTruthy();
    });

    it("disables every answer control while the task is busy and shows the reply error", () => {
      const task = awaitingTask();
      render(
        <QueueTaskList
          tasks={[task]}
          busyTaskIds={new Set([task.id])}
          onAction={vi.fn()}
          replyError={{ taskId: task.id, message: "The answer was rejected: bad choice" }}
          onReply={vi.fn()}
        />,
      );

      expect(screen.getByRole("button", { name: "Cinematic" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Anime" })).toBeDisabled();
      expect(screen.getByRole("textbox")).toBeDisabled();
      expect(screen.getByRole("button", { name: "Send answer" })).toBeDisabled();
      expect(screen.getByRole("button", { name: `Cancel task ${task.id}` })).toBeDisabled();
      expect(screen.getByRole("alert")).toHaveTextContent("The answer was rejected: bad choice");
    });

    it("renders provider question text as plain text, never as HTML", () => {
      const html = '<img src=x onerror="alert(1)"><b>bold</b>';
      render(
        <QueueTaskList
          tasks={[awaitingTask({ text: html, asked_at: null })]}
          busyTaskIds={new Set()}
          onAction={vi.fn()}
          onReply={vi.fn()}
        />,
      );

      const question = screen.getByRole("region", { name: "Question" });
      expect(question.querySelector("img")).toBeNull();
      expect(question.querySelector("b")).toBeNull();
      expect(question).toHaveTextContent(html);
      expect(question).toHaveTextContent("Asked time unknown");
    });

    it("explains an awaiting task without a question payload", () => {
      render(
        <QueueTaskList
          tasks={[makeTask("awaiting_input", { pending_input: null })]}
          busyTaskIds={new Set()}
          onAction={vi.fn()}
          onReply={vi.fn()}
        />,
      );
      expect(screen.getByText(/did not send a question yet/)).toBeTruthy();
      expect(screen.queryByRole("textbox")).toBeNull();
    });
  });
});

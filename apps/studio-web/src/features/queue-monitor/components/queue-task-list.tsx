import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Pause, Play, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { QueueTaskQuestion } from "@/features/queue-monitor/components/queue-task-question";
import {
  QUEUE_TERMINAL_TASK_STATES,
  queueTaskStateLabel,
  queueTaskStateTone,
  type QueueTask,
  type QueueTaskAction,
  type QueueTaskReply,
} from "@/features/queue-monitor/state";
import { cn } from "@/lib/utils";

interface QueueTaskListProps {
  tasks: QueueTask[];
  /** Tasks with an in-flight pause/resume/cancel or reply request. */
  busyTaskIds: ReadonlySet<string>;
  onAction: (taskId: string, action: QueueTaskAction) => void;
  /** Latest failed reply, shown next to that task's question while it is still pending. */
  replyError?: { taskId: string; message: string } | null;
  onReply: (taskId: string, reply: QueueTaskReply, onSuccess: () => void) => void;
}

export function QueueTaskList({
  tasks,
  busyTaskIds,
  onAction,
  replyError = null,
  onReply,
}: QueueTaskListProps) {
  const [cancelConfirmationId, setCancelConfirmationId] = useState<string | null>(null);
  const cancelButtons = useRef(new Map<string, HTMLButtonElement>());
  const keepTaskButton = useRef<HTMLButtonElement | null>(null);
  const returnFocusTo = useRef<string | null>(null);

  const openCancelConfirmation = (taskId: string) => {
    returnFocusTo.current = null;
    setCancelConfirmationId(taskId);
  };
  const dismissCancelConfirmation = useCallback((taskId: string) => {
    returnFocusTo.current = taskId;
    setCancelConfirmationId(null);
  }, []);

  useEffect(() => {
    if (cancelConfirmationId) {
      // Start on the safe choice so Enter/Space cannot cancel the task by accident.
      keepTaskButton.current?.focus();
      return;
    }
    const taskId = returnFocusTo.current;
    returnFocusTo.current = null;
    if (taskId) cancelButtons.current.get(taskId)?.focus();
  }, [cancelConfirmationId]);

  // A confirmation whose task left the visible list (filter, page, refresh) is dropped
  // instead of silently reopening when the task comes back.
  const confirmationTaskVisible =
    cancelConfirmationId !== null && tasks.some((task) => task.id === cancelConfirmationId);
  useEffect(() => {
    if (cancelConfirmationId !== null && !confirmationTaskVisible) {
      returnFocusTo.current = null;
      setCancelConfirmationId(null);
    }
  }, [cancelConfirmationId, confirmationTaskVisible]);

  const runAction = (taskId: string, action: "pause" | "resume") => {
    // Another action on the task supersedes a pending cancel confirmation.
    if (cancelConfirmationId === taskId) {
      returnFocusTo.current = null;
      setCancelConfirmationId(null);
    }
    onAction(taskId, action);
  };

  const onConfirmationKeyDown = (event: KeyboardEvent<HTMLDivElement>, taskId: string) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    dismissCancelConfirmation(taskId);
  };

  return (
    <div className="space-y-3">
      {tasks.map((task) => {
        const busy = busyTaskIds.has(task.id);
        // Pausing is refused (409) while the provider waits for an answer; cancel stays allowed.
        const canPause = task.state === "queued" || task.state === "running";
        const pendingInput = task.state === "awaiting_input" ? task.pending_input : null;
        const canResume = task.state === "paused";
        const canCancel = !QUEUE_TERMINAL_TASK_STATES.includes(task.state);

        return (
          <article
            key={task.id}
            data-testid={`queue-task-${task.id}`}
            aria-busy={busy}
            className={cn(
              "rounded-xl border bg-card p-4 shadow-sm sm:p-5",
              task.state === "awaiting_input" ? "border-warning/60" : "border-border",
            )}
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="break-all font-semibold">{task.id}</h3>
                  <span
                    data-testid="queue-task-state"
                    className={cn(
                      "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium",
                      queueTaskStateTone(task.state),
                    )}
                  >
                    {queueTaskStateLabel(task.state)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  Shot <span className="break-all font-medium text-foreground">{task.shot_id}</span>
                  <span className="mx-2" aria-hidden="true">
                    ·
                  </span>
                  {task.task_type} generation
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {canPause && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={`Pause task ${task.id}`}
                    disabled={busy}
                    onClick={() => runAction(task.id, "pause")}
                  >
                    <Pause className="mr-1 size-4" aria-hidden="true" />
                    Pause
                  </Button>
                )}
                {canResume && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={`Resume task ${task.id}`}
                    disabled={busy}
                    onClick={() => runAction(task.id, "resume")}
                  >
                    <Play className="mr-1 size-4" aria-hidden="true" />
                    Resume
                  </Button>
                )}
                {canCancel && cancelConfirmationId !== task.id && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={`Cancel task ${task.id}`}
                    aria-haspopup="dialog"
                    disabled={busy}
                    ref={(element) => {
                      if (element) cancelButtons.current.set(task.id, element);
                      else cancelButtons.current.delete(task.id);
                    }}
                    onClick={() => openCancelConfirmation(task.id)}
                  >
                    <XCircle className="mr-1 size-4" aria-hidden="true" />
                    Cancel
                  </Button>
                )}
              </div>
            </div>

            <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
              <div className="min-w-0">
                <dt className="text-xs text-muted-foreground">Connection</dt>
                <dd className="break-all font-medium">{task.connection_id}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-xs text-muted-foreground">Model</dt>
                <dd className="break-all font-medium">{task.model_id}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Attempts / polls</dt>
                <dd className="font-medium tabular-nums">
                  {task.attempt_count}/{task.max_attempts} attempts · {task.poll_count}/
                  {task.max_polls} polls
                </dd>
              </div>
              {task.external_task_id && (
                <div className="min-w-0">
                  <dt className="text-xs text-muted-foreground">Provider task</dt>
                  <dd className="break-all font-medium">{task.external_task_id}</dd>
                </div>
              )}
              {task.account_used && (
                <div className="min-w-0">
                  <dt className="text-xs text-muted-foreground">Account</dt>
                  <dd className="break-all font-medium">{task.account_used}</dd>
                </div>
              )}
              {task.result_asset_id && (
                <div className="min-w-0">
                  <dt className="text-xs text-muted-foreground">Result asset</dt>
                  <dd className="break-all font-medium">{task.result_asset_id}</dd>
                </div>
              )}
            </dl>

            {task.state === "awaiting_input" &&
              (pendingInput ? (
                <QueueTaskQuestion
                  key={`${pendingInput.asked_at ?? ""}:${pendingInput.text}`}
                  taskId={task.id}
                  pendingInput={pendingInput}
                  busy={busy}
                  error={replyError?.taskId === task.id ? replyError.message : null}
                  onReply={(reply, onSuccess) => onReply(task.id, reply, onSuccess)}
                />
              ) : (
                <p className="mt-4 rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm">
                  The provider is waiting for input but did not send a question yet. Refresh to
                  check again.
                </p>
              ))}

            {task.error_message_redacted && (
              <p
                className={cn(
                  "mt-3 rounded-md border border-destructive/20 bg-destructive/5 p-3",
                  "text-sm text-destructive",
                )}
              >
                <span className="font-semibold">Last error:</span> {task.error_message_redacted}
              </p>
            )}

            {task.history_log.length > 0 && (
              <p className="mt-3 text-xs text-muted-foreground">
                Latest event: {task.history_log[task.history_log.length - 1]}
              </p>
            )}

            {cancelConfirmationId === task.id && canCancel && (
              <div
                role="alertdialog"
                aria-label={`Confirm cancellation for task ${task.id}`}
                aria-describedby={`cancel-confirmation-${task.id}`}
                onKeyDown={(event) => onConfirmationKeyDown(event, task.id)}
                className={cn(
                  "mt-4 flex flex-col gap-3 rounded-lg border border-destructive/30",
                  "bg-destructive/5 p-3 sm:flex-row sm:items-center sm:justify-between",
                )}
              >
                <p id={`cancel-confirmation-${task.id}`} className="text-sm">
                  Cancel this task? This cannot be undone and may stop generation.
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      dismissCancelConfirmation(task.id);
                      onAction(task.id, "cancel");
                    }}
                  >
                    Confirm cancel
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    ref={keepTaskButton}
                    onClick={() => dismissCancelConfirmation(task.id)}
                  >
                    Keep task
                  </Button>
                </div>
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}

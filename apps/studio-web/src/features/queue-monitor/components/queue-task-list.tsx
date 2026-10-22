import { useState } from "react";
import { Pause, Play, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  QUEUE_TASK_STATE_LABELS,
  QUEUE_TASK_STATE_TONES,
  QUEUE_TERMINAL_TASK_STATES,
  type QueueTask,
  type QueueTaskAction,
} from "@/features/queue-monitor/state";
import { cn } from "@/lib/utils";

interface QueueTaskListProps {
  tasks: QueueTask[];
  busyTaskId: string | null;
  onAction: (taskId: string, action: QueueTaskAction) => void;
}

export function QueueTaskList({ tasks, busyTaskId, onAction }: QueueTaskListProps) {
  const [cancelConfirmationId, setCancelConfirmationId] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      {tasks.map((task) => {
        const busy = busyTaskId === task.id;
        const canPause = task.state === "queued" || task.state === "running";
        const canResume = task.state === "paused";
        const canCancel = !QUEUE_TERMINAL_TASK_STATES.includes(task.state);

        return (
          <article
            key={task.id}
            data-testid={`queue-task-${task.id}`}
            aria-busy={busy}
            className="rounded-xl border border-border bg-card p-4 shadow-sm sm:p-5"
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="break-all font-semibold">{task.id}</h3>
                  <span
                    data-testid="queue-task-state"
                    className={cn(
                      "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium",
                      QUEUE_TASK_STATE_TONES[task.state],
                    )}
                  >
                    {QUEUE_TASK_STATE_LABELS[task.state]}
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
                    onClick={() => onAction(task.id, "pause")}
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
                    onClick={() => onAction(task.id, "resume")}
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
                    disabled={busy}
                    onClick={() => setCancelConfirmationId(task.id)}
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

            {cancelConfirmationId === task.id && (
              <div
                role="group"
                aria-label={`Confirm cancellation for task ${task.id}`}
                className={cn(
                  "mt-4 flex flex-col gap-3 rounded-lg border border-destructive/30",
                  "bg-destructive/5 p-3 sm:flex-row sm:items-center sm:justify-between",
                )}
              >
                <p className="text-sm">
                  Cancel this task? This cannot be undone and may stop generation.
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      setCancelConfirmationId(null);
                      onAction(task.id, "cancel");
                    }}
                  >
                    Confirm cancel
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setCancelConfirmationId(null)}
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

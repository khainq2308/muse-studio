import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { queueTaskStateLabel, queueTaskStateTone, type QueueTask } from "@/entities/task/state";
import { QueueMonitorLink } from "@/features/shot-board/components/queue-monitor-link";
import { cn } from "@/lib/utils";

/** Terminal states a user can recover from by starting a new task. */
const RETRYABLE_END_STATES = new Set(["failed", "poll_timeout", "canceled"]);

interface ShotLatestTaskProps {
  projectId: string;
  task: QueueTask;
  /** Null when a new attempt can be started; otherwise why it cannot (e.g. no generation setup). */
  retryDisabledReason: string | null;
  onRetry: () => void;
}

/** The shot's most recent generation task: where it stands and, if it ended badly, why. */
export function ShotLatestTask({
  projectId,
  task,
  retryDisabledReason,
  onRetry,
}: ShotLatestTaskProps) {
  const ended = RETRYABLE_END_STATES.has(task.state);
  return (
    <div
      data-testid="shot-latest-task"
      className={cn(
        "space-y-2 rounded-lg border p-3 text-sm",
        task.state === "failed" || task.state === "poll_timeout"
          ? "border-destructive/30 bg-destructive/5"
          : task.state === "awaiting_input"
            ? "border-warning/40 bg-warning/5"
            : "border-border bg-muted/30",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Latest generation</span>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-xs font-medium",
            queueTaskStateTone(task.state),
          )}
        >
          {queueTaskStateLabel(task.state)}
        </span>
        <span className="text-xs text-muted-foreground">
          {task.task_type} · <span className="break-all font-mono">{task.model_id}</span> on{" "}
          <span className="break-all font-mono">{task.connection_id}</span> · automatic tries{" "}
          {task.attempt_count} of {task.max_attempts} ·{" "}
          <span className="break-all font-mono">{task.id}</span>
        </span>
      </div>

      {task.state === "awaiting_input" && (
        <p className="text-warning">
          The provider is waiting for an answer. Reply in the Generation Queue to continue.
        </p>
      )}
      {(task.state === "failed" || task.state === "poll_timeout") && (
        <p className="text-destructive" data-testid="shot-latest-task-error">
          {task.error_message_redacted || "The provider reported no error details."}
          {task.error_category ? ` (${task.error_category})` : ""}
        </p>
      )}
      {(task.state === "failed" || task.state === "poll_timeout") && !task.retryable && (
        <p className="text-xs text-muted-foreground">
          The backend marked this error as permanent: retrying unchanged will likely fail again.
          Check the shot or the generation setup first.
        </p>
      )}
      {ended && (
        <p className="text-xs text-muted-foreground">
          While the shot, model and connection are unchanged, Generate returns this task again.
          Start a new task always creates one, with the current generation setup.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {ended && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={retryDisabledReason !== null}
            title={retryDisabledReason ?? undefined}
            aria-haspopup="dialog"
            onClick={onRetry}
          >
            <RotateCcw className="size-4" aria-hidden="true" />
            Start a new task
          </Button>
        )}
        <QueueMonitorLink projectId={projectId} />
      </div>
      {ended && retryDisabledReason && (
        <p className="text-xs text-muted-foreground">{retryDisabledReason}</p>
      )}
    </div>
  );
}

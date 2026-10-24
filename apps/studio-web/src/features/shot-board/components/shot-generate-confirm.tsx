import { useEffect, useId, useRef } from "react";
import { Button } from "@/components/ui/button";
import { QueueMonitorLink } from "@/features/shot-board/components/queue-monitor-link";
import {
  isMockConnection,
  shotLabel,
  type Connection,
  type EnqueuedTask,
  type GenerationTaskType,
  type ProjectShot,
} from "@/features/shot-board/state";

interface ShotGenerateConfirmProps {
  shot: ProjectShot;
  connection: Connection;
  modelId: string;
  taskType: GenerationTaskType;
  isSubmitting: boolean;
  /** A new task after an ended one: sends `force_retry`, so it is never deduplicated. */
  forceRetry?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Inline confirmation before a quota-consuming single-shot enqueue. */
export function ShotGenerateConfirm({
  shot,
  connection,
  modelId,
  taskType,
  isSubmitting,
  forceRetry = false,
  onConfirm,
  onCancel,
}: ShotGenerateConfirmProps) {
  const idPrefix = useId();
  const keepButton = useRef<HTMLButtonElement | null>(null);
  useEffect(() => keepButton.current?.focus(), []);

  return (
    <div
      role="alertdialog"
      aria-labelledby={`${idPrefix}-title`}
      aria-describedby={`${idPrefix}-body`}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        onCancel();
      }}
      className="mt-4 space-y-3 rounded-lg border border-warning/40 bg-warning/5 p-4"
    >
      <p id={`${idPrefix}-title`} className="font-semibold">
        {forceRetry
          ? `Start a new ${taskType} generation task for ${shotLabel(shot)}?`
          : `Queue ${taskType} generation for ${shotLabel(shot)}?`}
      </p>
      <div id={`${idPrefix}-body`} className="space-y-1 text-sm">
        <p>
          Connection: <span className="font-medium">{connection.name}</span> ·{" "}
          {isMockConnection(connection)
            ? "Mock provider (no provider cost)"
            : `Live provider (${connection.provider_type}): may consume quota`}
        </p>
        <p>
          Model: <span className="break-all font-medium">{modelId.trim()}</span> · 1 shot
        </p>
        {forceRetry ? (
          <p className="text-xs font-medium text-warning">
            This always creates a new task, even if an identical request already ran, and may
            consume provider quota again.
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            If an identical request already exists, the server returns that task instead of starting
            a new generation.
          </p>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={onConfirm} disabled={isSubmitting}>
          {forceRetry ? "Confirm new task" : "Confirm and queue"}
        </Button>
        <Button type="button" size="sm" variant="outline" ref={keepButton} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

interface ShotEnqueueResultProps {
  projectId: string;
  task: EnqueuedTask;
  onDismiss: () => void;
  /** Offered when the request matched a task that already ended. */
  onRetry?: () => void;
}

/** Result of `POST /api/v1/queue/tasks`, reporting deduplicated requests honestly. */
export function ShotEnqueueResult({ projectId, task, onDismiss, onRetry }: ShotEnqueueResultProps) {
  const matchedEndedTask =
    !task.created_new &&
    (task.state === "failed" || task.state === "poll_timeout" || task.state === "canceled");
  return (
    <div
      role="status"
      data-testid="shot-enqueue-result"
      className="mt-4 space-y-2 rounded-lg border border-border bg-muted/30 p-3 text-sm"
    >
      {task.created_new ? (
        <p>
          Queued {task.task_type} task <span className="break-all font-medium">{task.id}</span>{" "}
          (state: {task.state}).
        </p>
      ) : (
        <p>
          This request matched an existing task{" "}
          <span className="break-all font-medium">{task.id}</span> (state: {task.state}). No new
          generation was started.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {matchedEndedTask && onRetry && (
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            Start a new task
          </Button>
        )}
        <QueueMonitorLink projectId={projectId} />
        <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  );
}

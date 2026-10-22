import { useState } from "react";
import { cn } from "@/lib/utils";
import { RefreshCw } from "lucide-react";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import { useQueueTaskAction, useQueueTasks } from "@/features/queue-monitor/api";
import { QueueTaskList } from "@/features/queue-monitor/components/queue-task-list";
import {
  isQueueTaskActive,
  QUEUE_TASK_STATES,
  QUEUE_TASK_STATE_LABELS,
  type QueueTaskAction,
  type QueueTaskFilter,
} from "@/features/queue-monitor/state";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

interface QueueMonitorPageProps {
  projectId: string;
}

export function QueueMonitorPage({ projectId }: QueueMonitorPageProps) {
  const [stateFilter, setStateFilter] = useState<QueueTaskFilter>("all");
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const tasksQuery = useQueueTasks(projectId, stateFilter);
  const actionMutation = useQueueTaskAction(projectId);
  const tasks = tasksQuery.data ?? [];
  const busyTaskId = actionMutation.isPending ? (actionMutation.variables?.taskId ?? null) : null;

  const handleAction = (taskId: string, action: QueueTaskAction) => {
    actionMutation.mutate({ taskId, action });
  };

  const actionError =
    actionMutation.error instanceof Error
      ? actionMutation.error.message
      : actionMutation.error
        ? "Could not update the queue task."
        : null;

  const activeCount = tasks.filter((task) => isQueueTaskActive(task.state)).length;
  const failedCount = tasks.filter(
    (task) => task.state === "failed" || task.state === "poll_timeout",
  ).length;

  return (
    <section
      data-testid="queue-monitor-page"
      data-project-id={projectId}
      aria-labelledby="queue-monitor-heading"
      className="space-y-6"
    >
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-primary">Project queue</p>
          <h2 id="queue-monitor-heading" className="mt-1 text-2xl font-semibold tracking-tight">
            Queue Monitor
          </h2>
          <p className="mt-2 break-all text-sm text-muted-foreground">
            Monitor generation tasks for project{" "}
            <span className="font-medium text-foreground">{projectId}</span>.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => void tasksQuery.refetch()}
          disabled={tasksQuery.isFetching}
        >
          <RefreshCw
            className={`mr-2 size-4 ${tasksQuery.isFetching ? "animate-spin" : ""}`}
            aria-hidden="true"
          />
          Refresh
        </Button>
      </header>

      <div className="grid gap-3 sm:grid-cols-3" aria-label="Queue summary">
        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-sm text-muted-foreground">Matching tasks</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{tasks.length}</p>
        </article>
        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-sm text-muted-foreground">Queued / running</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{activeCount}</p>
        </article>
        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-sm text-muted-foreground">Failed / timed out</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{failedCount}</p>
        </article>
      </div>

      <div className="flex flex-col gap-2 sm:max-w-xs">
        <label htmlFor="queue-state-filter" className="text-sm font-medium">
          Filter by state
        </label>
        <select
          id="queue-state-filter"
          value={stateFilter}
          onChange={(event) => setStateFilter(event.target.value as QueueTaskFilter)}
          className={cn(
            "rounded-md border border-input bg-background px-3 py-2 text-sm",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <option value="all">All states</option>
          {QUEUE_TASK_STATES.map((state) => (
            <option key={state} value={state}>
              {QUEUE_TASK_STATE_LABELS[state]}
            </option>
          ))}
        </select>
      </div>

      {actionError && (
        <div
          role="alert"
          className={cn(
            "rounded-lg border border-destructive/30 bg-destructive/5 p-4",
            "text-sm text-destructive",
          )}
        >
          Could not update queue task: {actionError}
        </div>
      )}

      <section aria-label="Queue tasks" className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="font-semibold">Generation tasks</h3>
          <span className="text-xs text-muted-foreground">
            Auto-refresh backs off from 2s to 10s and stops after 30 minutes or when no task is
            active.
          </span>
        </div>
        <QueryState
          isLoading={tasksQuery.isPending}
          isError={tasksQuery.isError}
          isEmpty={tasks.length === 0}
          loadingMessage="Loading queue tasks..."
          errorMessage={authErrorMessage(tasksQuery.error, activeAdminToken, "queue tasks")}
          emptyTitle={stateFilter === "all" ? "No queue tasks yet" : "No matching queue tasks"}
          emptyDescription={
            stateFilter === "all"
              ? "Generation tasks for this project will appear here when work is queued."
              : "Try another state filter or refresh to check for matching tasks."
          }
          onRetry={() => void tasksQuery.refetch()}
        >
          <QueueTaskList tasks={tasks} busyTaskId={busyTaskId} onAction={handleAction} />
        </QueryState>
      </section>
    </section>
  );
}

export default QueueMonitorPage;

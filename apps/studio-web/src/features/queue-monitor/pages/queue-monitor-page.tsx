import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { MessageCircleQuestionMark, RefreshCw } from "lucide-react";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import { useQueueTaskAction, useQueueTaskReply, useQueueTasks } from "@/features/queue-monitor/api";
import { QueueTaskList } from "@/features/queue-monitor/components/queue-task-list";
import { AwaitingInputNotifications } from "@/features/queue-monitor/components/awaiting-input-notifications";
import { useAwaitingInputAlert } from "@/features/queue-monitor/awaiting-input-alert";
import {
  QUEUE_TASKS_PAGE_SIZE,
  QUEUE_TASK_STATES,
  QUEUE_TASK_STATE_LABELS,
  type QueueTaskAction,
  type QueueTaskFilter,
  type QueueTaskReply,
} from "@/features/queue-monitor/state";
import { queueReplyErrorMessage } from "@/features/queue-monitor/reply-errors";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { PageHeader } from "@/components/shared/page-header";

interface QueueMonitorPageProps {
  projectId: string;
}

export function QueueMonitorPage({ projectId }: QueueMonitorPageProps) {
  const [stateFilter, setStateFilter] = useState<QueueTaskFilter>("all");
  const [pageState, setPageState] = useState({ scope: projectId + ":all", offset: 0 });
  const scope = projectId + ":" + stateFilter;
  const offset = pageState.scope === scope ? pageState.offset : 0;
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const tasksQuery = useQueueTasks(projectId, stateFilter, offset);
  const actionMutation = useQueueTaskAction(projectId, tasksQuery.resetPollingWindow);
  const replyMutation = useQueueTaskReply(projectId, tasksQuery.resetPollingWindow);
  const [replyError, setReplyError] = useState<{
    taskId: string;
    message: string;
    sessionVersion: number;
    projectId: string;
  } | null>(null);
  const tasks = tasksQuery.data?.items ?? [];
  const total = tasksQuery.data?.total ?? 0;
  const summary = tasksQuery.data?.summary;
  const isPageOutOfRange = Boolean(tasksQuery.data && offset >= total && offset > 0);
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const { reset: resetAction } = actionMutation;
  const { pendingTaskIds: pendingActionTaskIds } = actionMutation;
  const { pendingTaskIds: pendingReplyTaskIds } = replyMutation;
  const pendingTaskIds = useMemo<ReadonlySet<string>>(
    () => new Set([...pendingActionTaskIds, ...pendingReplyTaskIds]),
    [pendingActionTaskIds, pendingReplyTaskIds],
  );
  // Project-wide, independent of the filter and page being viewed.
  const awaitingInputCount = summary?.awaiting_input_tasks ?? 0;
  useAwaitingInputAlert(projectId, summary?.awaiting_input_tasks);

  // A new session must not show an action error (or pending state) from the previous one.
  const actionSessionVersion = useRef(sessionVersion);
  useEffect(() => {
    if (actionSessionVersion.current === sessionVersion) return;
    actionSessionVersion.current = sessionVersion;
    resetAction();
  }, [resetAction, sessionVersion]);

  useEffect(() => {
    if (!tasksQuery.data || !isPageOutOfRange) return;
    const lastValidOffset =
      total === 0 ? 0 : Math.floor((total - 1) / QUEUE_TASKS_PAGE_SIZE) * QUEUE_TASKS_PAGE_SIZE;
    setPageState({ scope, offset: lastValidOffset });
  }, [isPageOutOfRange, scope, tasksQuery.data, total]);

  const clearFeedback = () => {
    actionMutation.reset();
    setReplyError(null);
  };

  const setPageOffset = (nextOffset: number) => {
    clearFeedback();
    setPageState({ scope, offset: Math.max(0, nextOffset) });
  };

  const handleAction = (taskId: string, action: QueueTaskAction) => {
    actionMutation.mutate({ taskId, action });
  };

  const handleReply = (taskId: string, reply: QueueTaskReply, onSuccess: () => void) => {
    setReplyError(null);
    const startedSession = sessionVersion;
    replyMutation.mutate(
      { taskId, reply },
      {
        onSuccess,
        onError: (error) =>
          setReplyError({
            taskId,
            message: queueReplyErrorMessage(error),
            sessionVersion: startedSession,
            projectId,
          }),
      },
    );
  };

  // A reply error from a previous admin session or another project must not leak into this view.
  const visibleReplyError =
    replyError && replyError.sessionVersion === sessionVersion && replyError.projectId === projectId
      ? replyError
      : null;
  // Show the reply error next to its question while it is still visible; otherwise (for
  // example after a 409 the task moved on) show it at page level so it is not lost.
  const replyErrorInline = Boolean(
    visibleReplyError &&
    tasks.some(
      (task) =>
        task.id === visibleReplyError.taskId &&
        task.state === "awaiting_input" &&
        task.pending_input,
    ),
  );
  const refreshQueue = () => {
    clearFeedback();
    tasksQuery.resetPollingWindow();
    // Cancel a slow or hung in-flight request instead of waiting behind it.
    void tasksQuery.refetch({ cancelRefetch: true });
  };

  const actionError =
    actionMutation.error instanceof Error
      ? actionMutation.error.message
      : actionMutation.error
        ? "Could not update the queue task."
        : null;

  return (
    <section
      data-testid="queue-monitor-page"
      data-project-id={projectId}
      aria-labelledby="queue-monitor-heading"
      className="space-y-6"
    >
      <PageHeader
        title="Generation Queue"
        headingId="queue-monitor-heading"
        description="Every generation task of this project: follow progress, answer provider questions, and pause, resume or cancel tasks."
        actions={
          <>
            <AwaitingInputNotifications />
            <Button
              type="button"
              variant="outline"
              onClick={refreshQueue}
              aria-busy={tasksQuery.isFetching}
            >
              <RefreshCw
                className={cn("mr-2 size-4", tasksQuery.isFetching && "animate-spin")}
                aria-hidden="true"
              />
              Refresh
            </Button>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-3" role="group" aria-label="Queue summary">
        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-sm text-muted-foreground">Tasks in project</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{summary?.total_tasks ?? 0}</p>
        </article>
        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-sm text-muted-foreground">Active (queued, running, awaiting input)</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{summary?.active_tasks ?? 0}</p>
        </article>
        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-sm text-muted-foreground">Failed / timed out</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{summary?.failed_tasks ?? 0}</p>
        </article>
      </div>

      <div className="flex flex-col gap-2 sm:max-w-xs">
        <label htmlFor="queue-state-filter" className="text-sm font-medium">
          Filter by state
        </label>
        <select
          id="queue-state-filter"
          value={stateFilter}
          onChange={(event) => {
            const nextFilter = event.target.value as QueueTaskFilter;
            clearFeedback();
            setStateFilter(nextFilter);
            setPageState({ scope: projectId + ":" + nextFilter, offset: 0 });
          }}
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

      {visibleReplyError && !replyErrorInline && (
        <div
          role="alert"
          className={cn(
            "rounded-lg border border-destructive/30 bg-destructive/5 p-4",
            "text-sm text-destructive",
          )}
        >
          Answer for task <span className="break-all font-medium">{visibleReplyError.taskId}</span>{" "}
          was not sent: {visibleReplyError.message}
        </div>
      )}

      {awaitingInputCount > 0 && (
        <div
          role="status"
          data-testid="queue-awaiting-input-banner"
          className={cn(
            "flex flex-col gap-3 rounded-lg border border-warning/50 bg-warning/10 p-4",
            "text-sm sm:flex-row sm:items-center sm:justify-between",
          )}
        >
          <p className="flex items-center gap-2 font-medium">
            <MessageCircleQuestionMark className="size-4 text-warning" aria-hidden="true" />
            {awaitingInputCount === 1
              ? "1 task in this project is waiting for your answer."
              : `${awaitingInputCount} tasks in this project are waiting for your answer.`}
          </p>
          {stateFilter !== "awaiting_input" && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                clearFeedback();
                setStateFilter("awaiting_input");
                setPageState({ scope: projectId + ":awaiting_input", offset: 0 });
              }}
            >
              Show only awaiting input
            </Button>
          )}
        </div>
      )}

      <section aria-label="Queue tasks" className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="font-semibold">Generation tasks</h3>
          <span className="text-xs text-muted-foreground">
            Auto-refresh backs off from 2s to 10s and stops after 30 minutes or when no task is
            active in this project.
          </span>
        </div>
        <QueryState
          isLoading={tasksQuery.isPending || isPageOutOfRange}
          isError={tasksQuery.isError}
          hasData={Boolean(tasksQuery.data)}
          isEmpty={tasks.length === 0 && !isPageOutOfRange}
          loadingMessage="Loading queue tasks..."
          errorMessage={authErrorMessage(tasksQuery.error, activeAdminToken, "queue tasks")}
          emptyTitle={stateFilter === "all" ? "No queue tasks yet" : "No matching queue tasks"}
          emptyDescription={
            stateFilter === "all"
              ? "Generation tasks for this project will appear here when work is queued."
              : "Try another state filter or refresh to check for matching tasks."
          }
          onRetry={refreshQueue}
        >
          <QueueTaskList
            // A new admin session or project starts without pending confirmations.
            key={`${projectId}:${sessionVersion}`}
            tasks={tasks}
            busyTaskIds={pendingTaskIds}
            onAction={handleAction}
            replyError={replyErrorInline ? visibleReplyError : null}
            onReply={handleReply}
          />
        </QueryState>

        {tasksQuery.data && !isPageOutOfRange && (total > QUEUE_TASKS_PAGE_SIZE || offset > 0) && (
          <nav
            aria-label="Queue pagination"
            className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between"
          >
            <p role="status" className="text-sm text-muted-foreground">
              Showing {total === 0 ? 0 : offset + 1}–{offset + tasks.length} of {total} matching
              tasks
            </p>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setPageOffset(offset - QUEUE_TASKS_PAGE_SIZE)}
              >
                Previous page
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!tasksQuery.data.has_more}
                onClick={() => setPageOffset(offset + QUEUE_TASKS_PAGE_SIZE)}
              >
                Next page
              </Button>
            </div>
          </nav>
        )}
      </section>
    </section>
  );
}

export default QueueMonitorPage;

import { CircleAlert, LoaderCircle, MessageCircleQuestion, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { QueueMonitorLink } from "@/features/shot-board/components/queue-monitor-link";
import type { GenerationActivity } from "@/features/shot-board/queue-status";

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

interface GenerationActivityBarProps {
  projectId: string;
  /** Null until the project's tasks have loaded (or when they failed to load). */
  activity: GenerationActivity | null;
  /** Tasks are still active but polling stopped (time window over or a non-retryable error). */
  autoRefreshStopped: boolean;
  loadError: string | null;
  onRefresh: () => void;
}

/**
 * Live generation summary above the shots, so progress is visible without leaving the board.
 * The live region is always mounted (only its content changes) so screen readers announce
 * updates; it is empty when nothing is running, waiting for an answer, or failed last.
 */
export function GenerationActivityBar({
  projectId,
  activity,
  autoRefreshStopped,
  loadError,
  onRefresh,
}: GenerationActivityBarProps) {
  const working = activity ? activity.active - activity.awaitingInput : 0;
  const hasActivity =
    activity !== null && (working > 0 || activity.awaitingInput > 0 || activity.failedShots > 0);
  const visible = hasActivity || loadError !== null;

  return (
    <div role="status" data-testid="generation-activity-region">
      {visible && (
        <div
          data-testid="generation-activity"
          className="flex flex-col gap-2 rounded-xl border border-border bg-secondary/40 px-4 py-2.5 text-sm sm:flex-row sm:items-center sm:justify-between"
        >
          <ul className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {loadError && !activity && (
              <li className="inline-flex items-center gap-1.5 text-destructive">
                <CircleAlert className="size-4" aria-hidden="true" />
                Could not load generation status. {loadError}
              </li>
            )}
            {working > 0 && (
              <li className="inline-flex items-center gap-1.5">
                {!autoRefreshStopped && (
                  <LoaderCircle className="size-4 animate-spin text-primary" aria-hidden="true" />
                )}
                {plural(working, "task")} queued or running
              </li>
            )}
            {activity && activity.awaitingInput > 0 && (
              <li className="inline-flex items-center gap-1.5 font-medium text-warning">
                <MessageCircleQuestion className="size-4" aria-hidden="true" />
                {plural(activity.awaitingInput, "task")} waiting for your answer
              </li>
            )}
            {activity && activity.failedShots > 0 && (
              <li className="inline-flex items-center gap-1.5 text-destructive">
                <CircleAlert className="size-4" aria-hidden="true" />
                {activity.failedShots === 1
                  ? "1 shot's latest generation failed"
                  : `${activity.failedShots} shots' latest generation failed`}
              </li>
            )}
            {autoRefreshStopped && (
              <li className="text-xs text-muted-foreground" data-testid="auto-refresh-stopped">
                Auto-refresh stopped; these counts may be out of date.
              </li>
            )}
          </ul>
          <div className="flex flex-wrap items-center gap-3">
            {(autoRefreshStopped || (loadError && !activity)) && (
              <Button type="button" size="sm" variant="outline" onClick={onRefresh}>
                <RefreshCw className="size-4" aria-hidden="true" />
                Refresh
              </Button>
            )}
            <QueueMonitorLink projectId={projectId} />
          </div>
        </div>
      )}
    </div>
  );
}

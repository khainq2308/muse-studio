import { useQuery } from "@tanstack/react-query";
import { useCallback, useRef } from "react";
import { getQueueRefetchInterval, type QueueTaskPage } from "@/entities/task/state";
import { apiGet } from "@/lib/api/client";
import { isRetryableApiError } from "@/lib/api/errors";
import { queryKeys } from "@/lib/api/queryKeys";
import { useSessionStore } from "@/lib/stores/session-store";

/** Backend maximum page size of `GET /api/v1/queue/tasks` (`limit` le=100). */
export const RECENT_TASKS_LIMIT = 100;

/**
 * The newest generation tasks of a project (`GET /api/v1/queue/tasks`, newest first), for
 * views that show each shot's latest task. Polls with the Generation Queue's policy while the
 * project has active tasks (bounded window, backoff). The window restarts whenever the active
 * count grows (new work from any source) or on `resetPollingWindow`. `isPolling` tells callers
 * whether the data is still being kept fresh, so dependent polling can stop with it.
 */
export function useRecentProjectTasks(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const pollingKey = `${sessionVersion}:${projectId}`;
  const pollingWindow = useRef({ key: pollingKey, startedAt: Date.now(), lastActive: 0 });
  if (pollingWindow.current.key !== pollingKey) {
    pollingWindow.current = { key: pollingKey, startedAt: Date.now(), lastActive: 0 };
  }
  const resetPollingWindow = useCallback(() => {
    pollingWindow.current.startedAt = Date.now();
  }, []);

  const intervalFor = (
    data: QueueTaskPage | undefined,
    status: "pending" | "error" | "success",
    error: unknown,
  ) =>
    getQueueRefetchInterval(
      data?.items.map((task) => task.state),
      Date.now() - pollingWindow.current.startedAt,
      status,
      data?.summary.active_tasks,
      status === "error" && isRetryableApiError(error),
    );

  const query = useQuery({
    queryKey: queryKeys.recentProjectTasks(sessionVersion, projectId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/queue/tasks", {
        signal,
        query: { project_id: projectId, limit: RECENT_TASKS_LIMIT, offset: 0 },
      }),
    refetchInterval: (query) => {
      const active = query.state.data?.summary.active_tasks ?? 0;
      if (active > pollingWindow.current.lastActive) resetPollingWindow();
      pollingWindow.current.lastActive = active;
      return intervalFor(query.state.data, query.state.status, query.state.error);
    },
    refetchIntervalInBackground: false,
  });

  const isPolling = intervalFor(query.data, query.status, query.error) !== false;
  return { ...query, isPolling, resetPollingWindow };
}

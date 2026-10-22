import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";
import { apiGet, apiPost } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import {
  QUEUE_TASKS_PAGE_SIZE,
  getQueueRefetchInterval,
  type QueueTaskAction,
  type QueueTaskFilter,
} from "@/features/queue-monitor/state";
import { useSessionStore } from "@/lib/stores/session-store";

export function useQueueTasks(
  projectId: string,
  stateFilter: QueueTaskFilter,
  offset: number,
) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const pollingKey = [sessionVersion, projectId, stateFilter, offset].join(":");
  const pollingWindow = useRef({ key: pollingKey, startedAt: Date.now() });
  if (pollingWindow.current.key !== pollingKey) {
    pollingWindow.current = { key: pollingKey, startedAt: Date.now() };
  }
  const resetPollingWindow = useCallback(() => {
    pollingWindow.current.startedAt = Date.now();
  }, []);

  const query = useQuery({
    queryKey: queryKeys.queueTasks(sessionVersion, projectId, stateFilter, offset),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/queue/tasks", {
        signal,
        query: {
          project_id: projectId,
          ...(stateFilter === "all" ? {} : { state: stateFilter }),
          limit: QUEUE_TASKS_PAGE_SIZE,
          offset,
        },
      }),
    refetchInterval: (query) =>
      getQueueRefetchInterval(
        query.state.data?.items.map((task) => task.state),
        Date.now() - pollingWindow.current.startedAt,
        query.state.status,
        query.state.data?.summary.active_tasks,
      ),
    refetchIntervalInBackground: false,
  });

  return { ...query, resetPollingWindow };
}

export interface QueueTaskActionVariables {
  taskId: string;
  action: QueueTaskAction;
}

const ACTION_PATHS = {
  pause: "/api/v1/queue/tasks/{task_id}/pause",
  resume: "/api/v1/queue/tasks/{task_id}/resume",
  cancel: "/api/v1/queue/tasks/{task_id}/cancel",
} as const;

export function useQueueTaskAction(projectId: string, onActionSettled?: () => void) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ taskId, action }: QueueTaskActionVariables) =>
      apiPost(ACTION_PATHS[action], undefined, {
        pathParams: { task_id: taskId },
      }),
    onSettled: () => {
      onActionSettled?.();
      return queryClient.invalidateQueries({
        queryKey: queryKeys.queueTasksPrefix(sessionVersion, projectId),
      });
    },
  });
}

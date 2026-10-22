import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";
import { apiGet, apiPost } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";
import {
  getQueueRefetchInterval,
  type QueueTask,
  type QueueTaskAction,
  type QueueTaskFilter,
} from "@/features/queue-monitor/state";

export function useQueueTasks(projectId: string, stateFilter: QueueTaskFilter) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const pollingKey = `${sessionVersion}:${projectId}:${stateFilter}`;
  const pollingWindow = useRef({ key: pollingKey, startedAt: Date.now() });
  if (pollingWindow.current.key !== pollingKey) {
    pollingWindow.current = { key: pollingKey, startedAt: Date.now() };
  }
  const resetPollingWindow = useCallback(() => {
    pollingWindow.current.startedAt = Date.now();
  }, []);

  const query = useQuery({
    queryKey: queryKeys.queueTasks(sessionVersion, projectId, stateFilter),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/queue/tasks", {
        signal,
        query: {
          project_id: projectId,
          ...(stateFilter === "all" ? {} : { state: stateFilter }),
        },
      }),
    refetchInterval: (query) =>
      getQueueRefetchInterval(
        query.state.data?.map((task) => task.state),
        Date.now() - pollingWindow.current.startedAt,
        query.state.status,
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

export function useQueueTaskAction(projectId: string, onActionSuccess?: () => void) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();

  return useMutation<QueueTask, Error, QueueTaskActionVariables>({
    mutationFn: ({ taskId, action }) =>
      apiPost<components["schemas"]["QueueTaskOut"]>(ACTION_PATHS[action], undefined, {
        pathParams: { task_id: taskId },
      }),
    onSuccess: () => {
      onActionSuccess?.();
      return queryClient.invalidateQueries({
        queryKey: queryKeys.queueTasksPrefix(sessionVersion, projectId),
      });
    },
  });
}

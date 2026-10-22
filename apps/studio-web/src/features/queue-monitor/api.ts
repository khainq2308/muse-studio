import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
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
  const [pollingStartedAt] = useState(() => Date.now());

  return useQuery({
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
        Date.now() - pollingStartedAt,
      ),
    refetchIntervalInBackground: false,
  });
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

export function useQueueTaskAction(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();

  return useMutation<QueueTask, Error, QueueTaskActionVariables>({
    mutationFn: ({ taskId, action }) =>
      apiPost<components["schemas"]["QueueTaskOut"]>(ACTION_PATHS[action], undefined, {
        pathParams: { task_id: taskId },
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.queueTasksPrefix(sessionVersion, projectId),
      }),
  });
}

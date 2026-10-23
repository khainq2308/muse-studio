import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef } from "react";
import { apiGet, apiPost } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import {
  QUEUE_TASKS_PAGE_SIZE,
  getQueueRefetchInterval,
  type QueueTaskAction,
  type QueueTaskFilter,
  type QueueTaskReply,
} from "@/features/queue-monitor/state";
import { isRetryableApiError } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

export function useQueueTasks(projectId: string, stateFilter: QueueTaskFilter, offset: number) {
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
        stateFilter === "all" ||
          stateFilter === "queued" ||
          stateFilter === "running" ||
          stateFilter === "awaiting_input"
          ? query.state.data?.summary.active_tasks
          : undefined,
        query.state.status === "error" && isRetryableApiError(query.state.error),
      ),
    refetchIntervalInBackground: false,
  });

  return { ...query, resetPollingWindow };
}

export interface QueueTaskActionVariables {
  taskId: string;
  action: QueueTaskAction;
}

/** Scope captured when the mutation starts, so settling invalidates the originating project. */
interface QueueTaskActionScope extends QueueTaskActionVariables {
  projectId: string;
  sessionVersion: number;
}

const ACTION_PATHS = {
  pause: "/api/v1/queue/tasks/{task_id}/pause",
  resume: "/api/v1/queue/tasks/{task_id}/resume",
  cancel: "/api/v1/queue/tasks/{task_id}/cancel",
} as const;

export function useQueueTaskAction(projectId: string, onActionSettled?: () => void) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();

  const mutationKey = queryKeys.queueTaskActions(sessionVersion, projectId);
  const mutation = useMutation({
    mutationKey,
    mutationFn: ({ taskId, action }: QueueTaskActionScope) =>
      apiPost(ACTION_PATHS[action], undefined, {
        pathParams: { task_id: taskId },
      }),
    onSettled: (_data, _error, variables) => {
      onActionSettled?.();
      return queryClient.invalidateQueries({
        queryKey: queryKeys.queueTasksPrefix(variables.sessionVersion, variables.projectId),
      });
    },
  });

  // useMutation only tracks its latest call; read every in-flight action for this project so
  // concurrent actions on different tasks each keep their own busy state.
  const pendingTaskIdList = useMutationState({
    filters: { mutationKey, status: "pending" },
    select: (pending) => (pending.state.variables as QueueTaskActionScope | undefined)?.taskId,
  });
  const pendingTaskIdKey = pendingTaskIdList.filter(Boolean).sort().join("\u0000");
  const pendingTaskIds = useMemo<ReadonlySet<string>>(
    () => new Set(pendingTaskIdKey ? pendingTaskIdKey.split("\u0000") : []),
    [pendingTaskIdKey],
  );

  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (variables: QueueTaskActionVariables) => rawMutate({ ...variables, projectId, sessionVersion }),
    [projectId, rawMutate, sessionVersion],
  );

  return { ...mutation, mutate, pendingTaskIds };
}

export interface QueueTaskReplyVariables {
  taskId: string;
  reply: QueueTaskReply;
}

interface QueueTaskReplyScope extends QueueTaskReplyVariables {
  projectId: string;
  sessionVersion: number;
}

/**
 * Answer the question of an AWAITING_INPUT task. The queue is refreshed whether the reply
 * succeeds or fails: a 409 means the task already moved on and the list must show its new state.
 */
export function useQueueTaskReply(projectId: string, onReplySettled?: () => void) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();

  const mutationKey = queryKeys.queueTaskReplies(sessionVersion, projectId);
  const mutation = useMutation({
    mutationKey,
    mutationFn: ({ taskId, reply }: QueueTaskReplyScope) =>
      apiPost("/api/v1/queue/tasks/{task_id}/reply", reply, {
        pathParams: { task_id: taskId },
      }),
    onSettled: (_data, _error, variables) => {
      onReplySettled?.();
      return queryClient.invalidateQueries({
        queryKey: queryKeys.queueTasksPrefix(variables.sessionVersion, variables.projectId),
      });
    },
  });

  const pendingTaskIdList = useMutationState({
    filters: { mutationKey, status: "pending" },
    select: (pending) => (pending.state.variables as QueueTaskReplyScope | undefined)?.taskId,
  });
  const pendingTaskIdKey = pendingTaskIdList.filter(Boolean).sort().join("\u0000");
  const pendingTaskIds = useMemo<ReadonlySet<string>>(
    () => new Set(pendingTaskIdKey ? pendingTaskIdKey.split("\u0000") : []),
    [pendingTaskIdKey],
  );

  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      variables: QueueTaskReplyVariables,
      callbacks?: { onSuccess?: () => void; onError?: (error: unknown) => void },
    ) =>
      rawMutate(
        { ...variables, projectId, sessionVersion },
        {
          onSuccess: () => callbacks?.onSuccess?.(),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [projectId, rawMutate, sessionVersion],
  );

  return { ...mutation, mutate, pendingTaskIds };
}

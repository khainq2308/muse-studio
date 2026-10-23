import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { apiGet, apiPatch, apiPost, type ApiJsonResponse } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type {
  BatchLaunchRequest,
  EnqueueTaskRequest,
  ShotUpdate,
} from "@/features/shot-board/state";
import { useSessionStore } from "@/lib/stores/session-store";

export const SHOT_PATCH_PATH = "/api/v1/projects/{project_id}/shots/{shot_id}";
type ShotPatchResponse = ApiJsonResponse<typeof SHOT_PATCH_PATH, "patch">;

export function useProjectShots(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projectShots(sessionVersion, projectId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/shots", {
        signal,
        pathParams: { project_id: projectId },
      }),
  });
}

/** Project and admin session captured when a mutation starts, so settling refreshes them. */
interface MutationScope {
  projectId: string;
  sessionVersion: number;
}

export interface ShotUpdateVariables {
  shotId: string;
  update: ShotUpdate;
}

/**
 * PATCH a shot. The backend recompiles every shot of the project after a change (prompt
 * hashes and lint of neighbours can move), so the whole project shot list is invalidated.
 */
export function useUpdateShot(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: ({
      shotId,
      update,
      projectId: scopeProjectId,
    }: ShotUpdateVariables & MutationScope) =>
      apiPatch<ShotPatchResponse>(SHOT_PATCH_PATH, update, {
        pathParams: { project_id: scopeProjectId, shot_id: shotId },
      }),
    onSettled: (_data, _error, variables) =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.projectShots(variables.sessionVersion, variables.projectId),
      }),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (variables: ShotUpdateVariables) => rawMutate({ ...variables, projectId, sessionVersion }),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

function invalidateGenerationViews(
  queryClient: ReturnType<typeof useQueryClient>,
  scope: MutationScope,
) {
  return Promise.all([
    queryClient.invalidateQueries({
      queryKey: queryKeys.projectShots(scope.sessionVersion, scope.projectId),
    }),
    queryClient.invalidateQueries({
      queryKey: queryKeys.queueTasksPrefix(scope.sessionVersion, scope.projectId),
    }),
  ]);
}

/**
 * Enqueue one shot (`POST /api/v1/queue/tasks`). The request omits `idempotency_key` so the
 * backend deduplicates by content; `created_new: false` in the response means an existing
 * task was returned.
 */
export function useEnqueueShotTask(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.shotBoardGeneration(sessionVersion, projectId),
    mutationFn: ({ request }: { request: EnqueueTaskRequest } & MutationScope) =>
      apiPost("/api/v1/queue/tasks", request),
    onSettled: (_data, _error, variables) => invalidateGenerationViews(queryClient, variables),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (request: EnqueueTaskRequest) => rawMutate({ request, projectId, sessionVersion }),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

/** Launch a batch render (`POST /api/v1/projects/{project_id}/batch/launch`). */
export function useLaunchBatch(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.shotBoardGeneration(sessionVersion, projectId),
    mutationFn: ({
      request,
      projectId: scopeProjectId,
    }: { request: BatchLaunchRequest } & MutationScope) =>
      apiPost("/api/v1/projects/{project_id}/batch/launch", request, {
        pathParams: { project_id: scopeProjectId },
      }),
    onSettled: (_data, _error, variables) => invalidateGenerationViews(queryClient, variables),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (request: BatchLaunchRequest) => rawMutate({ request, projectId, sessionVersion }),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

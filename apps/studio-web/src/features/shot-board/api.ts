import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { apiGet, apiPatch, apiPost, type ApiJsonResponse } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { useAssetMedia } from "@/lib/media/use-asset-media";
import type {
  BatchLaunchRequest,
  EnqueueTaskRequest,
  ShotUpdate,
} from "@/features/shot-board/state";
import {
  REGENERATE_REQUEST_TIMEOUT_MS,
  type ContinuityFlagRequest,
} from "@/features/shot-board/takes";
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

/** Takes of one shot (`GET /api/v1/queue/shots/{shot_id}/takes`), loaded when review opens. */
export function useShotTakes(projectId: string, shotId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.shotTakes(sessionVersion, projectId, shotId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/queue/shots/{shot_id}/takes", { signal, pathParams: { shot_id: shotId } }),
  });
}

/** Server-side A/B comparison of two takes; disabled until two distinct takes are chosen. */
export function useCompareTakes(
  projectId: string,
  shotId: string,
  pair: readonly [string, string] | null,
) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const [takeAId = "", takeBId = ""] = pair ?? [];
  return useQuery({
    queryKey: queryKeys.shotTakeCompare(sessionVersion, projectId, shotId, takeAId, takeBId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/shots/{shot_id}/compare-takes", {
        signal,
        pathParams: { project_id: projectId, shot_id: shotId },
        query: { take_a_id: takeAId, take_b_id: takeBId },
      }),
    enabled: Boolean(pair) && takeAId !== takeBId,
  });
}

export { useAssetMedia };

function invalidateTakeViews(queryClient: ReturnType<typeof useQueryClient>, scope: MutationScope) {
  return Promise.all([
    queryClient.invalidateQueries({
      queryKey: queryKeys.projectShots(scope.sessionVersion, scope.projectId),
    }),
    queryClient.invalidateQueries({
      queryKey: queryKeys.shotTakesPrefix(scope.sessionVersion, scope.projectId),
    }),
    queryClient.invalidateQueries({
      queryKey: queryKeys.shotTakeComparePrefix(scope.sessionVersion, scope.projectId),
    }),
  ]);
}

export interface SelectTakeVariables {
  shotId: string;
  takeId: string;
}

/**
 * Select a take (`POST /api/v1/queue/shots/{shot_id}/takes/{take_id}/select`). The shot's
 * last frame follows the selected take and frame-chained downstream shots may be re-pointed,
 * so the whole project shot list and every take view are invalidated.
 */
export function useSelectTake(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.shotTakeActions(sessionVersion, projectId),
    mutationFn: ({ shotId, takeId }: SelectTakeVariables & MutationScope) =>
      apiPost("/api/v1/queue/shots/{shot_id}/takes/{take_id}/select", undefined, {
        pathParams: { shot_id: shotId, take_id: takeId },
      }),
    onSettled: (_data, _error, variables) => invalidateTakeViews(queryClient, variables),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (variables: SelectTakeVariables) => rawMutate({ ...variables, projectId, sessionVersion }),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export interface FlagTakeVariables {
  shotId: string;
  takeId: string;
  request: ContinuityFlagRequest;
}

/**
 * Flag (or clear) continuity on a take, optionally regenerating the shot now
 * (`POST /api/v1/projects/{project_id}/shots/{shot_id}/takes/{take_id}/flag-continuity`).
 * A regeneration enqueues a new task, so queue views are refreshed too.
 */
export function useFlagTakeContinuity(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.shotTakeActions(sessionVersion, projectId),
    mutationFn: ({
      shotId,
      takeId,
      request,
      projectId: scopeProjectId,
    }: FlagTakeVariables & MutationScope) =>
      apiPost(
        "/api/v1/projects/{project_id}/shots/{shot_id}/takes/{take_id}/flag-continuity",
        request,
        {
          pathParams: { project_id: scopeProjectId, shot_id: shotId, take_id: takeId },
          ...(request.regenerate_now ? { timeoutMs: REGENERATE_REQUEST_TIMEOUT_MS } : {}),
        },
      ),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        invalidateTakeViews(queryClient, variables),
        queryClient.invalidateQueries({
          queryKey: queryKeys.queueTasksPrefix(variables.sessionVersion, variables.projectId),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (variables: FlagTakeVariables) => rawMutate({ ...variables, projectId, sessionVersion }),
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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import type {
  BibleEntryCreateRequest,
  ScriptImportRequest,
  ShotExportFormat,
  UploadedAsset,
} from "@/features/script-studio/state";
import { apiGet, apiPost, apiRequest, downloadFile } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { useAssetMedia } from "@/lib/media/use-asset-media";
import { useSessionStore } from "@/lib/stores/session-store";

export const ASSET_UPLOAD_PATH = "/api/v1/assets/upload";
export const SHOTS_EXPORT_PATH = "/api/v1/projects/{project_id}/shots/export";

interface MutationScope {
  projectId: string;
  sessionVersion: number;
}

/** Story Bible entries for one project (`GET /api/v1/projects/{project_id}/story-bible`). */
export function useStoryBible(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.storyBible(sessionVersion, projectId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/story-bible", {
        signal,
        pathParams: { project_id: projectId },
      }),
  });
}

/** Project shot list (`GET /api/v1/projects/{project_id}/shots`). */
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

function invalidateStoryBibleAndShots(
  queryClient: ReturnType<typeof useQueryClient>,
  scope: MutationScope,
) {
  return Promise.all([
    queryClient.invalidateQueries({
      queryKey: queryKeys.storyBible(scope.sessionVersion, scope.projectId),
    }),
    queryClient.invalidateQueries({
      queryKey: queryKeys.projectShots(scope.sessionVersion, scope.projectId),
    }),
  ]);
}

/**
 * Create or update a Story Bible entry by `(project_id, code_name)`
 * (`POST /api/v1/projects/{project_id}/story-bible`). The backend atomically recompiles every
 * shot of the project, so both the Story Bible and the project shot list are invalidated.
 */
export function useUpsertStoryBibleEntry(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.scriptStudioActions(sessionVersion, projectId),
    mutationFn: ({
      request,
      projectId: scopeProjectId,
    }: { request: BibleEntryCreateRequest; editorKey: string } & MutationScope) =>
      apiPost("/api/v1/projects/{project_id}/story-bible", request, {
        pathParams: { project_id: scopeProjectId },
      }),
    onSettled: (_data, _error, variables) => invalidateStoryBibleAndShots(queryClient, variables),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (request: BibleEntryCreateRequest, options?: { editorKey?: string }) =>
      rawMutate({
        request,
        editorKey: options?.editorKey ?? request.code_name,
        projectId,
        sessionVersion,
      }),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

function invalidatePipelineAfterImport(
  queryClient: ReturnType<typeof useQueryClient>,
  scope: MutationScope,
) {
  return Promise.all([
    queryClient.invalidateQueries({
      queryKey: queryKeys.storyBible(scope.sessionVersion, scope.projectId),
    }),
    queryClient.invalidateQueries({
      queryKey: queryKeys.projectShots(scope.sessionVersion, scope.projectId),
    }),
    queryClient.invalidateQueries({
      queryKey: queryKeys.shotTakesPrefix(scope.sessionVersion, scope.projectId),
    }),
    queryClient.invalidateQueries({
      queryKey: queryKeys.shotTakeComparePrefix(scope.sessionVersion, scope.projectId),
    }),
    queryClient.invalidateQueries({
      queryKey: queryKeys.queueTasksPrefix(scope.sessionVersion, scope.projectId),
    }),
    queryClient.invalidateQueries({
      queryKey: queryKeys.projects(scope.sessionVersion),
    }),
  ]);
}

/**
 * Import and analyze a script (`POST /api/v1/projects/{project_id}/scripts/import`).
 * When `auto_apply_proposal` is true, the backend replaces the Story Bible, scenes, and shots
 * (and, with `replace_existing`, existing takes and finished tasks), then recompiles prompts.
 */
export function useImportScript(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.scriptStudioActions(sessionVersion, projectId),
    mutationFn: ({
      request,
      projectId: scopeProjectId,
    }: { request: ScriptImportRequest } & MutationScope) =>
      apiPost("/api/v1/projects/{project_id}/scripts/import", request, {
        pathParams: { project_id: scopeProjectId },
      }),
    onSettled: (_data, _error, variables) => {
      if (!variables) return;
      if (variables.request.auto_apply_proposal) {
        return invalidatePipelineAfterImport(queryClient, variables);
      }
      return queryClient.invalidateQueries({
        queryKey: queryKeys.projects(variables.sessionVersion),
      });
    },
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (request: ScriptImportRequest) => rawMutate({ request, projectId, sessionVersion }),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export interface UploadReferenceAssetVariables {
  file: File;
  isLockedReference?: boolean;
  editorKey?: string;
}

/**
 * Upload a reference image asset (`POST /api/v1/assets/upload` as `multipart/form-data`).
 * Returns the persisted `AssetOut` record so its `id` can be attached to `reference_asset_ids`.
 */
export function useUploadReferenceAsset(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.scriptStudioActions(sessionVersion, projectId),
    mutationFn: ({
      file,
      isLockedReference = true,
      projectId: scopeProjectId,
    }: UploadReferenceAssetVariables & { editorKey: string } & MutationScope) => {
      const formData = new FormData();
      formData.append("project_id", scopeProjectId);
      formData.append("asset_type", "reference");
      formData.append("is_locked_reference", String(Boolean(isLockedReference)));
      formData.append("file", file);
      return apiRequest<UploadedAsset>(ASSET_UPLOAD_PATH, {
        method: "POST",
        body: formData,
      });
    },
    onSettled: (_data, _error, variables) =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.projects(variables.sessionVersion),
      }),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (variables: UploadReferenceAssetVariables) =>
      rawMutate({
        ...variables,
        editorKey: variables.editorKey ?? "__new__",
        projectId,
        sessionVersion,
      }),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

/**
 * Authenticated bytes of one reference asset (`GET /api/v1/assets/{asset_id}/download`).
 * Shares `queryKeys.assetMedia` with the Shot Board so downloaded thumbnails are reused.
 */
export const useReferenceAssetMedia = useAssetMedia;

/**
 * Download the project shot list as JSON or CSV
 * (`GET /api/v1/projects/{project_id}/shots/export?format=json|csv`).
 */
export function useExportProjectShots(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const mutation = useMutation({
    mutationKey: queryKeys.scriptStudioActions(sessionVersion, projectId),
    mutationFn: ({
      format,
      projectId: scopeProjectId,
    }: { format: ShotExportFormat } & MutationScope) =>
      downloadFile(SHOTS_EXPORT_PATH, `${scopeProjectId}-shots.${format}`, {
        pathParams: { project_id: scopeProjectId },
        query: { format },
      }),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (format: ShotExportFormat) => rawMutate({ format, projectId, sessionVersion }),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

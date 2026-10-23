import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { apiGet, apiPost, downloadFile } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

export type TimelineSequence = components["schemas"]["TimelineSequenceOut"];
export type TimelineAssembleRequest = components["schemas"]["TimelineAssembleIn"];
export type TimelineAssembleResponse = components["schemas"]["TimelineAssembleOut"];
export type ExportLaunchRequest = components["schemas"]["ExportLaunchIn"];
export type ExportLaunchResponse = components["schemas"]["ExportLaunchOut"];
export type ExportJobStatus = components["schemas"]["ExportJobStatusOut"];

export function useProjectTimeline(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projectTimeline(sessionVersion, projectId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/timeline", {
        signal,
        pathParams: { project_id: projectId },
      }),
    retry: false,
  });
}

export function useAssembleTimeline(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.timelineActions(sessionVersion, projectId),
    mutationFn: ({
      request,
      projectId: scopeProjectId,
    }: {
      request: TimelineAssembleRequest;
      projectId: string;
      sessionVersion: number;
    }) =>
      apiPost("/api/v1/projects/{project_id}/timeline/assemble", request, {
        pathParams: { project_id: scopeProjectId },
      }),
    onSuccess: (data, variables) => {
      // The new sequence is the latest one; show it right away (the refetch below confirms it),
      // so a saved clip draft is replaced by the server's result instead of lingering as dirty.
      const latest: TimelineSequence = {
        sequence_id: data.sequence_id,
        name: data.name,
        aspect_ratio: data.aspect_ratio,
        fps: data.fps,
        resolution: data.resolution,
        total_duration_seconds: data.total_duration_seconds,
        warnings: data.warnings,
        manifest: data.manifest,
      };
      queryClient.setQueryData(
        queryKeys.projectTimeline(variables.sessionVersion, variables.projectId),
        latest,
      );
    },
    onSettled: (_data, _error, variables) =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.projectTimeline(variables.sessionVersion, variables.projectId),
      }),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      request: TimelineAssembleRequest,
      callbacks?: {
        onSuccess?: (result: TimelineAssembleResponse) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { request, projectId, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useLaunchTimelineExport(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const mutation = useMutation({
    mutationKey: queryKeys.timelineActions(sessionVersion, projectId),
    mutationFn: ({
      request,
      projectId: scopeProjectId,
    }: {
      request: ExportLaunchRequest;
      projectId: string;
    }) =>
      apiPost("/api/v1/projects/{project_id}/timeline/exports", request, {
        pathParams: { project_id: scopeProjectId },
      }),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      request: ExportLaunchRequest,
      callbacks?: {
        onSuccess?: (result: ExportLaunchResponse) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { request, projectId },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [projectId, rawMutate],
  );
  return { ...mutation, mutate };
}

export const EXPORT_STATUS_POLL_INTERVAL_MS = 2500;

export function useTimelineExportStatus(projectId: string, exportId: string | null) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.timelineExport(sessionVersion, projectId, exportId ?? ""),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/timeline/exports/{export_id}", {
        signal,
        pathParams: { project_id: projectId, export_id: exportId! },
      }),
    enabled: Boolean(exportId),
    refetchInterval: (query) => {
      // A failed refetch keeps the last (possibly "running") data; stop polling instead of
      // retrying forever every 2.5s. The page surfaces the error and the user can retry.
      if (query.state.status === "error") return false;
      const state = query.state.data?.state;
      return state === "queued" || state === "running" ? EXPORT_STATUS_POLL_INTERVAL_MS : false;
    },
    refetchIntervalInBackground: false,
  });
}

export function useDownloadExportedAsset(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const mutation = useMutation({
    mutationKey: queryKeys.timelineActions(sessionVersion, projectId),
    mutationFn: ({ assetId, filename }: { assetId: string; filename: string }) =>
      downloadFile("/api/v1/assets/{asset_id}/download", filename, {
        pathParams: { asset_id: assetId },
      }),
  });
  return mutation;
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import {
  LONG_REQUEST_TIMEOUT_MS,
  apiDelete,
  apiGet,
  apiPost,
  apiRequest,
  downloadFile,
} from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

export type BackupItem = components["schemas"]["BackupOut"];
export type BackupRestoreResult = components["schemas"]["BackupRestoreOut"];
export type MetricsSummary = components["schemas"]["MetricsSummaryOut"];
export type OrphanScanResult = components["schemas"]["OrphanScanOut"];
export type OrphanCleanupResult = components["schemas"]["OrphanCleanupOut"];
export type OrphanFile = components["schemas"]["OrphanFileOut"];
export type StructuredLogItem = components["schemas"]["StructuredLogOut"];
export type SystemDiagnostics = components["schemas"]["SystemDiagnosticsOut"];
export type UploadedAssetItem = components["schemas"]["AssetOut"];

export function useProjectBackups(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projectBackups(sessionVersion, projectId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/backups", {
        signal,
        pathParams: { project_id: projectId },
      }),
  });
}

export function useMetricsSummary() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.metricsSummary(sessionVersion),
    queryFn: ({ signal }) => apiGet("/api/v1/metrics/summary", { signal }),
  });
}

export function useSystemLogs(projectId?: string, level = "all") {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.systemLogs(sessionVersion, projectId ?? "all", level),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/system/logs", {
        signal,
        query: {
          ...(projectId ? { project_id: projectId } : {}),
          ...(level !== "all" ? { level } : {}),
          limit: 25,
        },
      }),
  });
}

export function useCreateProjectBackup(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, projectId),
    mutationFn: ({ projectId: scopeProjectId }: { projectId: string; sessionVersion: number }) =>
      // Snapshotting DB rows and media into a ZIP can far exceed the default JSON timeout.
      apiPost("/api/v1/projects/{project_id}/backup", undefined, {
        pathParams: { project_id: scopeProjectId },
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      }),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectBackups(variables.sessionVersion, variables.projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.metricsSummary(variables.sessionVersion),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (callbacks?: {
      onSuccess?: (backup: BackupItem) => void;
      onError?: (error: unknown) => void;
    }) =>
      rawMutate(
        { projectId, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useDownloadBackup(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, projectId),
    mutationFn: ({ backupId, filename }: { backupId: string; filename: string }) =>
      downloadFile("/api/v1/backups/{backup_id}/download", filename, {
        pathParams: { backup_id: backupId },
      }),
  });
}

export function useDeleteBackup(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, projectId),
    mutationFn: ({ backupId }: { backupId: string; projectId: string; sessionVersion: number }) =>
      apiDelete<null>("/api/v1/backups/{backup_id}", {
        pathParams: { backup_id: backupId },
      }),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectBackups(variables.sessionVersion, variables.projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.metricsSummary(variables.sessionVersion),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (backupId: string, callbacks?: { onSuccess?: () => void }) =>
      rawMutate(
        { backupId, projectId, sessionVersion },
        { onSuccess: () => callbacks?.onSuccess?.() },
      ),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useRestoreBackupZip(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, projectId),
    mutationFn: ({ file }: { file: File }) => {
      const formData = new FormData();
      formData.append("file", file);
      return apiRequest<BackupRestoreResult>("/api/v1/backups/restore", {
        method: "POST",
        body: formData,
      });
    },
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.projects(sessionVersion),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.metricsSummary(sessionVersion),
        }),
      ]),
  });
}

export function useScanOrphans(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, projectId),
    mutationFn: ({ retentionHours }: { retentionHours: number }) =>
      apiPost(
        "/api/v1/maintenance/orphans/scan",
        { retention_hours: retentionHours },
        { timeoutMs: LONG_REQUEST_TIMEOUT_MS },
      ),
  });
}

export function useCleanupOrphans(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, projectId),
    mutationFn: ({ dryRun, retentionHours }: { dryRun: boolean; retentionHours: number }) =>
      apiPost(
        "/api/v1/maintenance/orphans/cleanup",
        { dry_run: dryRun, retention_hours: retentionHours },
        { timeoutMs: LONG_REQUEST_TIMEOUT_MS },
      ),
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.metricsSummary(sessionVersion),
      }),
  });
}

export function useUploadProjectAsset(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, projectId),
    mutationFn: ({
      file,
      assetType,
      isLockedReference,
    }: {
      file: File;
      assetType: string;
      isLockedReference: boolean;
    }) => {
      const formData = new FormData();
      formData.append("project_id", projectId);
      formData.append("asset_type", assetType);
      formData.append("is_locked_reference", String(isLockedReference));
      formData.append("file", file);
      return apiRequest<UploadedAssetItem>("/api/v1/assets/upload", {
        method: "POST",
        body: formData,
      });
    },
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.projects(sessionVersion),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.metricsSummary(sessionVersion),
        }),
      ]),
  });
}

export function useDeleteProjectAsset(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, projectId),
    mutationFn: ({ assetId, force }: { assetId: string; force: boolean }) =>
      apiDelete<null>("/api/v1/assets/{asset_id}", {
        pathParams: { asset_id: assetId },
        query: { force },
      }),
    // A (forced) delete can detach the asset from shots, takes, Story Bible entries and timeline
    // clips; refresh every project view that may still reference it.
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.projects(sessionVersion) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.metricsSummary(sessionVersion) }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectShots(sessionVersion, projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.shotTakesPrefix(sessionVersion, projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.shotTakeComparePrefix(sessionVersion, projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.storyBible(sessionVersion, projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectTimeline(sessionVersion, projectId),
        }),
        // queryKeys has no asset-media prefix helper; match its ["asset-media", sessionVersion] head.
        queryClient.invalidateQueries({
          predicate: (query) =>
            query.queryKey[0] === "asset-media" && query.queryKey[1] === sessionVersion,
        }),
      ]),
  });
}

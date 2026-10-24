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

/** Mutation scope of workspace-wide actions (restore, orphan scan/cleanup); not a project ID. */
const WORKSPACE_SCOPE = "workspace";

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

export function useRestoreBackupZip() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, WORKSPACE_SCOPE),
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

export function useScanOrphans() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, WORKSPACE_SCOPE),
    mutationFn: ({ retentionHours }: { retentionHours: number }) =>
      apiPost(
        "/api/v1/maintenance/orphans/scan",
        { retention_hours: retentionHours },
        { timeoutMs: LONG_REQUEST_TIMEOUT_MS },
      ),
  });
}

export function useCleanupOrphans() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, WORKSPACE_SCOPE),
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

/** Query families that may hold a reference to an asset, in any project. */
const ASSET_REFERENCING_QUERIES = new Set([
  "project-shots",
  "shot-takes",
  "shot-take-compare",
  "story-bible",
  "project-timeline",
  "asset-media",
]);

/**
 * Delete an asset by ID (`DELETE /api/v1/assets/{asset_id}`). Asset IDs are global, so this is a
 * workspace action: the asset may belong to any project.
 */
export function useDeleteAsset() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.systemOpsActions(sessionVersion, WORKSPACE_SCOPE),
    mutationFn: ({ assetId, force }: { assetId: string; force: boolean }) =>
      apiDelete<null>("/api/v1/assets/{asset_id}", {
        pathParams: { asset_id: assetId },
        query: { force },
      }),
    // A (forced) delete can detach the asset from shots, takes, Story Bible entries and timeline
    // clips of whichever project owns it; refresh those views in every project.
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.projects(sessionVersion) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.metricsSummary(sessionVersion) }),
        queryClient.invalidateQueries({
          predicate: (query) =>
            ASSET_REFERENCING_QUERIES.has(String(query.queryKey[0])) &&
            query.queryKey[1] === sessionVersion,
        }),
      ]),
  });
}

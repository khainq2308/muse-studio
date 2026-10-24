import {
  Activity,
  Archive,
  Database,
  Download,
  FileUp,
  LoaderCircle,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { ConfirmActionDialog } from "@/components/shared/confirm-action-dialog";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import {
  useCreateProjectBackup,
  useDeleteBackup,
  useDownloadBackup,
  useProjectBackups,
  useSystemLogs,
  useUploadProjectAsset,
  type BackupItem,
  type UploadedAssetItem,
} from "@/features/system-ops/api";
import { ActionError } from "@/features/system-ops/components/ops-reports";
import { formatBytes } from "@/features/system-ops/format";
import { actionErrorMessage, authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

interface ProjectDataPageProps {
  projectId: string;
}

/** Backups and media assets of one project; workspace-wide maintenance lives on the System page. */
export function ProjectDataPage({ projectId }: ProjectDataPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const backupsQuery = useProjectBackups(projectId);
  const logsQuery = useSystemLogs(projectId);

  const createBackup = useCreateProjectBackup(projectId);
  const downloadBackup = useDownloadBackup(projectId);
  const deleteBackup = useDeleteBackup(projectId);
  const uploadAsset = useUploadProjectAsset(projectId);

  const [pendingDeleteBackup, setPendingDeleteBackup] = useState<BackupItem | null>(null);

  const [assetFile, setAssetFile] = useState<File | null>(null);
  const [assetInputKey, setAssetInputKey] = useState(0);
  const [assetType, setAssetType] = useState("reference");
  const [isLockedReference, setIsLockedReference] = useState(false);
  const [recentUploadedAssets, setRecentUploadedAssets] = useState<UploadedAssetItem[]>([]);

  const backups = backupsQuery.data ?? [];
  const logs = logsQuery.data ?? [];

  return (
    <div data-testid="project-data-page" data-project-id={projectId} className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-lg font-semibold tracking-tight">Backups & assets</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Snapshot this project into a downloadable ZIP, and upload or remove its media assets.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              void backupsQuery.refetch();
              void logsQuery.refetch();
            }}
          >
            <RefreshCw className="size-4" aria-hidden="true" />
            Refresh
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={createBackup.isPending}
            onClick={() => createBackup.mutate()}
          >
            {createBackup.isPending ? (
              <>
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                Creating backup...
              </>
            ) : (
              <>
                <Archive className="size-4" aria-hidden="true" />
                Create backup
              </>
            )}
          </Button>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <section
          aria-label="Project backups"
          className="space-y-4 rounded-xl border border-border bg-card p-5"
        >
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
            <h4 className="flex min-w-0 items-center gap-2 text-base font-semibold">
              <Archive className="size-4 shrink-0 text-primary" aria-hidden="true" />
              <span>Project Backups ({backups.length})</span>
            </h4>
            <span className="break-all sm:text-right font-mono text-xs text-muted-foreground">
              GET /api/v1/projects/{projectId}/backups
            </span>
          </div>

          {createBackup.isError && (
            <p role="alert" className="text-xs text-destructive">
              {actionErrorMessage(createBackup.error, activeAdminToken, "create the backup")}
            </p>
          )}

          <QueryState
            isLoading={backupsQuery.isPending}
            isError={backupsQuery.isError}
            isEmpty={backups.length === 0}
            hasData={backups.length > 0}
            loadingMessage="Loading project backups..."
            errorMessage={authErrorMessage(backupsQuery.error, activeAdminToken, "project backups")}
            emptyTitle="No backup snapshots yet"
            emptyDescription="Use “Create backup” to snapshot database records and media files into a downloadable ZIP."
            onRetry={() => void backupsQuery.refetch()}
          >
            <ul className="space-y-2.5">
              {backups.map((backup) => (
                <li
                  key={backup.id}
                  className="flex flex-col justify-between gap-3 rounded-xl border border-border bg-background p-3.5 sm:flex-row sm:items-center"
                >
                  <div className="min-w-0 space-y-1">
                    <p className="truncate font-mono text-xs font-semibold text-foreground">
                      {backup.filename}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {formatBytes(backup.size_bytes)} ·{" "}
                      {new Date(backup.created_at).toLocaleString()} · sha256:{" "}
                      <span className="font-mono">{backup.sha256.slice(0, 12)}…</span>
                    </p>
                  </div>

                  <div className="flex shrink-0 items-center gap-1.5">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={downloadBackup.isPending}
                      onClick={() =>
                        downloadBackup.mutate({
                          backupId: backup.id,
                          filename: backup.filename,
                        })
                      }
                    >
                      <Download className="size-3.5" aria-hidden="true" />
                      Download
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={deleteBackup.isPending}
                      aria-label={`Delete backup ${backup.filename}`}
                      onClick={() => setPendingDeleteBackup(backup)}
                    >
                      <Trash2 className="size-3.5 text-destructive" aria-hidden="true" />
                      Delete
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          </QueryState>

          {pendingDeleteBackup && (
            <ConfirmActionDialog
              title={`Delete backup ${pendingDeleteBackup.filename}?`}
              confirmLabel={deleteBackup.isPending ? "Deleting..." : "Delete backup"}
              destructive
              isSubmitting={deleteBackup.isPending}
              onConfirm={() =>
                deleteBackup.mutate(pendingDeleteBackup.id, {
                  onSuccess: () => setPendingDeleteBackup(null),
                })
              }
              onCancel={() => setPendingDeleteBackup(null)}
            >
              <p>
                The ZIP archive <span className="font-mono">{pendingDeleteBackup.filename}</span> is
                permanently removed from the server. This cannot be undone.
              </p>
            </ConfirmActionDialog>
          )}
          {downloadBackup.isError && (
            <ActionError
              message={actionErrorMessage(
                downloadBackup.error,
                activeAdminToken,
                "download the backup",
              )}
            />
          )}
          {deleteBackup.isError && (
            <ActionError
              message={actionErrorMessage(
                deleteBackup.error,
                activeAdminToken,
                "delete the backup",
              )}
            />
          )}
        </section>

        <section
          aria-label="Asset upload and deletion"
          className="space-y-4 rounded-xl border border-border bg-card p-5"
        >
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
            <h4 className="flex min-w-0 items-center gap-2 text-base font-semibold">
              <Database className="size-4 shrink-0 text-primary" aria-hidden="true" />
              <span>Project Media Asset Manager</span>
            </h4>
            <span className="break-all sm:text-right font-mono text-xs text-muted-foreground">
              /api/v1/assets/*
            </span>
          </div>

          <form
            aria-label="Upload project asset"
            className="space-y-3 rounded-xl border border-border bg-secondary/25 p-3.5"
            onSubmit={(event) => {
              event.preventDefault();
              if (!assetFile || uploadAsset.isPending) return;
              uploadAsset.mutate(
                { file: assetFile, assetType, isLockedReference },
                {
                  onSuccess: (created) => {
                    setRecentUploadedAssets((prev) => [created, ...prev]);
                    setAssetFile(null);
                    setAssetInputKey((key) => key + 1);
                  },
                },
              );
            }}
          >
            <p className="text-xs font-semibold">Upload Media / Reference Asset</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-xs font-medium">
                <span>Asset type</span>
                <select
                  value={assetType}
                  onChange={(event) => setAssetType(event.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm"
                >
                  <option value="reference">reference</option>
                  <option value="audio">audio</option>
                  <option value="video">video</option>
                </select>
              </label>
              <label className="flex items-center gap-2 pt-5 text-xs">
                <input
                  type="checkbox"
                  checked={isLockedReference}
                  onChange={(event) => setIsLockedReference(event.target.checked)}
                  className="rounded border-input"
                />
                <span>Lock as canonical reference asset</span>
              </label>
            </div>

            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <input
                key={assetInputKey}
                type="file"
                aria-label="Asset file"
                onChange={(event) => setAssetFile(event.target.files?.[0] ?? null)}
                className="text-xs text-muted-foreground file:mr-3 file:rounded-md file:border file:border-border file:bg-card file:px-2.5 file:py-1.5 file:text-xs file:font-medium"
              />
              <Button type="submit" size="sm" disabled={!assetFile || uploadAsset.isPending}>
                <FileUp className="size-3.5" aria-hidden="true" />
                {uploadAsset.isPending ? "Uploading..." : "Upload asset"}
              </Button>
            </div>
            {uploadAsset.isError && (
              <ActionError
                message={actionErrorMessage(
                  uploadAsset.error,
                  activeAdminToken,
                  "upload the asset",
                )}
              />
            )}
          </form>

          {recentUploadedAssets.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs font-semibold text-muted-foreground">
                Uploaded in this session:
              </p>
              <ul className="space-y-1.5 text-xs">
                {recentUploadedAssets.map((asset) => (
                  <li
                    key={asset.id}
                    className="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2"
                  >
                    <span className="truncate font-mono">
                      {asset.filename} ({asset.id})
                    </span>
                    <span className="text-muted-foreground">{formatBytes(asset.size_bytes)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>

      <section
        aria-label="Project logs"
        className="space-y-3 rounded-xl border border-border bg-card p-5"
      >
        <h4 className="flex min-w-0 items-center gap-2 text-base font-semibold">
          <Activity className="size-4 shrink-0 text-primary" aria-hidden="true" />
          <span>Recent logs for this project ({logs.length})</span>
        </h4>
        <QueryState
          isLoading={logsQuery.isPending}
          isError={logsQuery.isError}
          isEmpty={logs.length === 0}
          hasData={logs.length > 0}
          loadingMessage="Loading project logs..."
          errorMessage={authErrorMessage(logsQuery.error, activeAdminToken, "project logs")}
          emptyTitle="No structured logs for this project"
          emptyDescription="Operational events for this project are redacted at rest and listed here."
          onRetry={() => void logsQuery.refetch()}
        >
          <ul className="max-h-72 space-y-2 overflow-y-auto pr-1 text-xs">
            {logs.map((log) => (
              <li key={log.id} className="rounded-lg border border-border bg-background px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-semibold text-primary">{log.event}</span>
                  <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px]">
                    {log.level}
                  </span>
                </div>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                  corr: {log.correlation_id}
                  {log.created_at ? ` · ${new Date(log.created_at).toLocaleTimeString()}` : ""}
                </p>
              </li>
            ))}
          </ul>
        </QueryState>
      </section>
    </div>
  );
}

export default ProjectDataPage;

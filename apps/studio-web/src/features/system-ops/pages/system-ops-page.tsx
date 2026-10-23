import {
  Activity,
  Archive,
  Database,
  Download,
  FileUp,
  HardDrive,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldAlert,
  Trash2,
  Upload,
} from "lucide-react";
import { useState } from "react";
import { ConfirmActionDialog } from "@/components/shared/confirm-action-dialog";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import {
  useCleanupOrphans,
  useCreateProjectBackup,
  useDeleteBackup,
  useDeleteProjectAsset,
  useDownloadBackup,
  useMetricsSummary,
  useProjectBackups,
  useRestoreBackupZip,
  useScanOrphans,
  useSystemLogs,
  useUploadProjectAsset,
  type BackupItem,
  type BackupRestoreResult,
  type OrphanCleanupResult,
  type OrphanFile,
  type OrphanScanResult,
  type UploadedAssetItem,
} from "@/features/system-ops/api";
import { actionErrorMessage, authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

interface SystemOpsPageProps {
  projectId: string;
}

/** Backend bounds for `retention_hours` (`OrphanScanIn` / `OrphanCleanupIn`: ge=1, le=8760). */
const MIN_RETENTION_HOURS = 1;
const MAX_RETENTION_HOURS = 8760;
/** Rows listed per scan/dry-run/restore report before collapsing into "+N more". */
const MAX_LISTED_ROWS = 50;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/** Parse the retention field; null when empty, non-numeric or outside the backend bounds. */
function parseRetentionHours(input: string): number | null {
  if (!input.trim()) return null;
  const value = Number(input);
  if (!Number.isFinite(value)) return null;
  if (value < MIN_RETENTION_HOURS || value > MAX_RETENTION_HOURS) return null;
  return value;
}

function ActionError({ message }: { message: string }) {
  return (
    <p role="alert" className="text-xs text-destructive">
      {message}
    </p>
  );
}

function OrphanFileList({ orphans }: { orphans: OrphanFile[] }) {
  if (orphans.length === 0) return null;
  const shown = orphans.slice(0, MAX_LISTED_ROWS);
  const hidden = orphans.length - shown.length;
  return (
    <ul aria-label="Orphan files" className="mt-2 max-h-56 space-y-1 overflow-y-auto pr-1">
      {shown.map((orphan) => (
        <li key={orphan.rel_path} className="flex items-center justify-between gap-2">
          <span className="truncate font-mono">{orphan.rel_path}</span>
          <span className="shrink-0 text-muted-foreground">
            {formatBytes(orphan.size_bytes)} · {Math.round(orphan.age_hours)}h old
          </span>
        </li>
      ))}
      {hidden > 0 && <li className="text-muted-foreground">+{hidden} more</li>}
    </ul>
  );
}

function RestoreReport({ restored }: { restored: BackupRestoreResult }) {
  const { report } = restored;
  const rowsSkipped = Object.entries(report.rows_skipped).filter(([, count]) => count > 0);
  const skippedRows = report.skipped_rows ?? [];
  const tasksNeutralized = report.tasks_neutralized ?? 0;
  const exportsNeutralized = report.exports_neutralized ?? 0;
  const placeholderTasks = report.tasks_with_placeholder_connection ?? 0;
  const hasWarnings =
    report.media_missing.length > 0 ||
    rowsSkipped.length > 0 ||
    skippedRows.length > 0 ||
    tasksNeutralized > 0 ||
    exportsNeutralized > 0 ||
    placeholderTasks > 0;
  return (
    <div
      role="status"
      data-testid="restore-report"
      data-warning={hasWarnings ? "true" : "false"}
      className={
        hasWarnings
          ? "space-y-1 rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs"
          : "space-y-1 rounded-lg border border-success/30 bg-success/10 p-3 text-xs"
      }
    >
      <p className={hasWarnings ? "font-semibold text-warning" : "font-semibold text-success"}>
        Restored project “{restored.project_name}” ({restored.project_id})
        {hasWarnings && " with warnings"}
      </p>
      <p className="text-muted-foreground">
        Media files restored: {report.media_restored} · Missing media: {report.media_missing.length}
      </p>
      <p className="text-muted-foreground">
        Tasks neutralized: {tasksNeutralized} · Exports neutralized: {exportsNeutralized} · Tasks
        with placeholder connection: {placeholderTasks}
      </p>
      {rowsSkipped.length > 0 && (
        <p className="text-muted-foreground">
          Rows skipped: {rowsSkipped.map(([table, count]) => `${table}: ${count}`).join(", ")}
        </p>
      )}
      {skippedRows.length > 0 && (
        <ul aria-label="Skipped rows" className="max-h-40 space-y-0.5 overflow-y-auto">
          {skippedRows.slice(0, MAX_LISTED_ROWS).map((row) => (
            <li key={`${row.table}:${row.original_id}`} className="font-mono">
              {row.table}/{row.original_id}: {row.reason}
            </li>
          ))}
          {skippedRows.length > MAX_LISTED_ROWS && (
            <li className="text-muted-foreground">+{skippedRows.length - MAX_LISTED_ROWS} more</li>
          )}
        </ul>
      )}
      {report.media_missing.length > 0 && (
        <p className="text-muted-foreground">
          Missing media files:{" "}
          <span className="font-mono">{report.media_missing.slice(0, 10).join(", ")}</span>
          {report.media_missing.length > 10 && ` +${report.media_missing.length - 10} more`}
        </p>
      )}
    </div>
  );
}

function CleanupReport({ result }: { result: OrphanCleanupResult }) {
  const hasErrors = result.errors.length > 0;
  const freed = formatBytes(result.freed_bytes);
  let title: string;
  if (result.dry_run) {
    const previewBytes = result.orphans.reduce((total, orphan) => total + orphan.size_bytes, 0);
    title = `Dry-run report: ${result.scanned_orphans} orphan files would be deleted (${formatBytes(previewBytes)})`;
  } else if (hasErrors) {
    title = `Cleanup finished with ${result.errors.length} error(s): deleted ${result.deleted} of ${result.scanned_orphans} (${freed} freed)`;
  } else {
    title = `Cleanup completed: deleted ${result.deleted} of ${result.scanned_orphans} (${freed} freed)`;
  }
  return (
    <div
      role={hasErrors ? "alert" : "status"}
      data-testid={result.dry_run ? "dry-run-report" : "cleanup-report"}
      data-warning={hasErrors ? "true" : "false"}
      className={
        hasErrors
          ? "rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs"
          : "rounded-lg border border-border bg-secondary/40 p-3 text-xs"
      }
    >
      <p className={hasErrors ? "font-semibold text-warning" : "font-semibold"}>{title}</p>
      {hasErrors && (
        <ul aria-label="Cleanup errors" className="mt-2 space-y-0.5">
          {result.errors.map((error, index) => (
            <li key={`${index}:${error}`} className="font-mono">
              {error}
            </li>
          ))}
        </ul>
      )}
      {result.dry_run && <OrphanFileList orphans={result.orphans} />}
    </div>
  );
}

export function SystemOpsPage({ projectId }: SystemOpsPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const backupsQuery = useProjectBackups(projectId);
  const metricsQuery = useMetricsSummary();
  const logsQuery = useSystemLogs(projectId);

  const createBackup = useCreateProjectBackup(projectId);
  const downloadBackup = useDownloadBackup(projectId);
  const deleteBackup = useDeleteBackup(projectId);
  const restoreBackup = useRestoreBackupZip(projectId);
  const scanOrphans = useScanOrphans(projectId);
  const cleanupOrphans = useCleanupOrphans(projectId);
  const uploadAsset = useUploadProjectAsset(projectId);
  const deleteAsset = useDeleteProjectAsset(projectId);

  const [pendingDeleteBackup, setPendingDeleteBackup] = useState<BackupItem | null>(null);

  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreInputKey, setRestoreInputKey] = useState(0);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const [restoreReport, setRestoreReport] = useState<BackupRestoreResult | null>(null);

  const [retentionInput, setRetentionInput] = useState("24");
  const retentionHours = parseRetentionHours(retentionInput);
  const [confirmCleanupDelete, setConfirmCleanupDelete] = useState(false);
  const [scanResult, setScanResult] = useState<OrphanScanResult | null>(null);
  const [dryRunResult, setDryRunResult] = useState<OrphanCleanupResult | null>(null);
  const [cleanupResult, setCleanupResult] = useState<OrphanCleanupResult | null>(null);
  // Real deletion is only offered for the exact retention window the operator previewed.
  const canRunRealCleanup =
    retentionHours !== null &&
    dryRunResult !== null &&
    dryRunResult.retention_hours === retentionHours;

  const [assetFile, setAssetFile] = useState<File | null>(null);
  const [assetInputKey, setAssetInputKey] = useState(0);
  const [assetType, setAssetType] = useState("reference");
  const [isLockedReference, setIsLockedReference] = useState(false);
  const [recentUploadedAssets, setRecentUploadedAssets] = useState<UploadedAssetItem[]>([]);
  const [deleteAssetIdInput, setDeleteAssetIdInput] = useState("");
  const [forceDeleteAsset, setForceDeleteAsset] = useState(false);
  const [confirmDeleteAsset, setConfirmDeleteAsset] = useState(false);
  const [deleteAssetStatus, setDeleteAssetStatus] = useState<string | null>(null);

  const backups = backupsQuery.data ?? [];
  const metrics = metricsQuery.data;
  const logs = logsQuery.data ?? [];

  return (
    <div data-testid="system-ops-page" data-project-id={projectId} className="space-y-6">
      <div className="flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <HardDrive className="size-5 text-primary" aria-hidden="true" />
            <h2 className="text-xl font-semibold tracking-tight">
              System Operations, Backups & Asset Maintenance
            </h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Create and restore portable project backup ZIP archives, upload or remove media assets,
            scan and clean up orphan storage files, and inspect live observability metrics.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              void metricsQuery.refetch();
              void backupsQuery.refetch();
              void logsQuery.refetch();
            }}
          >
            <RefreshCw className="size-4" aria-hidden="true" />
            Refresh telemetry
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
                Snapshotting ZIP...
              </>
            ) : (
              <>
                <Archive className="size-4" aria-hidden="true" />
                Create Project Backup
              </>
            )}
          </Button>
        </div>
      </div>

      <QueryState
        isLoading={metricsQuery.isPending}
        isError={metricsQuery.isError}
        isEmpty={!metrics}
        hasData={Boolean(metrics)}
        loadingMessage="Loading observability summary..."
        errorMessage={authErrorMessage(metricsQuery.error, activeAdminToken, "metrics summary")}
        emptyTitle="No metrics available"
        emptyDescription="The Orchestrator returned no metrics summary."
        onRetry={() => void metricsQuery.refetch()}
      >
        {metrics && (
          <section
            aria-label="Observability summary"
            className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
          >
            <div className="rounded-xl border border-border bg-secondary/30 p-4">
              <p className="text-xs font-medium text-muted-foreground">Media Storage</p>
              <p className="mt-1 text-lg font-bold">{formatBytes(metrics.storage.total_bytes)}</p>
              <p className="text-xs text-muted-foreground">
                {metrics.storage.file_count} files on disk · {metrics.assets.total} DB assets
              </p>
            </div>
            <div className="rounded-xl border border-border bg-secondary/30 p-4">
              <p className="text-xs font-medium text-muted-foreground">Backups Archive</p>
              <p className="mt-1 text-lg font-bold">{metrics.backups.total} archives</p>
              <p className="text-xs text-muted-foreground">
                {formatBytes(metrics.backups.total_bytes)} total backup size
              </p>
            </div>
            <div className="rounded-xl border border-border bg-secondary/30 p-4">
              <p className="text-xs font-medium text-muted-foreground">Queue & Exports</p>
              <p className="mt-1 text-lg font-bold">
                {metrics.queue.depth} active · {metrics.exports.total} exports
              </p>
              <p className="text-xs text-muted-foreground">
                {metrics.queue.generation_tasks_total} total generation tasks
              </p>
            </div>
            <div className="rounded-xl border border-border bg-secondary/30 p-4">
              <p className="text-xs font-medium text-muted-foreground">Orchestrator Runtime</p>
              <p className="mt-1 text-lg font-bold">v{metrics.service.version}</p>
              <p className="text-xs text-muted-foreground">
                env: {metrics.service.environment} · up {Math.round(metrics.service.uptime_seconds)}
                s
              </p>
            </div>
          </section>
        )}
      </QueryState>

      <div className="grid gap-6 lg:grid-cols-12">
        <div className="space-y-6 lg:col-span-7">
          <section
            aria-label="Project backups"
            className="space-y-4 rounded-xl border border-border bg-card p-5"
          >
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
              <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold">
                <Archive className="size-4 shrink-0 text-primary" aria-hidden="true" />
                <span>Project Backups ({backups.length})</span>
              </h3>
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
              errorMessage={authErrorMessage(
                backupsQuery.error,
                activeAdminToken,
                "project backups",
              )}
              emptyTitle="No backup snapshots yet"
              emptyDescription="Click “Create Project Backup” to snapshot database records and media files into a downloadable ZIP."
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
                  The ZIP archive <span className="font-mono">{pendingDeleteBackup.filename}</span>{" "}
                  is permanently removed from the server. This cannot be undone.
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

            <form
              aria-label="Restore project from backup ZIP"
              className="space-y-3 border-t border-border pt-4"
              onSubmit={(event) => {
                event.preventDefault();
                if (!restoreFile || restoreBackup.isPending) return;
                setConfirmRestore(true);
              }}
            >
              <p className="text-xs font-semibold text-foreground">
                Restore Project from Backup Archive (POST /api/v1/backups/restore)
              </p>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  key={restoreInputKey}
                  type="file"
                  aria-label="Backup ZIP file"
                  accept=".zip,application/zip"
                  onChange={(event) => {
                    setRestoreFile(event.target.files?.[0] ?? null);
                    setConfirmRestore(false);
                  }}
                  className="text-xs text-muted-foreground file:mr-3 file:rounded-md file:border file:border-border file:bg-secondary file:px-2.5 file:py-1.5 file:text-xs file:font-medium file:text-foreground"
                />
                <Button
                  type="submit"
                  size="sm"
                  variant="secondary"
                  disabled={!restoreFile || restoreBackup.isPending || confirmRestore}
                >
                  <Upload className="size-3.5" aria-hidden="true" />
                  {restoreBackup.isPending ? "Restoring..." : "Restore ZIP"}
                </Button>
              </div>
              {confirmRestore && restoreFile && (
                <ConfirmActionDialog
                  title={`Restore ${restoreFile.name}?`}
                  confirmLabel={restoreBackup.isPending ? "Restoring..." : "Restore backup"}
                  isSubmitting={restoreBackup.isPending}
                  onConfirm={() =>
                    restoreBackup.mutate(
                      { file: restoreFile },
                      {
                        onSuccess: (restored) => {
                          setRestoreReport(restored);
                          setRestoreFile(null);
                          setConfirmRestore(false);
                          setRestoreInputKey((key) => key + 1);
                        },
                      },
                    )
                  }
                  onCancel={() => setConfirmRestore(false)}
                >
                  <p>
                    Restoring imports the archive as a new project. Queued tasks and exports are
                    neutralized and rows that cannot be mapped are skipped.
                  </p>
                </ConfirmActionDialog>
              )}
              {restoreBackup.isError && (
                <ActionError
                  message={actionErrorMessage(
                    restoreBackup.error,
                    activeAdminToken,
                    "restore the backup",
                  )}
                />
              )}
              {restoreReport && <RestoreReport restored={restoreReport} />}
            </form>
          </section>

          <section
            aria-label="Asset upload and deletion"
            className="space-y-4 rounded-xl border border-border bg-card p-5"
          >
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
              <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold">
                <Database className="size-4 shrink-0 text-primary" aria-hidden="true" />
                <span>Project Media Asset Manager</span>
              </h3>
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

            <form
              aria-label="Delete asset by ID"
              className="space-y-2.5 border-t border-border pt-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (!deleteAssetIdInput.trim() || deleteAsset.isPending) return;
                setDeleteAssetStatus(null);
                setConfirmDeleteAsset(true);
              }}
            >
              <p className="text-xs font-semibold">
                Delete Asset (DELETE /api/v1/assets/&#123;asset_id&#125;)
              </p>
              <p className="text-xs text-muted-foreground">
                The Orchestrator has no project asset listing endpoint, so assets are deleted by ID.
                This action is not project-scoped: any asset ID from any project is accepted.
              </p>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  aria-label="Asset ID to delete"
                  value={deleteAssetIdInput}
                  onChange={(event) => {
                    setDeleteAssetIdInput(event.target.value);
                    setConfirmDeleteAsset(false);
                  }}
                  placeholder="Asset ID to delete"
                  className="h-9 w-full min-w-0 rounded-lg border border-input bg-background px-3 font-mono text-xs sm:flex-1"
                />
                <label className="flex items-center gap-1.5 text-xs">
                  <input
                    type="checkbox"
                    checked={forceDeleteAsset}
                    onChange={(event) => {
                      setForceDeleteAsset(event.target.checked);
                      setConfirmDeleteAsset(false);
                    }}
                    className="rounded border-input"
                  />
                  <span>Force</span>
                </label>
                <Button
                  type="submit"
                  variant="destructive"
                  size="sm"
                  disabled={
                    !deleteAssetIdInput.trim() || deleteAsset.isPending || confirmDeleteAsset
                  }
                >
                  <Trash2 className="size-3.5" aria-hidden="true" />
                  Delete asset
                </Button>
              </div>
              {confirmDeleteAsset && deleteAssetIdInput.trim() && (
                <ConfirmActionDialog
                  title={
                    forceDeleteAsset
                      ? `Force delete asset ${deleteAssetIdInput.trim()}?`
                      : `Delete asset ${deleteAssetIdInput.trim()}?`
                  }
                  confirmLabel={
                    deleteAsset.isPending
                      ? "Deleting..."
                      : forceDeleteAsset
                        ? "Force delete asset"
                        : "Delete asset"
                  }
                  destructive
                  isSubmitting={deleteAsset.isPending}
                  onConfirm={() => {
                    const assetId = deleteAssetIdInput.trim();
                    const force = forceDeleteAsset;
                    deleteAsset.mutate(
                      { assetId, force },
                      {
                        onSuccess: () => {
                          setDeleteAssetIdInput("");
                          setConfirmDeleteAsset(false);
                          setForceDeleteAsset(false);
                          setDeleteAssetStatus(
                            `Asset ${assetId} was ${force ? "force " : ""}deleted.`,
                          );
                        },
                      },
                    );
                  }}
                  onCancel={() => setConfirmDeleteAsset(false)}
                >
                  {forceDeleteAsset ? (
                    <p>
                      Force deletes even if referenced by takes/shots, Story Bible entries or
                      timeline clips; those references are detached. The file is removed
                      permanently.
                    </p>
                  ) : (
                    <p>
                      The asset file is removed permanently. The delete is refused if takes, shots
                      or timeline clips still reference it.
                    </p>
                  )}
                  <p>This delete is not scoped to the current project.</p>
                </ConfirmActionDialog>
              )}
              {deleteAsset.isError && (
                <ActionError
                  message={actionErrorMessage(
                    deleteAsset.error,
                    activeAdminToken,
                    "delete the asset",
                  )}
                />
              )}
              {deleteAssetStatus && !deleteAsset.isError && (
                <p role="status" className="text-xs text-success">
                  {deleteAssetStatus}
                </p>
              )}
            </form>
          </section>
        </div>

        <div className="space-y-6 lg:col-span-5">
          <section
            aria-label="Orphan media maintenance"
            className="space-y-4 rounded-xl border border-border bg-card p-5"
          >
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-2 text-base font-semibold">
                <ShieldAlert className="size-4 text-warning" aria-hidden="true" />
                Orphan Media Scan & Cleanup
              </h3>
            </div>
            <p className="text-xs text-muted-foreground">
              Scan for unreferenced files on disk older than the retention window before running
              dry-run or destructive cleanup. Scope: the global media root shared by all projects,
              not only this project.
            </p>

            <label className="block space-y-1 text-xs font-medium">
              <span>Retention window (hours)</span>
              <input
                type="number"
                min={MIN_RETENTION_HOURS}
                max={MAX_RETENTION_HOURS}
                step="any"
                value={retentionInput}
                aria-invalid={retentionHours === null}
                onChange={(event) => {
                  setRetentionInput(event.target.value);
                  // A dry-run preview is only valid for the retention window it was run with.
                  setDryRunResult(null);
                  setConfirmCleanupDelete(false);
                }}
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm"
              />
            </label>
            {retentionHours === null && (
              <p role="alert" className="text-xs text-destructive">
                Retention must be between {MIN_RETENTION_HOURS} and {MAX_RETENTION_HOURS} hours.
              </p>
            )}

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={scanOrphans.isPending || retentionHours === null}
                onClick={() => {
                  if (retentionHours === null) return;
                  scanOrphans.mutate(
                    { retentionHours },
                    { onSuccess: (res) => setScanResult(res) },
                  );
                }}
              >
                <Search className="size-3.5" aria-hidden="true" />
                Scan Orphans (Read-only)
              </Button>

              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={cleanupOrphans.isPending || retentionHours === null}
                onClick={() => {
                  if (retentionHours === null) return;
                  setConfirmCleanupDelete(false);
                  cleanupOrphans.mutate(
                    { dryRun: true, retentionHours },
                    { onSuccess: (res) => setDryRunResult(res) },
                  );
                }}
              >
                Dry-Run Cleanup
              </Button>
            </div>

            <div className="space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
              <p className="text-xs">
                {canRunRealCleanup
                  ? `Permanently delete the ${dryRunResult.scanned_orphans} orphan files previewed by the dry-run (older than ${retentionHours}h).`
                  : "Run a dry-run with the current retention window before deleting orphan files."}
              </p>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={!canRunRealCleanup || cleanupOrphans.isPending || confirmCleanupDelete}
                onClick={() => setConfirmCleanupDelete(true)}
              >
                <Trash2 className="size-3.5" aria-hidden="true" />
                Delete Orphan Files
              </Button>
              {confirmCleanupDelete && canRunRealCleanup && (
                <ConfirmActionDialog
                  title={`Delete orphan files older than ${retentionHours}h across all projects?`}
                  confirmLabel={cleanupOrphans.isPending ? "Deleting..." : "Delete orphan files"}
                  destructive
                  isSubmitting={cleanupOrphans.isPending}
                  onConfirm={() =>
                    cleanupOrphans.mutate(
                      { dryRun: false, retentionHours },
                      {
                        onSuccess: (res) => {
                          setCleanupResult(res);
                          setDryRunResult(null);
                          setConfirmCleanupDelete(false);
                        },
                      },
                    )
                  }
                  onCancel={() => setConfirmCleanupDelete(false)}
                >
                  <p>
                    Cleanup is global: it scans the shared media root of every project, not only
                    this one. Unreferenced files older than {retentionHours}h are permanently
                    deleted (<code className="font-mono">dry_run: false</code>); the set may differ
                    from the dry-run if files changed since.
                  </p>
                </ConfirmActionDialog>
              )}
            </div>

            {scanOrphans.isError && (
              <ActionError
                message={actionErrorMessage(
                  scanOrphans.error,
                  activeAdminToken,
                  "scan orphan files",
                )}
              />
            )}
            {cleanupOrphans.isError && (
              <ActionError
                message={actionErrorMessage(
                  cleanupOrphans.error,
                  activeAdminToken,
                  "clean up orphan files",
                )}
              />
            )}

            {scanResult && (
              <div className="rounded-lg border border-border bg-secondary/40 p-3 text-xs">
                <p className="font-semibold">
                  Scan found {scanResult.count} orphan files ({formatBytes(scanResult.total_bytes)})
                  older than {scanResult.retention_hours}h
                </p>
                <OrphanFileList orphans={scanResult.orphans} />
              </div>
            )}

            {dryRunResult && <CleanupReport result={dryRunResult} />}
            {cleanupResult && <CleanupReport result={cleanupResult} />}
          </section>

          <section
            aria-label="Structured operational logs"
            className="space-y-3 rounded-xl border border-border bg-card p-5"
          >
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
              <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold">
                <Activity className="size-4 shrink-0 text-primary" aria-hidden="true" />
                <span>Recent Structured Logs ({logs.length})</span>
              </h3>
              <span className="break-all sm:text-right font-mono text-xs text-muted-foreground">
                /api/v1/system/logs
              </span>
            </div>

            <QueryState
              isLoading={logsQuery.isPending}
              isError={logsQuery.isError}
              isEmpty={logs.length === 0}
              hasData={logs.length > 0}
              loadingMessage="Loading structured logs..."
              errorMessage={authErrorMessage(logsQuery.error, activeAdminToken, "system logs")}
              emptyTitle="No structured logs for this project"
              emptyDescription="Operational events and audit trails are redacted at rest and listed here."
              onRetry={() => void logsQuery.refetch()}
            >
              <ul className="max-h-72 space-y-2 overflow-y-auto pr-1 text-xs">
                {logs.map((log) => (
                  <li
                    key={log.id}
                    className="rounded-lg border border-border bg-background px-3 py-2"
                  >
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
      </div>
    </div>
  );
}

export default SystemOpsPage;

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
  type BackupRestoreResult,
  type OrphanCleanupResult,
  type OrphanScanResult,
  type UploadedAssetItem,
} from "@/features/system-ops/api";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

interface SystemOpsPageProps {
  projectId: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
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

  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreReport, setRestoreReport] = useState<BackupRestoreResult | null>(null);

  const [retentionHours, setRetentionHours] = useState(24);
  const [confirmCleanupDelete, setConfirmCleanupDelete] = useState(false);
  const [scanResult, setScanResult] = useState<OrphanScanResult | null>(null);
  const [cleanupResult, setCleanupResult] = useState<OrphanCleanupResult | null>(null);

  const [assetFile, setAssetFile] = useState<File | null>(null);
  const [assetType, setAssetType] = useState("reference");
  const [isLockedReference, setIsLockedReference] = useState(false);
  const [recentUploadedAssets, setRecentUploadedAssets] = useState<UploadedAssetItem[]>([]);
  const [deleteAssetIdInput, setDeleteAssetIdInput] = useState("");
  const [forceDeleteAsset, setForceDeleteAsset] = useState(false);

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
              env: {metrics.service.environment} · up {Math.round(metrics.service.uptime_seconds)}s
            </p>
          </div>
        </section>
      )}

      <div className="grid gap-6 lg:grid-cols-12">
        <div className="space-y-6 lg:col-span-7">
          <section
            aria-label="Project backups"
            className="space-y-4 rounded-xl border border-border bg-card p-5"
          >
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-2 text-base font-semibold">
                <Archive className="size-4 text-primary" aria-hidden="true" />
                Project Backups ({backups.length})
              </h3>
              <span className="font-mono text-xs text-muted-foreground">
                GET /api/v1/projects/{projectId}/backups
              </span>
            </div>

            {createBackup.isError && (
              <p role="alert" className="text-xs text-destructive">
                {authErrorMessage(createBackup.error, activeAdminToken, "create backup")}
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
                        onClick={() => deleteBackup.mutate(backup.id)}
                      >
                        <Trash2 className="size-3.5 text-destructive" aria-hidden="true" />
                        Delete
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </QueryState>

            <form
              aria-label="Restore project from backup ZIP"
              className="space-y-3 border-t border-border pt-4"
              onSubmit={(event) => {
                event.preventDefault();
                if (!restoreFile || restoreBackup.isPending) return;
                restoreBackup.mutate(
                  { file: restoreFile },
                  {
                    onSuccess: (restored) => {
                      setRestoreReport(restored);
                      setRestoreFile(null);
                    },
                  },
                );
              }}
            >
              <p className="text-xs font-semibold text-foreground">
                Restore Project from Backup Archive (POST /api/v1/backups/restore)
              </p>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  type="file"
                  accept=".zip,application/zip"
                  onChange={(event) => setRestoreFile(event.target.files?.[0] ?? null)}
                  className="text-xs text-muted-foreground file:mr-3 file:rounded-md file:border file:border-border file:bg-secondary file:px-2.5 file:py-1.5 file:text-xs file:font-medium file:text-foreground"
                />
                <Button
                  type="submit"
                  size="sm"
                  variant="secondary"
                  disabled={!restoreFile || restoreBackup.isPending}
                >
                  <Upload className="size-3.5" aria-hidden="true" />
                  {restoreBackup.isPending ? "Restoring..." : "Restore ZIP"}
                </Button>
              </div>
              {restoreReport && (
                <div className="rounded-lg border border-success/30 bg-success/10 p-3 text-xs">
                  <p className="font-semibold text-success">
                    Restored project “{restoreReport.project_name}” ({restoreReport.project_id})
                  </p>
                  <p className="mt-1 text-muted-foreground">
                    Media files restored: {restoreReport.report.media_restored} · Missing media:{" "}
                    {restoreReport.report.media_missing.length}
                  </p>
                </div>
              )}
            </form>
          </section>

          <section
            aria-label="Asset upload and deletion"
            className="space-y-4 rounded-xl border border-border bg-card p-5"
          >
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-2 text-base font-semibold">
                <Database className="size-4 text-primary" aria-hidden="true" />
                Project Media Asset Manager
              </h3>
              <span className="font-mono text-xs text-muted-foreground">/api/v1/assets/*</span>
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
                  type="file"
                  onChange={(event) => setAssetFile(event.target.files?.[0] ?? null)}
                  className="text-xs text-muted-foreground file:mr-3 file:rounded-md file:border file:border-border file:bg-card file:px-2.5 file:py-1.5 file:text-xs file:font-medium"
                />
                <Button type="submit" size="sm" disabled={!assetFile || uploadAsset.isPending}>
                  <FileUp className="size-3.5" aria-hidden="true" />
                  {uploadAsset.isPending ? "Uploading..." : "Upload asset"}
                </Button>
              </div>
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
                deleteAsset.mutate(
                  { assetId: deleteAssetIdInput.trim(), force: forceDeleteAsset },
                  {
                    onSuccess: () => setDeleteAssetIdInput(""),
                  },
                );
              }}
            >
              <p className="text-xs font-semibold">
                Delete Asset (DELETE /api/v1/assets/&#123;asset_id&#125;)
              </p>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  value={deleteAssetIdInput}
                  onChange={(event) => setDeleteAssetIdInput(event.target.value)}
                  placeholder="Asset ID to delete"
                  className="h-9 flex-1 rounded-lg border border-input bg-background px-3 font-mono text-xs"
                />
                <label className="flex items-center gap-1.5 text-xs">
                  <input
                    type="checkbox"
                    checked={forceDeleteAsset}
                    onChange={(event) => setForceDeleteAsset(event.target.checked)}
                    className="rounded border-input"
                  />
                  <span>Force</span>
                </label>
                <Button
                  type="submit"
                  variant="destructive"
                  size="sm"
                  disabled={!deleteAssetIdInput.trim() || deleteAsset.isPending}
                >
                  <Trash2 className="size-3.5" aria-hidden="true" />
                  Delete asset
                </Button>
              </div>
              {deleteAsset.isError && (
                <p role="alert" className="text-xs text-destructive">
                  {authErrorMessage(deleteAsset.error, activeAdminToken, "delete asset")}
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
              dry-run or destructive cleanup.
            </p>

            <label className="block space-y-1 text-xs font-medium">
              <span>Retention window (hours)</span>
              <input
                type="number"
                min={0}
                max={720}
                value={retentionHours}
                onChange={(event) => setRetentionHours(Math.max(0, Number(event.target.value)))}
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm"
              />
            </label>

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={scanOrphans.isPending}
                onClick={() =>
                  scanOrphans.mutate({ retentionHours }, { onSuccess: (res) => setScanResult(res) })
                }
              >
                <Search className="size-3.5" aria-hidden="true" />
                Scan Orphans (Read-only)
              </Button>

              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={cleanupOrphans.isPending}
                onClick={() =>
                  cleanupOrphans.mutate(
                    { dryRun: true, retentionHours },
                    { onSuccess: (res) => setCleanupResult(res) },
                  )
                }
              >
                Dry-Run Cleanup
              </Button>
            </div>

            <div className="space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
              <label className="flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={confirmCleanupDelete}
                  onChange={(event) => setConfirmCleanupDelete(event.target.checked)}
                  className="mt-0.5 rounded border-input"
                />
                <span>
                  Confirm permanent deletion of orphan media files older than {retentionHours}h (
                  <code className="font-mono">dry_run: false</code>).
                </span>
              </label>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={!confirmCleanupDelete || cleanupOrphans.isPending}
                onClick={() =>
                  cleanupOrphans.mutate(
                    { dryRun: false, retentionHours },
                    {
                      onSuccess: (res) => {
                        setCleanupResult(res);
                        setConfirmCleanupDelete(false);
                      },
                    },
                  )
                }
              >
                <Trash2 className="size-3.5" aria-hidden="true" />
                Delete Orphan Files
              </Button>
            </div>

            {scanResult && (
              <div className="rounded-lg border border-border bg-secondary/40 p-3 text-xs">
                <p className="font-semibold">
                  Scan found {scanResult.count} orphan files ({formatBytes(scanResult.total_bytes)})
                </p>
              </div>
            )}

            {cleanupResult && (
              <div className="rounded-lg border border-border bg-secondary/40 p-3 text-xs">
                <p className="font-semibold">
                  {cleanupResult.dry_run ? "Dry-run report:" : "Cleanup completed:"} scanned{" "}
                  {cleanupResult.scanned_orphans}, deleted {cleanupResult.deleted} (
                  {formatBytes(cleanupResult.freed_bytes)} freed)
                </p>
              </div>
            )}
          </section>

          <section
            aria-label="Structured operational logs"
            className="space-y-3 rounded-xl border border-border bg-card p-5"
          >
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-2 text-base font-semibold">
                <Activity className="size-4 text-primary" aria-hidden="true" />
                Recent Structured Logs ({logs.length})
              </h3>
              <span className="font-mono text-xs text-muted-foreground">/api/v1/system/logs</span>
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

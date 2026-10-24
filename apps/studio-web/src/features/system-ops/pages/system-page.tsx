import { Activity, RefreshCw, Search, ShieldAlert, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import { DeleteAssetForm } from "@/features/system-ops/components/delete-asset-form";
import { ConfirmActionDialog } from "@/components/shared/confirm-action-dialog";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import {
  useCleanupOrphans,
  useMetricsSummary,
  useRestoreBackupZip,
  useScanOrphans,
  useSystemLogs,
  type BackupRestoreResult,
  type OrphanCleanupResult,
  type OrphanScanResult,
} from "@/features/system-ops/api";
import {
  ActionError,
  OrphanFileList,
  RestoreReport,
  CleanupReport,
} from "@/features/system-ops/components/ops-reports";
import {
  MIN_RETENTION_HOURS,
  MAX_RETENTION_HOURS,
  formatBytes,
  parseRetentionHours,
} from "@/features/system-ops/format";
import { actionErrorMessage, authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { PageHeader } from "@/components/shared/page-header";

/** Workspace-level operations that are not scoped to one project. */
export function SystemPage() {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const metricsQuery = useMetricsSummary();
  const logsQuery = useSystemLogs();

  const restoreBackup = useRestoreBackupZip();
  const scanOrphans = useScanOrphans();
  const cleanupOrphans = useCleanupOrphans();

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

  const metrics = metricsQuery.data;
  const logs = logsQuery.data ?? [];

  return (
    <div data-testid="system-page" className="space-y-6">
      <PageHeader
        title="System & maintenance"
        description="Workspace-wide tools. Restoring a backup creates a new project, and orphan cleanup scans the media root shared by every project. Back up a single project from its Settings page."
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                void metricsQuery.refetch();
                void logsQuery.refetch();
              }}
            >
              <RefreshCw className="size-4" aria-hidden="true" />
              Refresh
            </Button>
          </>
        }
      />

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
            aria-label="Restore a backup"
            className="space-y-4 rounded-xl border border-border bg-card p-5"
          >
            <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold">
              <Upload className="size-4 shrink-0 text-primary" aria-hidden="true" />
              <span>Restore a backup</span>
            </h3>
            <form
              aria-label="Restore project from backup ZIP"
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (!restoreFile || restoreBackup.isPending) return;
                setConfirmRestore(true);
              }}
            >
              <p className="text-xs text-muted-foreground">
                The archive is imported as a new project; existing projects are not changed.
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
              dry-run or destructive cleanup. Scope: the media root shared by every project.
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
            aria-label="Delete an asset"
            className="space-y-4 rounded-xl border border-border bg-card p-5"
          >
            <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold">
              <Trash2 className="size-4 shrink-0 text-destructive" aria-hidden="true" />
              <span>Delete an asset</span>
            </h3>
            <DeleteAssetForm />
          </section>
        </div>

        <div className="space-y-6 lg:col-span-5">
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
              emptyTitle="No structured logs yet"
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

export default SystemPage;

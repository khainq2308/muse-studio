import type {
  BackupRestoreResult,
  OrphanCleanupResult,
  OrphanFile,
} from "@/features/system-ops/api";
import { MAX_LISTED_ROWS, formatBytes } from "@/features/system-ops/format";

export function ActionError({ message }: { message: string }) {
  return (
    <p role="alert" className="text-xs text-destructive">
      {message}
    </p>
  );
}

export function OrphanFileList({ orphans }: { orphans: OrphanFile[] }) {
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

export function RestoreReport({ restored }: { restored: BackupRestoreResult }) {
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

export function CleanupReport({ result }: { result: OrphanCleanupResult }) {
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

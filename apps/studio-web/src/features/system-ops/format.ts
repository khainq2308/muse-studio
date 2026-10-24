/** Backend bounds for `retention_hours` (`OrphanScanIn` / `OrphanCleanupIn`: ge=1, le=8760). */
export const MIN_RETENTION_HOURS = 1;
export const MAX_RETENTION_HOURS = 8760;
/** Rows listed per scan/dry-run/restore report before collapsing into "+N more". */
export const MAX_LISTED_ROWS = 50;

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/** Parse the retention field; null when empty, non-numeric or outside the backend bounds. */
export function parseRetentionHours(input: string): number | null {
  if (!input.trim()) return null;
  const value = Number(input);
  if (!Number.isFinite(value)) return null;
  if (value < MIN_RETENTION_HOURS || value > MAX_RETENTION_HOURS) return null;
  return value;
}

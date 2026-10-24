import { Link } from "@tanstack/react-router";
import { Cable } from "lucide-react";
import { useTranslation } from "react-i18next";
import { QueryState } from "@/components/shared/query-state";
import type { Connection } from "@/entities/connection/api";
import { cn } from "@/lib/utils";

interface ConnectionHealthProps {
  connections: Connection[];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onRetry: () => void;
}

function healthClass(state: Connection["health_state"]): string {
  switch (state) {
    case "healthy":
      return "border-success/30 bg-success/10 text-success";
    case "degraded":
      return "border-warning/30 bg-warning/10 text-warning";
    case "auth_error":
    case "unavailable":
      return "border-destructive/30 bg-destructive/10 text-destructive";
    default:
      return "border-border bg-secondary text-muted-foreground";
  }
}

/**
 * Read-only provider health for the overview. Generation in every project depends on these, so
 * problems surface here; configuring connections happens on the workspace Connections page.
 */
export function ConnectionHealth({
  connections,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: ConnectionHealthProps) {
  const { t } = useTranslation("workspace-overview");

  return (
    <section
      aria-labelledby="overview-connections"
      className="space-y-3 rounded-xl border border-border bg-card p-4 shadow-2xs"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 id="overview-connections" className="flex items-center gap-2 text-sm font-semibold">
          <Cable className="size-4 text-primary" aria-hidden="true" />
          {t("connectionHealth.title")}
        </h3>
        <Link
          to="/connections"
          className="text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t("connectionHealth.manage")}
        </Link>
      </div>
      <QueryState
        isLoading={isLoading}
        isError={isError}
        isEmpty={connections.length === 0}
        hasData={connections.length > 0}
        loadingMessage={t("connectionHealth.loading")}
        errorMessage={errorMessage}
        emptyTitle={t("connectionHealth.noConnections")}
        emptyDescription={t("connectionHealth.description")}
        onRetry={onRetry}
      >
        <ul className="space-y-2">
          {connections.map((connection) => (
            <li key={connection.id} className="flex items-center justify-between gap-2 text-sm">
              <span className="min-w-0 truncate">
                {connection.name}{" "}
                <span className="font-mono text-xs text-muted-foreground">
                  {connection.provider_type}
                  {!connection.enabled && " · disabled"}
                </span>
              </span>
              <span
                className={cn(
                  "shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium",
                  healthClass(connection.health_state),
                )}
              >
                {connection.health_state}
              </span>
            </li>
          ))}
        </ul>
      </QueryState>
    </section>
  );
}

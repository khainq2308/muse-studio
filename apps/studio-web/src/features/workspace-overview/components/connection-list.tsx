import { Cable } from "lucide-react";
import type { ConnectionSummary } from "@/entities/connection/api";
import { QueryState } from "@/components/shared/query-state";
import { ResourceCard } from "@/components/shared/resource-card";

interface ConnectionListProps {
  connections: ConnectionSummary[];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onRetry: () => void;
}

function formatLatency(latencyMs: number | null): string {
  if (latencyMs === null) return "—";
  return `${Number(latencyMs.toFixed(1))} ms`;
}

export function ConnectionList({
  connections,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: ConnectionListProps) {
  return (
    <ResourceCard
      icon={<Cable className="size-5" aria-hidden="true" />}
      title="Provider connections"
      description="Providers and account pools"
      count={connections.length}
    >
      <QueryState
        isLoading={isLoading}
        isError={isError}
        isEmpty={connections.length === 0}
        loadingMessage="Loading connections..."
        errorMessage={errorMessage}
        emptyTitle="No connections configured"
        emptyDescription="Provider setup will live in the Connections module."
        onRetry={onRetry}
      >
        <ul className="divide-y divide-border">
          {connections.map((connection) => (
            <li
              key={connection.id}
              className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{connection.name}</p>
                <p className="mt-1 text-xs text-muted-foreground">{connection.provider_type}</p>
              </div>
              <div className="shrink-0 text-right text-xs text-muted-foreground">
                <p>{connection.health_state}</p>
                <p className="mt-1">{formatLatency(connection.latency_ms)}</p>
              </div>
            </li>
          ))}
        </ul>
      </QueryState>
    </ResourceCard>
  );
}

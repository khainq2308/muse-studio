import { Activity, Cable, CheckCircle2, LoaderCircle, Plus, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { QueryState } from "@/components/shared/query-state";
import { ResourceCard } from "@/components/shared/resource-card";
import { Button } from "@/components/ui/button";
import { useCreateConnection, useTestConnection } from "@/entities/connection/api";
import { authErrorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

interface ConnectionListProps {
  connections: components["schemas"]["ConnectionOut"][];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onRetry: () => void;
}

function formatLatency(latencyMs: number | null): string {
  if (latencyMs === null) return "—";
  return `${Number(latencyMs.toFixed(1))} ms`;
}

function healthBadgeClass(state: components["schemas"]["ConnectionHealthState"]): string {
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

export function ConnectionList({
  connections,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: ConnectionListProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const createMutation = useCreateConnection();
  const testMutation = useTestConnection();

  const [showCreateForm, setShowCreateForm] = useState(false);
  const [name, setName] = useState("");
  const [providerType, setProviderType] = useState("muse2api");
  const [baseUrl, setBaseUrl] = useState("https://api.muse2api.example.com");
  const [apiKey, setApiKey] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(2);
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [testingId, setTestingId] = useState<string | null>(null);

  const canSubmit =
    name.trim().length > 0 && baseUrl.trim().length > 0 && !createMutation.isPending;

  return (
    <ResourceCard
      icon={<Cable className="size-5" aria-hidden="true" />}
      title="Provider connections"
      description="Providers and account pools"
      count={connections.length}
      action={
        <Button
          type="button"
          size="sm"
          variant={showCreateForm ? "outline" : "secondary"}
          onClick={() => {
            createMutation.reset();
            setShowCreateForm((current) => !current);
          }}
        >
          {showCreateForm ? (
            <>
              <X className="size-4" aria-hidden="true" />
              Close
            </>
          ) : (
            <>
              <Plus className="size-4" aria-hidden="true" />
              Add provider
            </>
          )}
        </Button>
      }
    >
      {showCreateForm && (
        <form
          aria-label="Create provider connection"
          className="mb-5 space-y-3.5 rounded-xl border border-primary/20 bg-secondary/40 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!canSubmit) return;
            createMutation.mutate(
              {
                name: name.trim(),
                provider_type: providerType,
                base_url: baseUrl.trim(),
                api_key: apiKey.trim() || null,
                max_concurrency: maxConcurrency,
                timeout_seconds: timeoutSeconds,
                enabled: true,
              },
              {
                onSuccess: () => {
                  setName("");
                  setApiKey("");
                  setShowCreateForm(false);
                },
              },
            );
          }}
        >
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-foreground">New provider connection</h3>
            <span className="text-xs text-muted-foreground">POST /api/v1/connections</span>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs font-medium">
              <span>Connection name</span>
              <input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Primary muse2api Gateway"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>

            <label className="space-y-1 text-xs font-medium">
              <span>Provider adapter</span>
              <select
                value={providerType}
                onChange={(event) => setProviderType(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="muse2api">muse2api</option>
                <option value="mock">mock (deterministic test provider)</option>
              </select>
            </label>

            <label className="space-y-1 text-xs font-medium sm:col-span-2">
              <span>Base URL</span>
              <input
                required
                type="url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.muse2api.example.com"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>

            <label className="space-y-1 text-xs font-medium sm:col-span-2">
              <span>API key (optional if using account pool)</span>
              <input
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Encrypted at rest; never returned in plaintext"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>

            <label className="space-y-1 text-xs font-medium">
              <span>Max concurrency</span>
              <input
                type="number"
                min={1}
                max={32}
                value={maxConcurrency}
                onChange={(event) => setMaxConcurrency(Math.max(1, Number(event.target.value)))}
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>

            <label className="space-y-1 text-xs font-medium">
              <span>Timeout (seconds)</span>
              <input
                type="number"
                min={5}
                max={300}
                value={timeoutSeconds}
                onChange={(event) => setTimeoutSeconds(Math.max(5, Number(event.target.value)))}
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
          </div>

          {createMutation.isError && (
            <p role="alert" className="text-xs text-destructive">
              {authErrorMessage(createMutation.error, activeAdminToken, "create connection")}
            </p>
          )}

          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setShowCreateForm(false)}
            >
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={!canSubmit}>
              {createMutation.isPending ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  Saving...
                </>
              ) : (
                "Save connection"
              )}
            </Button>
          </div>
        </form>
      )}

      {testMutation.isError && (
        <p role="alert" className="mb-3 text-xs text-destructive">
          {authErrorMessage(testMutation.error, activeAdminToken, "test connection")}
        </p>
      )}

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
            <li key={connection.id} className="flex flex-col gap-2.5 py-3.5 first:pt-0 last:pb-0">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-semibold text-foreground">
                      {connection.name}
                    </p>
                    <span className="rounded-md border border-border bg-secondary/70 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                      {connection.provider_type}
                    </span>
                  </div>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {connection.base_url} · max {connection.max_concurrency} concurrent · key{" "}
                    <span className="font-mono">{connection.api_key_masked}</span>
                  </p>
                </div>

                <div className="shrink-0 text-right text-xs">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-medium",
                      healthBadgeClass(connection.health_state),
                    )}
                  >
                    {connection.health_state === "healthy" ? (
                      <CheckCircle2 className="size-3" aria-hidden="true" />
                    ) : connection.health_state === "auth_error" ||
                      connection.health_state === "unavailable" ? (
                      <ShieldAlert className="size-3" aria-hidden="true" />
                    ) : null}
                    {connection.health_state}
                  </span>
                  <p className="mt-1 font-mono text-muted-foreground">
                    {formatLatency(connection.latency_ms)}
                  </p>
                </div>
              </div>

              {connection.last_error_redacted && (
                <p className="rounded-md border border-destructive/25 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
                  {connection.last_error_redacted}
                </p>
              )}

              <div className="flex items-center justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={testMutation.isPending && testingId === connection.id}
                  onClick={() => {
                    setTestingId(connection.id);
                    testMutation.mutate(connection.id, {
                      onSuccess: () => setTestingId(null),
                      onError: () => setTestingId(null),
                    });
                  }}
                >
                  {testMutation.isPending && testingId === connection.id ? (
                    <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                  ) : (
                    <Activity className="size-3.5" aria-hidden="true" />
                  )}
                  Test & probe
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </QueryState>
    </ResourceCard>
  );
}

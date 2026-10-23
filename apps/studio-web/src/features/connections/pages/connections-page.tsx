import {
  Activity,
  Cable,
  CheckCircle2,
  KeyRound,
  LoaderCircle,
  Plus,
  Server,
  ShieldAlert,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import {
  useConnectionAccounts,
  useConnections,
  useCreateAccount,
  useCreateConnection,
  useDeleteAccount,
  useTestAccount,
  useTestConnection,
  useUpdateAccount,
  type Connection,
} from "@/entities/connection/api";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

interface ConnectionsPageProps {
  projectId: string;
}

function formatLatency(latencyMs: number | null): string {
  if (latencyMs === null) return "—";
  return `${Number(latencyMs.toFixed(1))} ms`;
}

function healthBadgeClass(state: Connection["health_state"]): string {
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

function AccountPoolPanel({ connection }: { connection: Connection }) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const accountsQuery = useConnectionAccounts(connection.id);
  const createAccount = useCreateAccount(connection.id);
  const updateAccount = useUpdateAccount(connection.id);
  const deleteAccount = useDeleteAccount(connection.id);
  const testAccount = useTestAccount(connection.id);

  const [showAddAccount, setShowAddAccount] = useState(false);
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [proxyUrl, setProxyUrl] = useState("");
  const [priority, setPriority] = useState(100);
  const [testingAccountId, setTestingAccountId] = useState<string | null>(null);
  const [deletingAccountId, setDeletingAccountId] = useState<string | null>(null);

  const accounts = accountsQuery.data ?? [];
  const capabilitiesEntries = Object.entries(connection.capabilities_snapshot ?? {});

  return (
    <div className="space-y-4 rounded-xl border border-border bg-secondary/25 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-2">
            <Server className="size-4 text-primary" aria-hidden="true" />
            <h3 className="text-base font-semibold">{connection.name} — Account Pool</h3>
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Multi-account rotation pool with priority weighting, optional per-account proxy, and
            live capability probing. Secrets are encrypted at rest and masked in responses.
          </p>
        </div>

        <Button
          type="button"
          size="sm"
          variant={showAddAccount ? "outline" : "default"}
          onClick={() => {
            createAccount.reset();
            setShowAddAccount((current) => !current);
          }}
        >
          {showAddAccount ? (
            <>
              <X className="size-4" aria-hidden="true" />
              Close
            </>
          ) : (
            <>
              <Plus className="size-4" aria-hidden="true" />
              Add pool account
            </>
          )}
        </Button>
      </div>

      {capabilitiesEntries.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-3">
          <p className="text-xs font-semibold text-foreground">
            Observed Capability Probe Snapshot
          </p>
          <dl className="mt-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            {capabilitiesEntries.slice(0, 8).map(([key, value]) => (
              <div key={key} className="min-w-0">
                <dt className="truncate font-mono text-[11px] text-muted-foreground">{key}</dt>
                <dd className="truncate font-medium text-foreground">
                  {typeof value === "object" && value !== null
                    ? Array.isArray(value)
                      ? `${value.length} items`
                      : "configured"
                    : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {showAddAccount && (
        <form
          aria-label="Add pool account"
          className="space-y-3 rounded-xl border border-primary/20 bg-card p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!label.trim() || !apiKey.trim() || createAccount.isPending) return;
            createAccount.mutate(
              {
                label: label.trim(),
                api_key: apiKey.trim(),
                proxy_url: proxyUrl.trim() || null,
                priority,
                enabled: true,
              },
              {
                onSuccess: () => {
                  setLabel("");
                  setApiKey("");
                  setProxyUrl("");
                  setShowAddAccount(false);
                },
              },
            );
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs font-medium">
              <span>Account label</span>
              <input
                required
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                placeholder="e.g. Account #01 (Primary)"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>Priority (lower = preferred)</span>
              <input
                type="number"
                min={1}
                max={1000}
                value={priority}
                onChange={(event) => setPriority(Number(event.target.value))}
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1 text-xs font-medium sm:col-span-2">
              <span>Account API key</span>
              <input
                required
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Provider bearer key or token"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1 text-xs font-medium sm:col-span-2">
              <span>Proxy URL (optional, credentials redacted in UI)</span>
              <input
                value={proxyUrl}
                onChange={(event) => setProxyUrl(event.target.value)}
                placeholder="http://proxy.example.internal:8080"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
          </div>

          {createAccount.isError && (
            <p role="alert" className="text-xs text-destructive">
              {authErrorMessage(createAccount.error, activeAdminToken, "create account")}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setShowAddAccount(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={!label.trim() || !apiKey.trim() || createAccount.isPending}
            >
              {createAccount.isPending ? "Saving..." : "Save account"}
            </Button>
          </div>
        </form>
      )}

      <QueryState
        isLoading={accountsQuery.isPending}
        isError={accountsQuery.isError}
        isEmpty={accounts.length === 0}
        hasData={accounts.length > 0}
        loadingMessage="Loading pool accounts..."
        errorMessage={authErrorMessage(
          accountsQuery.error,
          activeAdminToken,
          "connection accounts",
        )}
        emptyTitle="No pool accounts configured"
        emptyDescription="Add one or more provider accounts to enable priority failover and per-account rate isolation."
        onRetry={() => void accountsQuery.refetch()}
      >
        <ul className="space-y-2.5">
          {accounts.map((account) => (
            <li
              key={account.id}
              className="flex flex-col justify-between gap-3 rounded-xl border border-border bg-card p-3.5 sm:flex-row sm:items-center"
            >
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <KeyRound className="size-3.5 text-primary" aria-hidden="true" />
                  <span className="text-sm font-semibold">{account.label}</span>
                  <span
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                      healthBadgeClass(account.health_state),
                    )}
                  >
                    {account.health_state}
                  </span>
                  <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                    priority {account.priority}
                  </span>
                </div>
                <p className="font-mono text-xs text-muted-foreground">
                  key: {account.api_key_masked} · latency: {formatLatency(account.latency_ms)}
                  {account.proxy_display_host ? ` · proxy: ${account.proxy_display_host}` : ""}
                </p>
                {account.last_error_redacted && (
                  <p className="text-xs text-destructive">{account.last_error_redacted}</p>
                )}
              </div>

              <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={updateAccount.isPending}
                  onClick={() => updateAccount.mutate(account.id, { enabled: !account.enabled })}
                >
                  {account.enabled ? "Disable" : "Enable"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={testAccount.isPending && testingAccountId === account.id}
                  onClick={() => {
                    setTestingAccountId(account.id);
                    testAccount.mutate(account.id, {
                      onSuccess: () => setTestingAccountId(null),
                      onError: () => setTestingAccountId(null),
                    });
                  }}
                >
                  {testAccount.isPending && testingAccountId === account.id ? (
                    <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                  ) : (
                    <Activity className="size-3.5" aria-hidden="true" />
                  )}
                  Test
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={deleteAccount.isPending && deletingAccountId === account.id}
                  onClick={() => {
                    setDeletingAccountId(account.id);
                    deleteAccount.mutate(account.id, {
                      onSuccess: () => setDeletingAccountId(null),
                      onError: () => setDeletingAccountId(null),
                    });
                  }}
                >
                  <Trash2 className="size-3.5 text-destructive" aria-hidden="true" />
                  Remove
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </QueryState>
    </div>
  );
}

export function ConnectionsPage({ projectId }: ConnectionsPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const connectionsQuery = useConnections();
  const createConnection = useCreateConnection();
  const testConnection = useTestConnection();

  const [selectedConnectionId, setSelectedConnectionId] = useState<string | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [name, setName] = useState("");
  const [providerType, setProviderType] = useState("muse2api");
  const [baseUrl, setBaseUrl] = useState("https://api.muse2api.example.com");
  const [apiKey, setApiKey] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(2);
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [testingId, setTestingId] = useState<string | null>(null);

  const connections = connectionsQuery.data ?? [];
  const activeConnection =
    connections.find((item) => item.id === selectedConnectionId) ?? connections[0] ?? null;

  return (
    <div data-testid="connections-page" data-project-id={projectId} className="space-y-6">
      <div className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Cable className="size-5 text-primary" aria-hidden="true" />
            <h2 className="text-xl font-semibold tracking-tight">
              Provider Connections & Account Pools
            </h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Configure video/image generation gateways, run health and capability probes, and manage
            prioritized account pools.
          </p>
        </div>

        <Button
          type="button"
          size="sm"
          variant={showCreateForm ? "outline" : "default"}
          onClick={() => {
            createConnection.reset();
            setShowCreateForm((current) => !current);
          }}
        >
          {showCreateForm ? (
            <>
              <X className="size-4" aria-hidden="true" />
              Close form
            </>
          ) : (
            <>
              <Plus className="size-4" aria-hidden="true" />
              New connection
            </>
          )}
        </Button>
      </div>

      {showCreateForm && (
        <form
          aria-label="Create provider connection"
          className="space-y-4 rounded-xl border border-primary/20 bg-secondary/40 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!name.trim() || !baseUrl.trim() || createConnection.isPending) return;
            createConnection.mutate(
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
                onSuccess: (created) => {
                  setName("");
                  setApiKey("");
                  setShowCreateForm(false);
                  setSelectedConnectionId(created.id);
                },
              },
            );
          }}
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="space-y-1 text-xs font-medium">
              <span>Connection name</span>
              <input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Studio muse2api Cluster"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>Provider type</span>
              <select
                value={providerType}
                onChange={(event) => setProviderType(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="muse2api">muse2api</option>
                <option value="mock">mock</option>
              </select>
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>Base URL</span>
              <input
                required
                type="url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>Default API key (optional)</span>
              <input
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Masked & encrypted at rest"
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
              <span>Timeout (s)</span>
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

          {createConnection.isError && (
            <p role="alert" className="text-xs text-destructive">
              {authErrorMessage(createConnection.error, activeAdminToken, "create connection")}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setShowCreateForm(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={!name.trim() || !baseUrl.trim() || createConnection.isPending}
            >
              {createConnection.isPending ? "Creating..." : "Create connection"}
            </Button>
          </div>
        </form>
      )}

      <QueryState
        isLoading={connectionsQuery.isPending}
        isError={connectionsQuery.isError}
        isEmpty={connections.length === 0}
        hasData={connections.length > 0}
        loadingMessage="Loading provider connections..."
        errorMessage={authErrorMessage(
          connectionsQuery.error,
          activeAdminToken,
          "provider connections",
        )}
        emptyTitle="No provider connections configured"
        emptyDescription="Create a muse2api or mock provider connection above to begin queuing shot generations."
        onRetry={() => void connectionsQuery.refetch()}
      >
        <div className="grid gap-6 lg:grid-cols-12">
          <div className="space-y-3 lg:col-span-5">
            {connections.map((conn) => {
              const isSelected = activeConnection?.id === conn.id;
              return (
                <div
                  key={conn.id}
                  onClick={() => setSelectedConnectionId(conn.id)}
                  className={cn(
                    "cursor-pointer rounded-xl border p-4 transition-all",
                    isSelected
                      ? "border-primary bg-primary/5 shadow-xs"
                      : "border-border bg-background hover:border-primary/40",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-sm font-semibold">{conn.name}</p>
                        <span className="rounded border border-border bg-secondary px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                          {conn.provider_type}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-xs text-muted-foreground">{conn.base_url}</p>
                      <p className="mt-1 font-mono text-xs text-muted-foreground">
                        max {conn.max_concurrency} concurrent · timeout {conn.timeout_seconds}s ·
                        key {conn.api_key_masked}
                      </p>
                    </div>

                    <div className="shrink-0 text-right text-xs">
                      <span
                        className={cn(
                          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-medium",
                          healthBadgeClass(conn.health_state),
                        )}
                      >
                        {conn.health_state === "healthy" ? (
                          <CheckCircle2 className="size-3" aria-hidden="true" />
                        ) : conn.health_state === "auth_error" ||
                          conn.health_state === "unavailable" ? (
                          <ShieldAlert className="size-3" aria-hidden="true" />
                        ) : null}
                        {conn.health_state}
                      </span>
                      <p className="mt-1 font-mono text-muted-foreground">
                        {formatLatency(conn.latency_ms)}
                      </p>
                    </div>
                  </div>

                  {conn.last_error_redacted && (
                    <p className="mt-2 rounded border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
                      {conn.last_error_redacted}
                    </p>
                  )}

                  <div className="mt-3 flex items-center justify-between gap-2">
                    <span className="text-[11px] text-muted-foreground">
                      {conn.last_checked_at
                        ? `Checked ${new Date(conn.last_checked_at).toLocaleTimeString()}`
                        : "Never probed"}
                    </span>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={testConnection.isPending && testingId === conn.id}
                      onClick={(event) => {
                        event.stopPropagation();
                        setTestingId(conn.id);
                        testConnection.mutate(conn.id, {
                          onSuccess: () => setTestingId(null),
                          onError: () => setTestingId(null),
                        });
                      }}
                    >
                      {testConnection.isPending && testingId === conn.id ? (
                        <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                      ) : (
                        <Activity className="size-3.5" aria-hidden="true" />
                      )}
                      Test & probe
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="lg:col-span-7">
            {activeConnection && (
              <AccountPoolPanel key={activeConnection.id} connection={activeConnection} />
            )}
          </div>
        </div>
      </QueryState>
    </div>
  );
}

export default ConnectionsPage;

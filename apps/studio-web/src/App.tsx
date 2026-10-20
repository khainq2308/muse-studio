import { AlertCircle, Cable, CheckCircle2, Compass, Film, LoaderCircle, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useConnections } from "@/entities/connection/api";
import { useHealth } from "@/entities/system/api";
import { useProjects } from "@/entities/project/api";
import { ApiError } from "@/lib/api/client";
import { useSessionStore } from "@/lib/stores/session-store";

function isAuthenticationError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.status);
}

function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
      <div className="flex items-start gap-2 text-sm text-muted-foreground">
        <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
        <p>{message}</p>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw aria-hidden="true" />
        Retry
      </Button>
    </div>
  );
}

export function App() {
  const [draftAdminToken, setDraftAdminToken] = useState("");
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const setAdminToken = useSessionStore((state) => state.setAdminToken);
  const clearAdminToken = useSessionStore((state) => state.clearAdminToken);

  const projectQuery = useProjects();
  const connectionQuery = useConnections();
  const healthQuery = useHealth();

  const projects = projectQuery.data ?? [];
  const connections = connectionQuery.data ?? [];
  const authenticationRequired =
    isAuthenticationError(projectQuery.error) || isAuthenticationError(connectionQuery.error);

  const apiStatus = authenticationRequired
    ? "Admin token required"
    : healthQuery.isPending
      ? "Checking..."
      : healthQuery.isError
        ? "Offline (start Orchestrator)"
        : healthQuery.data?.status === "ok"
          ? "Online"
          : "Unknown";

  const statusClass = apiStatus === "Online"
    ? "text-success"
    : authenticationRequired
      ? "text-warning"
      : "text-destructive";

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-5 border-b border-border pb-6 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Film className="size-5" aria-hidden="true" />
              </div>
              <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">MuseStudio</h1>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              AI video production workspace · Frontend foundation
            </p>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <form
              className="flex flex-col gap-2 sm:flex-row"
              onSubmit={(event) => {
                event.preventDefault();
                setAdminToken(draftAdminToken);
              }}
            >
              <input
                aria-label="Studio admin token"
                type="password"
                autoComplete="off"
                placeholder="Studio admin token"
                value={draftAdminToken}
                onChange={(event) => setDraftAdminToken(event.target.value)}
                className="h-10 min-w-0 rounded-md border border-input bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring sm:w-64"
              />
              <Button type="submit">Connect</Button>
              {activeAdminToken && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setDraftAdminToken("");
                    clearAdminToken();
                  }}
                >
                  Disconnect
                </Button>
              )}
            </form>

            <div className="flex items-center gap-2 rounded-full border border-border bg-card px-3 py-2 text-xs">
              {apiStatus === "Online" ? (
                <CheckCircle2 className="size-4 text-success" aria-hidden="true" />
              ) : (
                <span className="size-2 rounded-full bg-current" aria-hidden="true" />
              )}
              <span className="text-muted-foreground">API</span>
              <span className={statusClass} aria-live="polite">{apiStatus}</span>
            </div>
          </div>
        </header>

        <section aria-label="Workspace overview" className="grid gap-4 py-6 lg:grid-cols-2">
          <article className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-sm">
            <div className="mb-5 flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Film className="size-5" aria-hidden="true" />
                </div>
                <div>
                  <h2 className="font-semibold">Projects</h2>
                  <p className="text-sm text-muted-foreground">Production workspaces</p>
                </div>
              </div>
              <span className="rounded-full bg-secondary px-2.5 py-1 text-xs font-medium tabular-nums">
                {projects.length}
              </span>
            </div>

            {projectQuery.isPending ? (
              <p className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                Loading projects...
              </p>
            ) : projectQuery.isError ? (
              <LoadError
                message={isAuthenticationError(projectQuery.error)
                  ? "Admin token required to read projects."
                  : "Could not load projects. Check the Orchestrator and retry."}
                onRetry={() => void projectQuery.refetch()}
              />
            ) : projects.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center">
                <p className="text-sm font-medium">No projects yet</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Create a project to begin organizing scripts, shots, and takes.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {projects.map((project) => (
                  <li key={project.id} className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{project.name}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {project.aspect_ratio} · {project.fps} fps
                      </p>
                    </div>
                    <div className="shrink-0 text-right text-xs text-muted-foreground">
                      <p>Revision {project.current_revision_num}</p>
                      <p className="mt-1">{project.asset_count} assets</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </article>

          <article className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-sm">
            <div className="mb-5 flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Cable className="size-5" aria-hidden="true" />
                </div>
                <div>
                  <h2 className="font-semibold">Provider connections</h2>
                  <p className="text-sm text-muted-foreground">Providers and account pools</p>
                </div>
              </div>
              <span className="rounded-full bg-secondary px-2.5 py-1 text-xs font-medium tabular-nums">
                {connections.length}
              </span>
            </div>

            {connectionQuery.isPending ? (
              <p className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                Loading connections...
              </p>
            ) : connectionQuery.isError ? (
              <LoadError
                message={isAuthenticationError(connectionQuery.error)
                  ? "Admin token required to read provider connections."
                  : "Could not load provider connections. Check the Orchestrator and retry."}
                onRetry={() => void connectionQuery.refetch()}
              />
            ) : connections.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center">
                <p className="text-sm font-medium">No connections configured</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Provider setup will live in the Connections module.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {connections.map((connection) => (
                  <li key={connection.id} className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{connection.name}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{connection.provider_type}</p>
                    </div>
                    <div className="shrink-0 text-right text-xs text-muted-foreground">
                      <p>{connection.health_state}</p>
                      <p className="mt-1">{connection.latency_ms ?? 0} ms</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </article>
        </section>

        <section aria-labelledby="roadmap-title" className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Compass className="size-5" aria-hidden="true" />
            </div>
            <div>
              <h2 id="roadmap-title" className="font-semibold">Frontend roadmap</h2>
              <p className="text-sm text-muted-foreground">Foundation first; feature modules follow as separate phases.</p>
            </div>
          </div>
          <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <li className="rounded-lg border border-primary/30 bg-primary/5 p-3">
              <p className="text-sm font-medium">Phase 0a · Foundation</p>
              <p className="mt-1 text-xs text-muted-foreground">API client, router, theme, locked dependencies, and test gates.</p>
            </li>
            <li className="rounded-lg border border-border p-3">
              <p className="text-sm font-medium">Phase 0b · OpenAPI</p>
              <p className="mt-1 text-xs text-muted-foreground">Offline schema export, generated types, and drift checks.</p>
            </li>
            <li className="rounded-lg border border-border p-3">
              <p className="text-sm font-medium">Phase 0c · Typed routes</p>
              <p className="mt-1 text-xs text-muted-foreground">Audit response models and enum contracts by router.</p>
            </li>
            <li className="rounded-lg border border-border p-3">
              <p className="text-sm font-medium">Phase 1 · Production workspace</p>
              <p className="mt-1 text-xs text-muted-foreground">Shot board, media previews, and queue monitor.</p>
            </li>
          </ol>
        </section>
      </div>
    </main>
  );
}

export default App;

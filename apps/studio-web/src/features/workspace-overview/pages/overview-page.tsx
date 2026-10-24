import { useQueries } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { FolderPlus } from "lucide-react";
import { QueryState } from "@/components/shared/query-state";
import { useConnections } from "@/entities/connection/api";
import { useProjects } from "@/entities/project/api";
import { projectShotsQueryOptions } from "@/entities/shot/api";
import { ConnectionHealth } from "@/features/workspace-overview/components/connection-health";
import {
  ProjectProgressCard,
  type ProgressState,
} from "@/features/workspace-overview/components/project-progress-card";
import { deriveNextStep, summarizeProgress } from "@/features/workspace-overview/progress";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

/**
 * Projects whose shot lists are loaded for progress. The API has no project summary endpoint,
 * so each card costs one `GET /projects/{id}/shots`; older projects stay on the Projects page.
 */
export const RECENT_PROJECT_LIMIT = 4;

export function OverviewPage() {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const projectQuery = useProjects();
  const connectionQuery = useConnections();

  const allProjects = projectQuery.data ?? [];
  const recentProjects = [...allProjects]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, RECENT_PROJECT_LIMIT);

  const shotQueries = useQueries({
    queries: recentProjects.map((project) => projectShotsQueryOptions(sessionVersion, project.id)),
  });

  function progressState(index: number): ProgressState {
    const query = shotQueries[index];
    if (!query || query.isPending) return { status: "loading" };
    if (query.isError) {
      return {
        status: "error",
        message: authErrorMessage(query.error, activeAdminToken, "shots"),
        onRetry: () => void query.refetch(),
      };
    }
    return {
      status: "ready",
      progress: summarizeProgress(query.data),
      nextStep: deriveNextStep(query.data),
    };
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Overview</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Recently updated projects, each with the next step its shots call for.
          </p>
        </div>
        <Link
          to="/projects"
          className="inline-flex h-9 items-center gap-2 self-start rounded-lg border border-border bg-background px-3.5 text-sm font-medium transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:self-auto"
        >
          <FolderPlus className="size-4 text-muted-foreground" aria-hidden="true" />
          All projects & new project
        </Link>
      </div>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <section aria-labelledby="overview-recent" className="min-w-0 space-y-3">
          <div className="flex items-baseline justify-between gap-2">
            <h3 id="overview-recent" className="text-base font-semibold">
              Recently updated
            </h3>
            {allProjects.length > recentProjects.length && (
              <Link
                to="/projects"
                className="text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                View all {allProjects.length} projects
              </Link>
            )}
          </div>
          <QueryState
            isLoading={projectQuery.isPending}
            isError={projectQuery.isError}
            isEmpty={allProjects.length === 0}
            hasData={allProjects.length > 0}
            loadingMessage="Loading projects..."
            errorMessage={authErrorMessage(projectQuery.error, activeAdminToken, "projects")}
            emptyTitle="No projects yet"
            emptyDescription="Create a project on the Projects page, then import a script to start."
            onRetry={() => void projectQuery.refetch()}
          >
            <ul aria-label="Recently updated projects" className="grid gap-3 lg:grid-cols-2">
              {recentProjects.map((project, index) => (
                <ProjectProgressCard
                  key={project.id}
                  project={project}
                  state={progressState(index)}
                />
              ))}
            </ul>
          </QueryState>
        </section>

        <ConnectionHealth
          connections={connectionQuery.data ?? []}
          isLoading={connectionQuery.isPending}
          isError={connectionQuery.isError}
          errorMessage={authErrorMessage(
            connectionQuery.error,
            activeAdminToken,
            "provider connections",
          )}
          onRetry={() => void connectionQuery.refetch()}
        />
      </div>
    </div>
  );
}

export default OverviewPage;

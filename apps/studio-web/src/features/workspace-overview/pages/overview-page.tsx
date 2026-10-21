import { useConnections } from "@/entities/connection/api";
import { useProjects } from "@/entities/project/api";
import { ConnectionList } from "@/features/workspace-overview/components/connection-list";
import { ProjectList } from "@/features/workspace-overview/components/project-list";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

export function OverviewPage() {
  const projectQuery = useProjects();
  const connectionQuery = useConnections();
  const activeAdminToken = useSessionStore((state) => state.adminToken);

  return (
    <section aria-label="Workspace overview" className="grid gap-4 lg:grid-cols-2">
      <ProjectList
        projects={projectQuery.data ?? []}
        isLoading={projectQuery.isPending}
        isError={projectQuery.isError}
        errorMessage={authErrorMessage(projectQuery.error, activeAdminToken, "projects")}
        onRetry={() => void projectQuery.refetch()}
      />
      <ConnectionList
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
    </section>
  );
}

export default OverviewPage;

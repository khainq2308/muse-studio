import { Film } from "lucide-react";
import type { components } from "@/lib/api/schema";
import { QueryState } from "@/components/shared/query-state";
import { ResourceCard } from "@/components/shared/resource-card";

interface ProjectListProps {
  projects: components["schemas"]["ProjectOut"][];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onRetry: () => void;
}

export function ProjectList({
  projects,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: ProjectListProps) {
  return (
    <ResourceCard
      icon={<Film className="size-5" aria-hidden="true" />}
      title="Projects"
      description="Production workspaces"
      count={projects.length}
    >
      <QueryState
        isLoading={isLoading}
        isError={isError}
        isEmpty={projects.length === 0}
        loadingMessage="Loading projects..."
        errorMessage={errorMessage}
        emptyTitle="No projects yet"
        emptyDescription="Create a project to begin organizing scripts, shots, and takes."
        onRetry={onRetry}
      >
        <ul className="divide-y divide-border">
          {projects.map((project) => (
            <li
              key={project.id}
              className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0"
            >
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
      </QueryState>
    </ResourceCard>
  );
}

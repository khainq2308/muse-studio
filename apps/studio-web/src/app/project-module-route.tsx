import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { Film, LoaderCircle } from "lucide-react";
import { useCallback } from "react";
import NotFoundPage from "@/app/not-found-page";
import { ProjectModuleNav } from "@/app/project-module-nav";
import { useProjects } from "@/entities/project/api";
import ConnectionsPage from "@/features/connections";
import ProjectsPage from "@/features/projects";
import QueueMonitorPage from "@/features/queue-monitor";
import ScriptStudioPage from "@/features/script-studio";
import ShotBoardPage, {
  type ShotBoardNavigateOptions,
  type ShotBoardSearch,
} from "@/features/shot-board";
import SystemOpsPage from "@/features/system-ops";
import TimelineEditorPage from "@/features/timeline-editor";
import WorkflowCanvasPage from "@/features/workflow-canvas";

export default function ProjectModuleRoute() {
  const { projectId, module } = useParams({ from: "/_shell/projects/$projectId/$module" });
  // The API has no GET /api/v1/projects/{project_id}; the unpaginated project list is the
  // contract for checking that a deep-linked project exists.
  const projectsQuery = useProjects();
  const moduleTitle = module.split("-").filter(Boolean).join(" ");
  const heading = moduleTitle.charAt(0).toUpperCase() + moduleTitle.slice(1);

  if (projectsQuery.isPending) {
    return (
      <p role="status" className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
        Loading project...
      </p>
    );
  }

  const activeProject = projectsQuery.data?.find((project) => project.id === projectId);

  // Only a successful list proves absence. On auth/network errors the module renders and
  // surfaces its own error state instead of claiming the project does not exist.
  if (projectsQuery.isSuccess && !activeProject) {
    return (
      <section
        data-testid="project-not-found"
        data-project-id={projectId}
        className="rounded-xl border border-border bg-card p-6"
      >
        <NotFoundPage
          headingLevel={2}
          title="Project not found"
          message={`No project with ID "${projectId}" exists, or it has been deleted.`}
        />
      </section>
    );
  }

  // Remount per project so no local UI state (filters, open editors) leaks across projects.
  const content =
    module === "script-studio" ? (
      <ScriptStudioPage key={projectId} projectId={projectId} />
    ) : module === "queue-monitor" ? (
      <QueueMonitorPage key={projectId} projectId={projectId} />
    ) : module === "shot-board" ? (
      <ShotBoardModule key={projectId} projectId={projectId} />
    ) : module === "workflow-canvas" ? (
      <WorkflowCanvasPage key={projectId} projectId={projectId} />
    ) : module === "timeline-editor" ? (
      <TimelineEditorPage key={projectId} projectId={projectId} />
    ) : module === "connections" ? (
      <ConnectionsPage key={projectId} projectId={projectId} />
    ) : module === "projects" ? (
      <ProjectsPage key={projectId} projectId={projectId} />
    ) : module === "system-ops" ? (
      <SystemOpsPage key={projectId} projectId={projectId} />
    ) : (
      <div data-testid="project-module-placeholder" className="space-y-2">
        <h2 className="text-xl font-semibold">{heading}</h2>
        <p className="text-sm text-muted-foreground">This module is a placeholder in Phase 0a.</p>
        <p className="text-xs text-muted-foreground">Project: {projectId}</p>
      </div>
    );

  return (
    <>
      {activeProject && activeProject.name && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-secondary/45 px-4 py-2.5 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Film className="size-3.5 text-primary" aria-hidden="true" />
            <span className="font-semibold text-foreground">{activeProject.name}</span>
            <span className="font-mono text-muted-foreground">({activeProject.id})</span>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
            {activeProject.aspect_ratio && (
              <span className="rounded bg-card px-2 py-0.5 font-mono">
                {activeProject.aspect_ratio} · {activeProject.fps} fps
              </span>
            )}
            {activeProject.style_preset && (
              <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-primary">
                {activeProject.style_preset}
              </span>
            )}
            {typeof activeProject.current_revision_num === "number" && (
              <span className="font-mono">r{activeProject.current_revision_num}</span>
            )}
          </div>
        </div>
      )}
      <ProjectModuleNav projectId={projectId} activeModule={module} />
      <section
        data-testid="project-module-route"
        data-project-id={projectId}
        data-module={module}
        className="rounded-xl border border-border bg-card p-6 shadow-xs"
      >
        {content}
      </section>
    </>
  );
}

/** Connects the Shot Board to its URL state (`?shot=<id>&tab=<tab>`). */
function ShotBoardModule({ projectId }: { projectId: string }) {
  const search = useSearch({ from: "/_shell/projects/$projectId/$module" });
  const navigate = useNavigate();
  const onSearchChange = useCallback(
    (next: ShotBoardSearch, options?: ShotBoardNavigateOptions) =>
      void navigate({
        to: "/projects/$projectId/$module",
        params: { projectId, module: "shot-board" },
        search: next,
        replace: options?.replace ?? false,
      }),
    [navigate, projectId],
  );
  return <ShotBoardPage projectId={projectId} search={search} onSearchChange={onSearchChange} />;
}

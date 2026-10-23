import { useParams } from "@tanstack/react-router";
import { LoaderCircle } from "lucide-react";
import NotFoundPage from "@/app/not-found-page";
import { useProjects } from "@/entities/project/api";
import { ProjectModuleNav } from "@/app/project-module-nav";
import QueueMonitorPage from "@/features/queue-monitor";
import ScriptStudioPage from "@/features/script-studio";
import ShotBoardPage from "@/features/shot-board";

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

  // Only a successful list proves absence. On auth/network errors the module renders and
  // surfaces its own error state instead of claiming the project does not exist.
  if (projectsQuery.isSuccess && !projectsQuery.data.some((project) => project.id === projectId)) {
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
      <ShotBoardPage key={projectId} projectId={projectId} />
    ) : (
      <div data-testid="project-module-placeholder" className="space-y-2">
        <h2 className="text-xl font-semibold">{heading}</h2>
        <p className="text-sm text-muted-foreground">This module is a placeholder in Phase 0a.</p>
        <p className="text-xs text-muted-foreground">Project: {projectId}</p>
      </div>
    );

  return (
    <>
      <ProjectModuleNav projectId={projectId} activeModule={module} />
      <section
        data-testid="project-module-route"
        data-project-id={projectId}
        data-module={module}
        className="rounded-xl border border-border bg-card p-6"
      >
        {content}
      </section>
    </>
  );
}

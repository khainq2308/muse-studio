import { useParams } from "@tanstack/react-router";
import QueueMonitorPage from "@/features/queue-monitor";

export default function ProjectModuleRoute() {
  const { projectId, module } = useParams({ from: "/_shell/projects/$projectId/$module" });
  const moduleTitle = module.split("-").filter(Boolean).join(" ");
  const heading = moduleTitle.charAt(0).toUpperCase() + moduleTitle.slice(1);

  if (module === "queue-monitor") {
    return (
      <section
        data-testid="project-module-route"
        data-project-id={projectId}
        data-module={module}
        className="rounded-xl border border-border bg-card p-6"
      >
        <QueueMonitorPage key={projectId} projectId={projectId} />
      </section>
    );
  }

  return (
    <section
      data-testid="project-module-route"
      data-project-id={projectId}
      data-module={module}
      className="rounded-xl border border-border bg-card p-6"
    >
      <div data-testid="project-module-placeholder" className="space-y-2">
        <h2 className="text-xl font-semibold">{heading}</h2>
        <p className="text-sm text-muted-foreground">This module is a placeholder in Phase 0a.</p>
        <p className="text-xs text-muted-foreground">Project: {projectId}</p>
      </div>
    </section>
  );
}

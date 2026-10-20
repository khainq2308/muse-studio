import { useParams } from "@tanstack/react-router";
import App from "@/App";

export const PROJECT_MODULES = [
  "projects",
  "connections",
  "script-studio",
  "shot-board",
  "workflow-canvas",
  "queue-monitor",
  "timeline-editor",
  "system-ops",
] as const;

export function isProjectModule(module: string): boolean {
  return (PROJECT_MODULES as readonly string[]).includes(module);
}

// Distinct component makes route matching observable in the Phase 0a smoke test.
export default function ProjectModuleRoute() {
  const { projectId, module } = useParams({ from: "/projects/$projectId/$module" });
  return (
    <div data-testid="project-module-route" data-project-id={projectId} data-module={module}>
      <App />
    </div>
  );
}

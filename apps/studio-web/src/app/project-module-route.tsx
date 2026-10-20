import { useParams } from "@tanstack/react-router";
import App from "@/App";

// Phase 0a placeholder: all registered module slugs temporarily share the legacy dashboard.
// Phase 1 will replace this wrapper with the actual feature-module route components.
// Keeping projects/connections here also preserves the planned module URL contract.
export default function ProjectModuleRoute() {
  const { projectId, module } = useParams({ from: "/projects/$projectId/$module" });
  return (
    <div data-testid="project-module-route" data-project-id={projectId} data-module={module}>
      <App />
    </div>
  );
}

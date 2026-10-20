import { useParams } from "@tanstack/react-router";
import App from "@/App";

// Distinct component makes route matching observable in the Phase 0a smoke test.
export default function ProjectModuleRoute() {
  const { projectId, module } = useParams({ from: "/projects/$projectId/$module" });
  return (
    <div data-testid="project-module-route" data-project-id={projectId} data-module={module}>
      <App />
    </div>
  );
}

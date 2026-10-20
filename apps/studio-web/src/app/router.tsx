import { createRootRoute, createRoute, createRouter, Outlet, useParams } from "@tanstack/react-router";
import App from "../App";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: App,
});

// Temporary route keeps a real project-module deep link navigable before Phase 1 adds features.
// Keeping route params visible lets the smoke test prove this exact route matched.
function ProjectModuleRoute() {
  const { projectId, module } = useParams({ from: "/projects/$projectId/$module" });
  return (
    <div
      data-testid="project-module-route"
      data-project-id={projectId}
      data-module={module}
    >
      <App />
    </div>
  );
}

const projectModuleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/projects/$projectId/$module",
  component: ProjectModuleRoute,
});

const routeTree = rootRoute.addChildren([indexRoute, projectModuleRoute]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

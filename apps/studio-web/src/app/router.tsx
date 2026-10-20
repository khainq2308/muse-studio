import { createRootRoute, createRoute, createRouter, Outlet, redirect } from "@tanstack/react-router";
import App from "@/App";
import ProjectModuleRoute, { isProjectModule } from "@/app/project-module-route";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: App,
});

const projectModuleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/projects/$projectId/$module",
  beforeLoad: ({ params }) => {
    if (!isProjectModule(params.module)) {
      throw redirect({ to: "/" });
    }
  },
  component: ProjectModuleRoute,
});

const routeTree = rootRoute.addChildren([indexRoute, projectModuleRoute]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

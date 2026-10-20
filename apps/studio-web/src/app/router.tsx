import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";
import App from "../App";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: () => <App />,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: App,
});

// Temporary route keeps a real project-module deep link navigable before Phase 1 adds features.
const projectModuleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/projects/$projectId/$module",
  component: App,
});

const routeTree = rootRoute.addChildren([indexRoute, projectModuleRoute]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

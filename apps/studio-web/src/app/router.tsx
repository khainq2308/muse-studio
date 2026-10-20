import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";
import App from "../App";
import ProjectModuleRoute from "./project-module-route";

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
  component: ProjectModuleRoute,
});

const routeTree = rootRoute.addChildren([indexRoute, projectModuleRoute]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

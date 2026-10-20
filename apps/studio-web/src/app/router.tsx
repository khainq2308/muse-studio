import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from "@tanstack/react-router";
import App from "@/App";
import ProjectModuleRoute from "@/app/project-module-route";
import { isProjectModule } from "@/app/project-modules";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: () => (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-3 px-6 py-12">
      <h1 className="text-2xl font-semibold">404 - Page not found</h1>
      <p className="text-muted-foreground">The page you requested doesn't exist.</p>
    </main>
  ),
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

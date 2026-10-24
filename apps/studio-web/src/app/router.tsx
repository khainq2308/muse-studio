import {
  createRootRoute,
  createRoute,
  createRouter,
  notFound,
  Outlet,
  redirect,
  rootRouteId,
} from "@tanstack/react-router";
import AppShell from "@/app/app-shell";
import NotFoundPage from "@/app/not-found-page";
import ProjectModuleRoute from "@/app/project-module-route";
import { isProjectModule, legacyModuleTarget } from "@/app/project-modules";
import ConnectionsPage from "@/features/connections";
import ProjectsPage from "@/features/projects";
import { validateShotBoardSearch } from "@/features/shot-board/search";
import SystemPage from "@/features/system-ops";
import OverviewPage from "@/features/workspace-overview/pages/overview-page";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: () => (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center px-6 py-12">
      <NotFoundPage />
    </main>
  ),
});

const shellRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "_shell",
  component: AppShell,
});

const indexRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/",
  component: OverviewPage,
});

// Workspace-level tools: not scoped to one project, so they never sit under /projects/$projectId.
const projectsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/projects",
  component: ProjectsPage,
});

const connectionsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/connections",
  component: ConnectionsPage,
});

const systemRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/system",
  component: SystemPage,
});

// Opening a project without a module lands on the Shot Board, the centre of production work.
const projectIndexRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/projects/$projectId",
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/projects/$projectId/$module",
      params: { projectId: params.projectId, module: "shot-board" },
      replace: true,
    });
  },
});

const projectModuleRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/projects/$projectId/$module",
  // Optional module navigation state; only the Shot Board reads it (`?shot=&tab=`). Links to
  // another module do not carry it over.
  validateSearch: validateShotBoardSearch,
  beforeLoad: ({ params }) => {
    const legacy = legacyModuleTarget(params.module);
    if (legacy?.kind === "workspace") throw redirect({ to: legacy.to, replace: true });
    if (legacy?.kind === "project") {
      throw redirect({
        to: "/projects/$projectId/$module",
        params: { projectId: params.projectId, module: legacy.module },
        replace: true,
      });
    }
    // Unknown modules get the same 404 page as any other unknown URL.
    if (!isProjectModule(params.module)) {
      throw notFound({ routeId: rootRouteId });
    }
  },
  component: ProjectModuleRoute,
});

const shellChildren = shellRoute.addChildren([
  indexRoute,
  projectsRoute,
  connectionsRoute,
  systemRoute,
  projectIndexRoute,
  projectModuleRoute,
]);
export const routeTree = rootRoute.addChildren([shellChildren]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

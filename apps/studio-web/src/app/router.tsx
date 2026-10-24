import {
  createRootRoute,
  createRoute,
  createRouter,
  notFound,
  Outlet,
  rootRouteId,
} from "@tanstack/react-router";
import AppShell from "@/app/app-shell";
import NotFoundPage from "@/app/not-found-page";
import ProjectModuleRoute from "@/app/project-module-route";
import { isProjectModule } from "@/app/project-modules";
import { validateShotBoardSearch } from "@/features/shot-board/search";
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

const projectModuleRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/projects/$projectId/$module",
  // Optional module navigation state; only the Shot Board reads it (`?shot=&tab=`). Links to
  // another module do not carry it over.
  validateSearch: validateShotBoardSearch,
  beforeLoad: ({ params }) => {
    // Unknown modules get the same 404 page as any other unknown URL.
    if (!isProjectModule(params.module)) {
      throw notFound({ routeId: rootRouteId });
    }
  },
  component: ProjectModuleRoute,
});

const shellChildren = shellRoute.addChildren([indexRoute, projectModuleRoute]);
export const routeTree = rootRoute.addChildren([shellChildren]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

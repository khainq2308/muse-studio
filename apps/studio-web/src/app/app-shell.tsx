import { Link, Outlet, useMatchRoute } from "@tanstack/react-router";
import { Cable, Film, FolderOpen, HardDrive, LayoutDashboard, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { LanguageSwitcher } from "@/components/shared/language-switcher";
import { AdminTokenForm } from "@/features/admin-session/components/admin-token-form";
import { ApiStatusBadge } from "@/features/api-status/components/api-status-badge";
import { cn } from "@/lib/utils";

/** Workspace-wide destinations; project work happens inside a project's own navigation. */
const WORKSPACE_NAV: ReadonlyArray<{
  to: "/" | "/projects" | "/connections" | "/system";
  translationKey: "nav.overview" | "nav.projects" | "nav.connections" | "nav.system";
  icon: LucideIcon;
}> = [
  { to: "/", translationKey: "nav.overview", icon: LayoutDashboard },
  { to: "/projects", translationKey: "nav.projects", icon: FolderOpen },
  { to: "/connections", translationKey: "nav.connections", icon: Cable },
  { to: "/system", translationKey: "nav.system", icon: HardDrive },
];

const NAV_LINK_CLASS =
  "inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const NAV_ACTIVE_CLASS = "bg-secondary text-foreground";

export function AppShell() {
  const { t } = useTranslation();
  const matchRoute = useMatchRoute();
  // Inside a project the Projects area stays highlighted, but only the open project module
  // carries aria-current="page" (exact matching keeps the router from adding it here).
  const inProject = Boolean(matchRoute({ to: "/projects/$projectId", fuzzy: true }));

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="border-b border-border bg-card/85 backdrop-blur-md">
        <div className="mx-auto max-w-[1440px] px-4 py-3 sm:px-6 lg:px-8">
          <header className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-5">
              <Link
                to="/"
                activeOptions={{ exact: true }}
                className="flex items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="flex size-9 items-center justify-center rounded-xl border border-primary/20 bg-primary/10 text-primary">
                  <Film className="size-5" aria-hidden="true" />
                </span>
                <h1 className="text-lg font-bold tracking-tight sm:text-xl">MuseStudio</h1>
              </Link>

              <nav aria-label={t("nav.workspace")}>
                <ul className="flex flex-wrap items-center gap-1">
                  {WORKSPACE_NAV.map(({ to, translationKey, icon: Icon }) => (
                    <li key={to}>
                      <Link
                        to={to}
                        activeOptions={{ exact: true, includeSearch: false }}
                        className={cn(
                          NAV_LINK_CLASS,
                          to === "/projects" && inProject && NAV_ACTIVE_CLASS,
                        )}
                        activeProps={{ className: NAV_ACTIVE_CLASS }}
                      >
                        <Icon className="size-4 shrink-0" aria-hidden="true" />
                        {t(translationKey)}
                      </Link>
                    </li>
                  ))}
                </ul>
              </nav>
            </div>

            <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center">
              <LanguageSwitcher />
              <AdminTokenForm />
              <ApiStatusBadge />
            </div>
          </header>
        </div>
      </div>

      <div className="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
        <Outlet />
      </div>
    </main>
  );
}

export default AppShell;

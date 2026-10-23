import { Link, Outlet } from "@tanstack/react-router";
import { Film, LayoutDashboard, Sparkles } from "lucide-react";
import { AdminTokenForm } from "@/features/admin-session/components/admin-token-form";
import { ApiStatusBadge } from "@/features/api-status/components/api-status-badge";

export function AppShell() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="border-b border-border bg-card/85 backdrop-blur-md">
        <div className="mx-auto max-w-[1440px] px-4 py-3.5 sm:px-6 lg:px-8">
          <header className="flex flex-col gap-3.5 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap items-center gap-3 sm:gap-4">
              <div className="flex items-center gap-2.5">
                <div className="flex size-10 items-center justify-center rounded-xl border border-primary/20 bg-primary/10 text-primary shadow-xs">
                  <Film className="size-5" aria-hidden="true" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h1 className="text-xl font-bold tracking-tight sm:text-2xl">MuseStudio</h1>
                    <span className="hidden items-center gap-1 rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary sm:inline-flex">
                      <Sparkles className="size-3" aria-hidden="true" />
                      Studio Suite
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">AI video production workspace</p>
                </div>
              </div>

              <div className="hidden h-6 w-px bg-border sm:block" aria-hidden="true" />

              <Link
                to="/"
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background px-3 text-xs font-medium text-foreground shadow-2xs transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <LayoutDashboard className="size-3.5 text-muted-foreground" aria-hidden="true" />
                Workspace Overview
              </Link>
            </div>

            <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center">
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

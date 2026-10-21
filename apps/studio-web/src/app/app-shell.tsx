import { Outlet } from "@tanstack/react-router";
import { Film } from "lucide-react";
import { AdminTokenForm } from "@/features/admin-session/components/admin-token-form";
import { ApiStatusBadge } from "@/features/api-status/components/api-status-badge";

export function AppShell() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-5 border-b border-border pb-6 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Film className="size-5" aria-hidden="true" />
              </div>
              <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">MuseStudio</h1>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">AI video production workspace</p>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <AdminTokenForm />
            <ApiStatusBadge />
          </div>
        </header>
        <div className="py-6">
          <Outlet />
        </div>
      </div>
    </main>
  );
}

export default AppShell;

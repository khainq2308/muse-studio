import { Link } from "@tanstack/react-router";
import { BookOpenText, LayoutGrid, ListChecks, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/** Project modules with a real product screen; other modules are still placeholders. */
const NAV_ITEMS: ReadonlyArray<{ module: string; label: string; icon: LucideIcon }> = [
  { module: "script-studio", label: "Script Studio", icon: BookOpenText },
  { module: "shot-board", label: "Shot Board", icon: LayoutGrid },
  { module: "queue-monitor", label: "Queue Monitor", icon: ListChecks },
];

interface ProjectModuleNavProps {
  projectId: string;
  activeModule: string;
}

export function ProjectModuleNav({ projectId, activeModule }: ProjectModuleNavProps) {
  return (
    <nav aria-label="Project modules" className="mb-4">
      <ul className="flex flex-wrap gap-2">
        {NAV_ITEMS.map(({ module, label, icon: Icon }) => {
          const active = module === activeModule;
          return (
            <li key={module}>
              <Link
                to="/projects/$projectId/$module"
                params={{ projectId, module }}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-9 items-center gap-2 rounded-md border px-3 text-sm font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  active
                    ? "border-primary/50 bg-primary/10 text-primary"
                    : "border-border bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                )}
              >
                <Icon className="size-4" aria-hidden="true" />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

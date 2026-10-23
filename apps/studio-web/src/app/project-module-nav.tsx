import { Link } from "@tanstack/react-router";
import {
  BookOpenText,
  Cable,
  Clapperboard,
  FolderCog,
  GitBranch,
  HardDrive,
  LayoutGrid,
  ListChecks,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/** Creative pipeline and operational modules available for every project workspace. */
const NAV_ITEMS: ReadonlyArray<{
  module: string;
  label: string;
  stage?: string;
  icon: LucideIcon;
}> = [
  { module: "script-studio", label: "Script Studio", stage: "01", icon: BookOpenText },
  { module: "shot-board", label: "Shot Board", stage: "02", icon: LayoutGrid },
  { module: "workflow-canvas", label: "Workflow Canvas", stage: "03", icon: GitBranch },
  { module: "queue-monitor", label: "Queue Monitor", stage: "04", icon: ListChecks },
  { module: "timeline-editor", label: "Timeline Editor", stage: "05", icon: Clapperboard },
  { module: "connections", label: "Connections", icon: Cable },
  { module: "projects", label: "Project Settings", icon: FolderCog },
  { module: "system-ops", label: "System & Backups", icon: HardDrive },
];

interface ProjectModuleNavProps {
  projectId: string;
  activeModule: string;
}

export function ProjectModuleNav({ projectId, activeModule }: ProjectModuleNavProps) {
  return (
    <nav aria-label="Project modules" className="mb-5">
      <ul className="flex flex-wrap items-center gap-1.5 rounded-xl border border-border bg-card p-1.5 shadow-2xs">
        {NAV_ITEMS.map(({ module, label, stage, icon: Icon }) => {
          const active = module === activeModule;
          return (
            <li key={module}>
              <Link
                to="/projects/$projectId/$module"
                params={{ projectId, module }}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-9 items-center gap-2 rounded-lg px-3 text-xs font-medium transition-all sm:text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  active
                    ? "border border-primary/25 bg-primary text-primary-foreground shadow-xs"
                    : "border border-transparent bg-transparent text-muted-foreground hover:bg-secondary hover:text-foreground",
                )}
              >
                {stage && (
                  <span
                    className={cn(
                      "hidden rounded px-1 py-0.5 font-mono text-[10px] font-semibold tracking-tight xl:inline-block",
                      active
                        ? "bg-primary-foreground/20 text-primary-foreground"
                        : "bg-secondary text-muted-foreground",
                    )}
                    aria-hidden="true"
                  >
                    {stage}
                  </span>
                )}
                <Icon className="size-4 shrink-0" aria-hidden="true" />
                <span>{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

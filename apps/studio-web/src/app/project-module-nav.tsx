import { Link } from "@tanstack/react-router";
import {
  BookOpenText,
  Clapperboard,
  FolderCog,
  GitBranch,
  LayoutGrid,
  ListChecks,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PIPELINE_MODULES, PROJECT_MODULE_LABELS, type ProjectModule } from "@/app/project-modules";
import { cn } from "@/lib/utils";

const MODULE_ICONS: Record<ProjectModule, LucideIcon> = {
  "script-studio": BookOpenText,
  "shot-board": LayoutGrid,
  "queue-monitor": ListChecks,
  "timeline-editor": Clapperboard,
  "workflow-canvas": GitBranch,
  settings: FolderCog,
};

const MODULE_TRANSLATION_KEYS: Record<
  ProjectModule,
  | "nav.modules.scriptStudio"
  | "nav.modules.shotBoard"
  | "nav.modules.queueMonitor"
  | "nav.modules.timelineEditor"
  | "nav.modules.workflowCanvas"
  | "nav.modules.settings"
> = {
  "script-studio": "nav.modules.scriptStudio",
  "shot-board": "nav.modules.shotBoard",
  "queue-monitor": "nav.modules.queueMonitor",
  "timeline-editor": "nav.modules.timelineEditor",
  "workflow-canvas": "nav.modules.workflowCanvas",
  settings: "nav.modules.settings",
};

interface ProjectModuleNavProps {
  projectId: string;
  activeModule: ProjectModule;
}

function ModuleLink({
  projectId,
  module,
  active,
  step,
  badge,
}: {
  projectId: string;
  module: ProjectModule;
  active: boolean;
  step?: number;
  badge?: string;
}) {
  const { t } = useTranslation();
  const Icon = MODULE_ICONS[module];
  const label = t(MODULE_TRANSLATION_KEYS[module], {
    defaultValue: PROJECT_MODULE_LABELS[module],
  });

  return (
    <Link
      to="/projects/$projectId/$module"
      params={{ projectId, module }}
      aria-current={active ? "page" : undefined}
      className={cn(
        "inline-flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "bg-primary text-primary-foreground shadow-xs"
          : "text-muted-foreground hover:bg-secondary hover:text-foreground",
      )}
    >
      {step !== undefined ? (
        <span
          className={cn(
            "flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
            active ? "bg-primary-foreground/20" : "bg-secondary text-foreground",
          )}
          aria-hidden="true"
        >
          {step}
        </span>
      ) : (
        <Icon className="size-4 shrink-0" aria-hidden="true" />
      )}
      <span>{label}</span>
      {badge && (
        <span
          className={cn(
            "rounded px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
            active ? "bg-primary-foreground/20" : "bg-secondary text-muted-foreground",
          )}
        >
          {badge}
        </span>
      )}
    </Link>
  );
}

/**
 * Project navigation: the four production steps in order, then the optional advanced tool and
 * the project's own settings. Workspace tools are in the app header, not here.
 */
export function ProjectModuleNav({ projectId, activeModule }: ProjectModuleNavProps) {
  const { t } = useTranslation();

  return (
    <nav aria-label={t("nav.projectModules", { defaultValue: "Project modules" })} className="mb-5">
      <div className="flex flex-col gap-1.5 rounded-xl border border-border bg-card p-1.5 shadow-2xs lg:flex-row lg:items-center lg:justify-between">
        <ol aria-label={t("nav.steps.production")} className="flex flex-wrap items-center gap-1">
          {PIPELINE_MODULES.map((module, index) => (
            <li key={module}>
              <ModuleLink
                projectId={projectId}
                module={module}
                active={module === activeModule}
                step={index + 1}
              />
            </li>
          ))}
        </ol>
        <ul
          aria-label={t("nav.steps.moreTools")}
          className="flex flex-wrap items-center gap-1 border-t border-border pt-1.5 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-1.5"
        >
          <li>
            <ModuleLink
              projectId={projectId}
              module="workflow-canvas"
              active={activeModule === "workflow-canvas"}
              badge={t("nav.steps.advanced")}
            />
          </li>
          <li>
            <ModuleLink
              projectId={projectId}
              module="settings"
              active={activeModule === "settings"}
            />
          </li>
        </ul>
      </div>
    </nav>
  );
}

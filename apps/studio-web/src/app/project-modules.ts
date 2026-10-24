/**
 * Modules inside one project, in production order. Workspace-wide tools (projects list,
 * connections, system maintenance) are top-level routes, not project modules.
 */
export const PROJECT_MODULES = [
  "script-studio",
  "shot-board",
  "queue-monitor",
  "timeline-editor",
  "workflow-canvas",
  "settings",
] as const;

export type ProjectModule = (typeof PROJECT_MODULES)[number];

export function isProjectModule(module: string): module is ProjectModule {
  return (PROJECT_MODULES as readonly string[]).includes(module);
}

/** Navigation labels; the route segment stays stable while the label says what the page does. */
export const PROJECT_MODULE_LABELS: Record<ProjectModule, string> = {
  "script-studio": "Script & Story Bible",
  "shot-board": "Shot Board",
  "queue-monitor": "Generation Queue",
  "timeline-editor": "Timeline & Export",
  "workflow-canvas": "Workflow Automation",
  settings: "Project Settings",
};

/** The default filmmaking path, shown as numbered steps. */
export const PIPELINE_MODULES = [
  "script-studio",
  "shot-board",
  "queue-monitor",
  "timeline-editor",
] as const satisfies readonly ProjectModule[];

/**
 * Project module URLs from before the workspace/project split, mapped to their new home so
 * bookmarks keep working. `connections` became workspace-wide; project backups and the old
 * project/revision page now live under the project's settings.
 */
export type LegacyModuleTarget =
  { kind: "workspace"; to: "/connections" } | { kind: "project"; module: ProjectModule };

export const LEGACY_PROJECT_MODULES: Record<string, LegacyModuleTarget> = {
  connections: { kind: "workspace", to: "/connections" },
  projects: { kind: "project", module: "settings" },
  "system-ops": { kind: "project", module: "settings" },
};

export function legacyModuleTarget(module: string): LegacyModuleTarget | undefined {
  return Object.prototype.hasOwnProperty.call(LEGACY_PROJECT_MODULES, module)
    ? LEGACY_PROJECT_MODULES[module]
    : undefined;
}

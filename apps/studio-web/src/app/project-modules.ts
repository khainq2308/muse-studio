export const PROJECT_MODULES = [
  "projects",
  "connections",
  "script-studio",
  "shot-board",
  "workflow-canvas",
  "queue-monitor",
  "timeline-editor",
  "system-ops",
] as const;

export function isProjectModule(module: string): boolean {
  return (PROJECT_MODULES as readonly string[]).includes(module);
}

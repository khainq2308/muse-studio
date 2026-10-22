export const queryKeys = {
  health: ["system", "health"] as const,
  projects: (sessionVersion: number) => ["projects", sessionVersion] as const,
  connections: (sessionVersion: number) => ["connections", sessionVersion] as const,
  queueTasksPrefix: (sessionVersion: number, projectId: string) =>
    ["queue-tasks", sessionVersion, projectId] as const,
  queueTasks: (sessionVersion: number, projectId: string, stateFilter: string, offset: number) =>
    ["queue-tasks", sessionVersion, projectId, stateFilter, offset] as const,
};

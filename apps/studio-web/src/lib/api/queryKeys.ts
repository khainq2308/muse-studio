export const queryKeys = {
  health: ["system", "health"] as const,
  projects: (sessionVersion: number) => ["projects", sessionVersion] as const,
  connections: (sessionVersion: number) => ["connections", sessionVersion] as const,
  queueTasksPrefix: (sessionVersion: number, projectId: string) =>
    ["queue-tasks", sessionVersion, projectId] as const,
  queueTasks: (sessionVersion: number, projectId: string, stateFilter: string, offset: number) =>
    ["queue-tasks", sessionVersion, projectId, stateFilter, offset] as const,
  /** Mutation key for pause/resume/cancel actions, used to track every in-flight action. */
  queueTaskActions: (sessionVersion: number, projectId: string) =>
    ["queue-task-actions", sessionVersion, projectId] as const,
  /** Mutation key for operator replies to awaiting-input tasks. */
  queueTaskReplies: (sessionVersion: number, projectId: string) =>
    ["queue-task-replies", sessionVersion, projectId] as const,
  /** Every shot of a project; a shot edit recompiles neighbours, so invalidate the whole list. */
  projectShots: (sessionVersion: number, projectId: string) =>
    ["project-shots", sessionVersion, projectId] as const,
  /** Mutation key for shot board generation requests (single enqueue and batch launch). */
  shotBoardGeneration: (sessionVersion: number, projectId: string) =>
    ["shot-board-generation", sessionVersion, projectId] as const,
};

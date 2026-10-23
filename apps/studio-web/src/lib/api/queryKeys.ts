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
  /** Every take list of a project's shots (prefix for invalidation after take changes). */
  shotTakesPrefix: (sessionVersion: number, projectId: string) =>
    ["shot-takes", sessionVersion, projectId] as const,
  shotTakes: (sessionVersion: number, projectId: string, shotId: string) =>
    ["shot-takes", sessionVersion, projectId, shotId] as const,
  /** Every A/B comparison of a project's takes (prefix for invalidation). */
  shotTakeComparePrefix: (sessionVersion: number, projectId: string) =>
    ["shot-take-compare", sessionVersion, projectId] as const,
  shotTakeCompare: (
    sessionVersion: number,
    projectId: string,
    shotId: string,
    takeAId: string,
    takeBId: string,
  ) => ["shot-take-compare", sessionVersion, projectId, shotId, takeAId, takeBId] as const,
  /** Authenticated asset bytes for an on-demand preview; assets are immutable by ID. */
  assetMedia: (sessionVersion: number, assetId: string, maxBytes: number) =>
    ["asset-media", sessionVersion, assetId, maxBytes] as const,
  /** Mutation key for take selection and continuity flags of a project. */
  shotTakeActions: (sessionVersion: number, projectId: string) =>
    ["shot-take-actions", sessionVersion, projectId] as const,
  /** Story Bible entries of a project (`GET /api/v1/projects/{project_id}/story-bible`). */
  storyBible: (sessionVersion: number, projectId: string) =>
    ["story-bible", sessionVersion, projectId] as const,
  /** Mutation key for Script Studio actions (Story Bible upsert, script import, reference upload). */
  scriptStudioActions: (sessionVersion: number, projectId: string) =>
    ["script-studio-actions", sessionVersion, projectId] as const,
};

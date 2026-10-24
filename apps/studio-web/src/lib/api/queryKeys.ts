export const queryKeys = {
  health: ["system", "health"] as const,
  projects: (sessionVersion: number) => ["projects", sessionVersion] as const,
  /** Every project's revision history (prefix; create/duplicate write a new revision). */
  projectRevisionsPrefix: (sessionVersion: number) =>
    ["project-revisions", sessionVersion] as const,
  projectRevisions: (sessionVersion: number, projectId: string) =>
    ["project-revisions", sessionVersion, projectId] as const,
  projectActions: (sessionVersion: number) => ["project-actions", sessionVersion] as const,
  connections: (sessionVersion: number) => ["connections", sessionVersion] as const,
  connectionAccounts: (sessionVersion: number, connectionId: string) =>
    ["connection-accounts", sessionVersion, connectionId] as const,
  connectionActions: (sessionVersion: number) => ["connection-actions", sessionVersion] as const,
  queueTasksPrefix: (sessionVersion: number, projectId: string) =>
    ["queue-tasks", sessionVersion, projectId] as const,
  queueTasks: (sessionVersion: number, projectId: string, stateFilter: string, offset: number) =>
    ["queue-tasks", sessionVersion, projectId, stateFilter, offset] as const,
  /** Newest tasks of a project for per-shot status; under the queue-tasks prefix. */
  recentProjectTasks: (sessionVersion: number, projectId: string) =>
    ["queue-tasks", sessionVersion, projectId, "recent"] as const,
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
  /** Saved script revisions of a project (`GET /api/v1/projects/{project_id}/script-revisions`). */
  scriptRevisions: (sessionVersion: number, projectId: string) =>
    ["script-revisions", sessionVersion, projectId] as const,
  /** Mutation key for Script Studio actions (Story Bible upsert, script import, reference upload). */
  scriptStudioActions: (sessionVersion: number, projectId: string) =>
    ["script-studio-actions", sessionVersion, projectId] as const,
  /** Latest timeline sequence of a project (`GET /api/v1/projects/{project_id}/timeline`). */
  projectTimeline: (sessionVersion: number, projectId: string) =>
    ["project-timeline", sessionVersion, projectId] as const,
  /** Export job status (`GET /api/v1/projects/{project_id}/timeline/exports/{export_id}`). */
  timelineExport: (sessionVersion: number, projectId: string, exportId: string) =>
    ["timeline-export", sessionVersion, projectId, exportId] as const,
  /** Mutation key for timeline assemble and export launch. */
  timelineActions: (sessionVersion: number, projectId: string) =>
    ["timeline-actions", sessionVersion, projectId] as const,
  /** Latest workflow of a project (`GET /api/v1/projects/{project_id}/workflows`). */
  projectWorkflow: (sessionVersion: number, projectId: string) =>
    ["project-workflow", sessionVersion, projectId] as const,
  /** Mutation key for workflow creation, graph update, layout save, run, and gate approval. */
  workflowActions: (sessionVersion: number, projectId: string) =>
    ["workflow-actions", sessionVersion, projectId] as const,
  /** Project backups (`GET /api/v1/projects/{project_id}/backups`). */
  projectBackups: (sessionVersion: number, projectId: string) =>
    ["project-backups", sessionVersion, projectId] as const,
  /** System metrics summary (`GET /api/v1/metrics/summary`). */
  metricsSummary: (sessionVersion: number) => ["metrics-summary", sessionVersion] as const,
  /** System diagnostics (`GET /api/v1/system/diagnostics`). */
  systemDiagnostics: (sessionVersion: number) => ["system-diagnostics", sessionVersion] as const,
  /** Structured operational logs (`GET /api/v1/system/logs`). */
  systemLogs: (sessionVersion: number, projectId: string, level: string) =>
    ["system-logs", sessionVersion, projectId, level] as const,
  /** Mutation key for backups, orphan media scan/cleanup, and asset management. */
  systemOpsActions: (sessionVersion: number, projectId: string) =>
    ["system-ops-actions", sessionVersion, projectId] as const,
};

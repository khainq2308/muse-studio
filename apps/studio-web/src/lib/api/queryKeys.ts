export const queryKeys = {
  health: ["system", "health"] as const,
  projects: (sessionVersion: number) => ["projects", sessionVersion] as const,
  connections: (sessionVersion: number) => ["connections", sessionVersion] as const,
};

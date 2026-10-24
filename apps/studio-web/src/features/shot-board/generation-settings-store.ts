import { create } from "zustand";
import {
  modelOptionsForTaskType,
  type Connection,
  type GenerationTaskType,
} from "@/features/shot-board/state";

export interface GenerationSettingsValue {
  connectionId: string;
  modelId: string;
  taskType: GenerationTaskType;
}

export const EMPTY_GENERATION_SETTINGS: GenerationSettingsValue = {
  connectionId: "",
  modelId: "",
  taskType: "video",
};

interface GenerationSettingsState {
  /** Per-project choice, kept in memory only so it survives switching modules, not reloads. */
  byProject: Record<string, GenerationSettingsValue>;
  setForProject: (projectId: string, value: GenerationSettingsValue) => void;
}

export const useGenerationSettingsStore = create<GenerationSettingsState>((set) => ({
  byProject: {},
  setForProject: (projectId, value) =>
    set((state) => ({ byProject: { ...state.byProject, [projectId]: value } })),
}));

/**
 * The settings to use against the loaded connections. A stored connection that is no longer
 * enabled (disabled or deleted) counts as not chosen. With nothing chosen and exactly one
 * enabled connection, choose it and the first model its last connection test discovered. Every
 * launch still shows the provider and cost in its confirmation; with several connections the
 * user must pick one. While connections are loading (`undefined`) the value is kept as is.
 */
export function withDefaultConnection(
  value: GenerationSettingsValue,
  connections: readonly Connection[] | undefined,
): GenerationSettingsValue {
  if (!connections) return value;
  const enabled = connections.filter((connection) => connection.enabled);
  if (value.connectionId && enabled.some((connection) => connection.id === value.connectionId)) {
    return value;
  }
  const unchosen = value.connectionId ? { ...value, connectionId: "" } : value;
  const only = enabled[0];
  if (enabled.length !== 1 || !only) return unchosen;
  const discovered = modelOptionsForTaskType(only, value.taskType);
  return {
    ...unchosen,
    connectionId: only.id,
    modelId: value.modelId || discovered[0]?.modelId || "",
  };
}

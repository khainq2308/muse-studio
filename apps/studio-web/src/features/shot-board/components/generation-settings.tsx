import { useId } from "react";
import { QueryState } from "@/components/shared/query-state";
import {
  isMockConnection,
  modelOptionsForTaskType,
  type Connection,
  type GenerationTaskType,
} from "@/features/shot-board/state";
import type { GenerationSettingsValue } from "@/features/shot-board/generation-settings-store";
import { cn } from "@/lib/utils";

export type { GenerationSettingsValue };

interface GenerationSettingsProps {
  connections: readonly Connection[];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onRetry: () => void;
  value: GenerationSettingsValue;
  onChange: (value: GenerationSettingsValue) => void;
  disabled?: boolean;
}

const controlClass = cn(
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "disabled:cursor-not-allowed disabled:opacity-60",
);

/** Connection, task type and model used by single-shot enqueue and batch launch. */
export function GenerationSettings({
  connections,
  isLoading,
  isError,
  errorMessage,
  onRetry,
  value,
  onChange,
  disabled = false,
}: GenerationSettingsProps) {
  const idPrefix = useId();
  const enabledConnections = connections.filter((connection) => connection.enabled);
  const connection = enabledConnections.find((item) => item.id === value.connectionId);
  const modelOptions = modelOptionsForTaskType(connection, value.taskType);

  const selectConnection = (connectionId: string) => {
    const next = enabledConnections.find((item) => item.id === connectionId);
    const discovered = modelOptionsForTaskType(next, value.taskType);
    onChange({
      ...value,
      connectionId,
      // Prefill a model the connection test discovered; otherwise keep what the operator typed.
      modelId: discovered[0]?.modelId ?? value.modelId,
    });
  };

  const selectTaskType = (taskType: GenerationTaskType) => {
    const discovered = modelOptionsForTaskType(connection, taskType);
    onChange({ ...value, taskType, modelId: discovered[0]?.modelId ?? value.modelId });
  };

  return (
    <section
      aria-labelledby={`${idPrefix}-heading`}
      className="space-y-4 rounded-xl border border-border bg-card p-4"
    >
      <div>
        <h3 id={`${idPrefix}-heading`} className="font-semibold">
          Generation settings
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Used when you queue a single shot or launch a batch. Every launch asks for confirmation.
        </p>
      </div>
      <QueryState
        isLoading={isLoading}
        isError={isError}
        isEmpty={enabledConnections.length === 0}
        loadingMessage="Loading provider connections..."
        errorMessage={errorMessage}
        emptyTitle="No enabled provider connections"
        emptyDescription="Add or enable a provider connection before generating shots."
        onRetry={onRetry}
      >
        <div className="grid gap-4 md:grid-cols-3">
          <div className="space-y-1">
            <label htmlFor={`${idPrefix}-connection`} className="text-sm font-medium">
              Connection
            </label>
            <select
              id={`${idPrefix}-connection`}
              value={value.connectionId}
              disabled={disabled}
              onChange={(event) => selectConnection(event.target.value)}
              className={controlClass}
            >
              <option value="">Select a connection</option>
              {enabledConnections.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} ({isMockConnection(item) ? "mock" : item.provider_type})
                </option>
              ))}
            </select>
            {connection && (
              <p className="text-xs text-muted-foreground" data-testid="connection-cost-label">
                {isMockConnection(connection)
                  ? "Mock provider: no provider cost."
                  : `Live provider (${connection.provider_type}): generation may consume quota.`}{" "}
                Health: {connection.health_state}.
              </p>
            )}
          </div>

          <div className="space-y-1">
            <label htmlFor={`${idPrefix}-task-type`} className="text-sm font-medium">
              Single-shot output
            </label>
            <select
              id={`${idPrefix}-task-type`}
              value={value.taskType}
              disabled={disabled}
              onChange={(event) => selectTaskType(event.target.value as GenerationTaskType)}
              className={controlClass}
            >
              <option value="video">Video</option>
              <option value="image">Image</option>
            </select>
            <p className="text-xs text-muted-foreground">Batch launch always renders video.</p>
          </div>

          <div className="space-y-1">
            <label htmlFor={`${idPrefix}-model`} className="text-sm font-medium">
              Model
            </label>
            <input
              id={`${idPrefix}-model`}
              type="text"
              list={`${idPrefix}-models`}
              value={value.modelId}
              maxLength={128}
              disabled={disabled}
              autoComplete="off"
              placeholder="Model ID"
              onChange={(event) => onChange({ ...value, modelId: event.target.value })}
              className={controlClass}
            />
            <datalist id={`${idPrefix}-models`}>
              {modelOptions.map((model) => (
                <option key={model.modelId} value={model.modelId}>
                  {model.modality} · {model.verificationStatus}
                </option>
              ))}
            </datalist>
            <p className="text-xs text-muted-foreground">
              {connection && modelOptions.length === 0
                ? "The last connection test listed no models for this output; enter a model ID."
                : "Suggestions come from the last connection test."}
            </p>
          </div>
        </div>
      </QueryState>
    </section>
  );
}

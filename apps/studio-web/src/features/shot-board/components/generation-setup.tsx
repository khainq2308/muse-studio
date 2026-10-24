import { Settings2, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import { Button } from "@/components/ui/button";
import {
  GenerationSettings,
  type GenerationSettingsValue,
} from "@/features/shot-board/components/generation-settings";
import { isMockConnection, type Connection } from "@/features/shot-board/state";

interface GenerationSetupProps {
  connections: readonly Connection[];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onRetry: () => void;
  value: GenerationSettingsValue;
  onChange: (value: GenerationSettingsValue) => void;
  /** The connection in `value`, when it exists and is enabled. */
  connection: Connection | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Incremented to bring the setup into view and focus its first control. */
  focusRequest?: number;
}

/**
 * One-line summary of what Generate and Batch launch will use, expanding to the full settings.
 * When nothing usable is chosen the summary says so instead of hiding the problem.
 */
export function GenerationSetup({
  connections,
  isLoading,
  isError,
  errorMessage,
  onRetry,
  value,
  onChange,
  connection,
  open,
  onOpenChange,
  focusRequest = 0,
}: GenerationSetupProps) {
  const panelId = useId();
  const sectionRef = useRef<HTMLElement | null>(null);
  const ready = Boolean(connection && value.modelId.trim());
  const enabledCount = connections.filter((item) => item.enabled).length;

  useEffect(() => {
    if (focusRequest === 0 || !open) return;
    const section = sectionRef.current;
    section?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    section
      ?.querySelector<HTMLElement>("[data-setup-panel] select, [data-setup-panel] input")
      ?.focus();
  }, [focusRequest, open]);

  return (
    <section
      ref={sectionRef}
      aria-label="Generation setup"
      data-testid="generation-setup"
      className="rounded-xl border border-border bg-card"
    >
      <div className="flex flex-col gap-2 px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between">
        <p
          className="flex min-w-0 items-start gap-2 text-sm"
          data-testid="generation-setup-summary"
        >
          {ready && connection ? (
            <>
              <Settings2 className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
              <span className="min-w-0">
                Generating with <span className="font-medium">{connection.name}</span>{" "}
                <span className="text-muted-foreground">
                  ({isMockConnection(connection) ? "mock, no provider cost" : "live, may use quota"}
                  )
                </span>{" "}
                · <span className="break-all font-mono text-xs">{value.modelId.trim()}</span> ·{" "}
                {value.taskType === "video" ? "Video" : "Image"}
              </span>
            </>
          ) : (
            <>
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
              <span>
                {isLoading
                  ? "Loading provider connections..."
                  : isError
                    ? "Could not load provider connections. Open the setup to retry."
                    : enabledCount === 0
                      ? "No enabled provider connection: add or enable one on the Connections page."
                      : !connection
                        ? "Generation is not set up: choose a provider connection to generate shots."
                        : "Generation is not set up: enter a model ID to generate shots."}
              </span>
            </>
          )}
        </p>
        <Button
          type="button"
          size="sm"
          variant={ready ? "outline" : "default"}
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => onOpenChange(!open)}
        >
          {open ? "Done" : ready ? "Change" : "Set up generation"}
        </Button>
      </div>
      {open && (
        <div id={panelId} data-setup-panel className="border-t border-border p-3">
          <GenerationSettings
            connections={connections}
            isLoading={isLoading}
            isError={isError}
            errorMessage={errorMessage}
            onRetry={onRetry}
            value={value}
            onChange={onChange}
          />
        </div>
      )}
    </section>
  );
}

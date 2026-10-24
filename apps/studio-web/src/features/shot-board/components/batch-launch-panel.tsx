import { AlertTriangle, LoaderCircle, OctagonAlert, Rocket } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { QueueMonitorLink } from "@/features/shot-board/components/queue-monitor-link";
import {
  batchTargetShots,
  buildBatchLaunchRequest,
  isMockConnection,
  shotGenerationLabel,
  shotLabel,
  shotLintBlockers,
  type BatchLaunchRequest,
  type BatchLaunchResult,
  type BatchScope,
  type Connection,
  type ProjectShot,
} from "@/features/shot-board/state";
import { cn } from "@/lib/utils";

interface BatchLaunchPanelProps {
  projectId: string;
  shots: readonly ProjectShot[];
  sceneIds: readonly string[];
  connection: Connection | undefined;
  modelId: string;
  /** Batch launch renders video only; an image model must not be sent as a video model. */
  taskType: "video" | "image";
  isLaunching: boolean;
  result: BatchLaunchResult | null;
  errorMessage: string | null;
  onLaunch: (request: BatchLaunchRequest) => void;
}

const controlClass = cn(
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "disabled:cursor-not-allowed disabled:opacity-60",
);

export function BatchLaunchPanel({
  projectId,
  shots,
  sceneIds,
  connection,
  modelId,
  taskType,
  isLaunching,
  result,
  errorMessage,
  onLaunch,
}: BatchLaunchPanelProps) {
  const idPrefix = useId();
  const [sceneId, setSceneId] = useState("");
  const [runAll, setRunAll] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [runAllAcknowledged, setRunAllAcknowledged] = useState(false);
  const keepButton = useRef<HTMLButtonElement | null>(null);
  const launchButton = useRef<HTMLButtonElement | null>(null);
  const wasConfirming = useRef(false);

  const scope: BatchScope = sceneId ? { kind: "scene", sceneId } : { kind: "project" };
  const targetCount = batchTargetShots(shots, scope).length;
  const trimmedModel = modelId.trim();
  const blockedReason = !connection
    ? "Select a connection in Generation settings to launch a batch."
    : taskType !== "video"
      ? "Batch launch renders video. Switch the output to Video and choose a video model."
      : !trimmedModel
        ? "Enter a model ID in Generation settings."
        : targetCount === 0
          ? "There are no shots in this scope."
          : null;

  useEffect(() => {
    if (confirming) keepButton.current?.focus();
    else if (wasConfirming.current) launchButton.current?.focus();
    wasConfirming.current = confirming;
  }, [confirming]);

  const closeConfirmation = () => {
    setConfirming(false);
    setRunAllAcknowledged(false);
  };

  const confirmLaunch = () => {
    if (!connection || blockedReason || (runAll && !runAllAcknowledged)) return;
    onLaunch(
      buildBatchLaunchRequest({
        connectionId: connection.id,
        modelId: trimmedModel,
        scope,
        onlyChanged: !runAll,
      }),
    );
    closeConfirmation();
  };

  return (
    <section
      aria-labelledby={`${idPrefix}-heading`}
      data-testid="batch-launch-panel"
      className="space-y-4 rounded-xl border border-border bg-card p-4"
    >
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 id={`${idPrefix}-heading`} className="font-semibold">
            Batch launch
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Queues video generation for every shot in scope. Unchanged shots with a matching
            selected take are skipped unless you choose to run all.
          </p>
        </div>
        <QueueMonitorLink projectId={projectId} label="Track progress in Generation Queue" />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1">
          <label htmlFor={`${idPrefix}-scope`} className="text-sm font-medium">
            Scope
          </label>
          <select
            id={`${idPrefix}-scope`}
            value={sceneId}
            disabled={isLaunching || confirming}
            onChange={(event) => setSceneId(event.target.value)}
            className={controlClass}
          >
            <option value="">All shots in project ({shots.length})</option>
            {sceneIds.map((id) => (
              <option key={id} value={id}>
                Scene {id} ({batchTargetShots(shots, { kind: "scene", sceneId: id }).length})
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-start gap-2 pt-1 md:pt-7">
          <input
            id={`${idPrefix}-run-all`}
            type="checkbox"
            checked={runAll}
            disabled={isLaunching || confirming}
            onChange={(event) => setRunAll(event.target.checked)}
            className="mt-0.5 size-4 accent-primary"
          />
          <label htmlFor={`${idPrefix}-run-all`} className="text-sm">
            <span className="font-medium">Run all regardless of changes</span>
            <span className="block text-xs text-muted-foreground">
              Also rerenders shots whose selected take already matches the current prompt.
            </span>
          </label>
        </div>
      </div>

      {blockedReason && <p className="text-sm text-muted-foreground">{blockedReason}</p>}

      {!confirming && (
        <Button
          type="button"
          ref={launchButton}
          disabled={Boolean(blockedReason) || isLaunching}
          aria-haspopup="dialog"
          onClick={() => setConfirming(true)}
        >
          {isLaunching ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <Rocket aria-hidden="true" />
          )}
          {isLaunching ? "Launching batch..." : "Launch batch"}
        </Button>
      )}

      {confirming && connection && (
        <div
          role="alertdialog"
          aria-labelledby={`${idPrefix}-confirm-title`}
          aria-describedby={`${idPrefix}-confirm-body`}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            closeConfirmation();
          }}
          className="space-y-3 rounded-lg border border-warning/40 bg-warning/5 p-4"
        >
          <p id={`${idPrefix}-confirm-title`} className="font-semibold">
            Launch batch for {targetCount} {targetCount === 1 ? "shot" : "shots"}?
          </p>
          <div id={`${idPrefix}-confirm-body`} className="space-y-1 text-sm">
            <p>
              Connection: <span className="font-medium">{connection.name}</span> ·{" "}
              {isMockConnection(connection)
                ? "Mock provider (no provider cost)"
                : `Live provider (${connection.provider_type}): may consume quota`}
            </p>
            <p>
              Model: <span className="break-all font-medium">{trimmedModel}</span> · Scope:{" "}
              {scope.kind === "scene" ? `scene ${scope.sceneId}` : "all shots in project"}
            </p>
            <p>
              {runAll
                ? `All ${targetCount} shots in scope will be rendered again, including unchanged shots.`
                : "Shots that are unchanged since their selected take will be skipped."}
            </p>
          </div>
          {runAll && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3">
              <input
                id={`${idPrefix}-run-all-ack`}
                type="checkbox"
                checked={runAllAcknowledged}
                onChange={(event) => setRunAllAcknowledged(event.target.checked)}
                className="mt-0.5 size-4 accent-primary"
              />
              <label htmlFor={`${idPrefix}-run-all-ack`} className="text-sm">
                I understand that all {targetCount} shots will be rerun and new takes may consume
                provider quota.
              </label>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              onClick={confirmLaunch}
              disabled={isLaunching || (runAll && !runAllAcknowledged)}
            >
              Confirm launch
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              ref={keepButton}
              onClick={closeConfirmation}
            >
              Keep editing
            </Button>
          </div>
        </div>
      )}

      {errorMessage && (
        <p
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {errorMessage}
        </p>
      )}

      {result && <BatchLaunchResultView result={result} shots={shots} projectId={projectId} />}
    </section>
  );
}

function blockedShotReason(shot: ProjectShot | undefined): string {
  if (!shot) return "Shot not found in the current shot list.";
  const blockers = shotLintBlockers(shot.lint_report);
  if (blockers.length > 0) {
    return blockers
      .map((issue) => `Prompt Linter blocker ${issue.code}: ${issue.message}`)
      .join("; ");
  }
  if (
    shot.generation_state === "blocked_reference" ||
    shot.generation_state === "blocked_upstream_frame"
  ) {
    return shotGenerationLabel(shot.generation_state);
  }
  return `The batch response does not include a reason. Current state: ${shotGenerationLabel(shot.generation_state)}.`;
}

interface BatchLaunchResultViewProps {
  result: BatchLaunchResult;
  shots: readonly ProjectShot[];
  projectId: string;
}

export function BatchLaunchResultView({ result, shots, projectId }: BatchLaunchResultViewProps) {
  const shotsById = new Map(shots.map((shot) => [shot.id, shot]));
  const describeShot = (shotId: string) => {
    const shot = shotsById.get(shotId);
    return shot ? shotLabel(shot) : shotId;
  };
  const warnings = result.warnings ?? [];

  return (
    <section
      aria-label="Batch launch result"
      data-testid="batch-launch-result"
      className="space-y-3 border-t border-border pt-4"
    >
      <p role="status" className="text-sm font-medium">
        Batch <span className="break-all">{result.batch_id}</span>: {result.enqueued_count} queued,{" "}
        {result.skipped_unchanged_count} skipped, {result.blocked_count} blocked of{" "}
        {result.total_shots} {result.total_shots === 1 ? "shot" : "shots"}.
      </p>

      {warnings.map((warning, index) => (
        <div
          key={`${warning.code}:${index}`}
          data-testid="batch-warning"
          data-code={warning.code}
          className="space-y-2 rounded-md border border-warning/40 bg-warning/5 p-3 text-sm"
        >
          <p className="flex items-center gap-2 font-semibold text-warning">
            <AlertTriangle className="size-4" aria-hidden="true" />
            {warning.code === "frame_chain_continuity_not_guaranteed"
              ? "Frame-chain continuity not guaranteed"
              : warning.code}
          </p>
          <p className="break-words">{warning.message}</p>
          {warning.shot_ids.length > 0 && (
            <div>
              <p className="text-xs text-muted-foreground">Affected shots</p>
              <ul className="mt-1 list-inside list-disc text-sm">
                {warning.shot_ids.map((shotId) => (
                  <li key={shotId} className="break-all">
                    {describeShot(shotId)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ))}

      {result.blocked_shot_ids.length > 0 && (
        <div
          data-testid="batch-blocked-shots"
          className="space-y-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm"
        >
          <p className="flex items-center gap-2 font-semibold text-destructive">
            <OctagonAlert className="size-4" aria-hidden="true" />
            Blocked shots
          </p>
          <ul className="space-y-1">
            {result.blocked_shot_ids.map((shotId) => (
              <li key={shotId} className="break-words">
                <span className="font-medium">{describeShot(shotId)}</span>:{" "}
                {blockedShotReason(shotsById.get(shotId))}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.skipped_shot_ids.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Skipped (unchanged or an existing task already covers them):{" "}
          {result.skipped_shot_ids.map(describeShot).join(", ")}
        </p>
      )}

      <QueueMonitorLink projectId={projectId} label="Follow progress in Generation Queue" />
    </section>
  );
}

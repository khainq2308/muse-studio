import { Columns2, LoaderCircle, RefreshCw, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import {
  useCompareTakes,
  useFlagTakeContinuity,
  useSelectTake,
  useShotTakes,
} from "@/features/shot-board/api";
import { ContinuityFlagForm } from "@/features/shot-board/components/continuity-flag-form";
import { QueueMonitorLink } from "@/features/shot-board/components/queue-monitor-link";
import { TakeCompare } from "@/features/shot-board/components/take-compare";
import { TakeList } from "@/features/shot-board/components/take-list";
import { shotLabel, type Connection, type ProjectShot } from "@/features/shot-board/state";
import {
  buildContinuityFlagRequest,
  downstreamShotLabels,
  sortTakes,
  takeActionErrorMessage,
  takeLabel,
  type ContinuityFlagResult,
  type ShotTake,
  type ShotTakeSelection,
} from "@/features/shot-board/takes";
import { authErrorMessage, isAuthError } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

interface ShotTakeReviewProps {
  projectId: string;
  shot: ProjectShot;
  /** All shots of the project, to name the downstream shots a selection re-pointed. */
  shots: readonly ProjectShot[];
  /** Generation settings connection/model, used by "Regenerate now". */
  connection: Connection | undefined;
  modelId: string;
  regenerateDisabledReason: string | null;
  onClose: () => void;
}

/**
 * Take review for one shot: list takes with frames, preview on demand, A/B compare, select a
 * take and flag continuity (optionally regenerating). Requests start only while it is open.
 */
export function ShotTakeReview({
  projectId,
  shot,
  shots,
  connection,
  modelId,
  regenerateDisabledReason,
  onClose,
}: ShotTakeReviewProps) {
  const headingId = useId();
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const takesQuery = useShotTakes(projectId, shot.id);
  const selectMutation = useSelectTake(projectId);
  const flagMutation = useFlagTakeContinuity(projectId);

  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [previewTakeId, setPreviewTakeId] = useState<string | null>(null);
  const [flagTakeId, setFlagTakeId] = useState<string | null>(null);

  const comparePair =
    compareOpen && compareIds.length === 2 ? ([compareIds[0]!, compareIds[1]!] as const) : null;
  const compareQuery = useCompareTakes(projectId, shot.id, comparePair);

  const takes = useMemo(() => sortTakes(takesQuery.data ?? []), [takesQuery.data]);

  const { reset: resetSelect } = selectMutation;
  const { reset: resetFlag } = flagMutation;
  // A new admin session must not show results or errors from the previous one.
  const startedSession = useRef(sessionVersion);
  useEffect(() => {
    if (startedSession.current === sessionVersion) return;
    startedSession.current = sessionVersion;
    resetSelect();
    resetFlag();
    setFlagTakeId(null);
  }, [resetFlag, resetSelect, sessionVersion]);

  // Drop compare choices for takes that disappeared after a refresh.
  useEffect(() => {
    if (!takesQuery.data) return;
    const ids = new Set(takesQuery.data.map((take) => take.id));
    setCompareIds((current) => {
      const kept = current.filter((id) => ids.has(id));
      return kept.length === current.length ? current : kept;
    });
  }, [takesQuery.data]);

  const actionPending = selectMutation.isPending || flagMutation.isPending;
  const selectTakeId = selectMutation.variables?.takeId ?? null;
  const flagVariablesTakeId = flagMutation.variables?.takeId ?? null;
  const busyTakeId = selectMutation.isPending
    ? selectTakeId
    : flagMutation.isPending
      ? flagVariablesTakeId
      : null;
  const takeById = new Map(takes.map((take) => [take.id, take]));

  const select = (takeId: string) => {
    resetFlag();
    selectMutation.mutate({ shotId: shot.id, takeId });
  };
  const openFlag = (takeId: string) => {
    resetFlag();
    resetSelect();
    setFlagTakeId(takeId);
  };
  const clearFlag = (take: ShotTake) => {
    resetSelect();
    setFlagTakeId(null);
    flagMutation.mutate({
      shotId: shot.id,
      takeId: take.id,
      request: buildContinuityFlagRequest({
        take,
        flagged: false,
        directorNotes: take.director_notes,
      }),
    });
  };
  const toggleCompare = (takeId: string) => {
    setCompareIds((current) =>
      current.includes(takeId)
        ? current.filter((id) => id !== takeId)
        : current.length >= 2
          ? current
          : [...current, takeId],
    );
  };

  useEffect(() => {
    if (compareIds.length < 2) setCompareOpen(false);
  }, [compareIds.length]);

  // A saved flag closes its form; the result is reported above the take list.
  const flagSucceeded = flagMutation.isSuccess;
  useEffect(() => {
    if (flagSucceeded) setFlagTakeId(null);
  }, [flagSucceeded]);

  const refresh = () => void takesQuery.refetch({ cancelRefetch: true });

  return (
    <section
      aria-labelledby={headingId}
      data-testid={`shot-take-review-${shot.id}`}
      className="mt-4 space-y-4 rounded-lg border border-primary/40 bg-background/60 p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 id={headingId} className="font-semibold">
            Takes for {shotLabel(shot)}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            Selecting a take sets this shot&apos;s last frame; frame-chained shots that follow it
            are re-pointed to the new frame.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={refresh}
            aria-busy={takesQuery.isFetching}
            aria-label={`Refresh takes for ${shotLabel(shot)}`}
          >
            <RefreshCw className={cn(takesQuery.isFetching && "animate-spin")} aria-hidden="true" />
            Refresh
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={onClose}
            aria-label={`Close takes for ${shotLabel(shot)}`}
          >
            <X aria-hidden="true" />
            Close
          </Button>
        </div>
      </div>

      {selectMutation.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          <LoaderCircle className="mr-2 inline size-4 animate-spin" aria-hidden="true" />
          Selecting {takeLabelFor(takeById, selectTakeId)}...
        </p>
      )}
      {selectMutation.data && (
        <SelectionResult
          selection={selectMutation.data}
          takeName={takeLabelFor(takeById, selectMutation.data.selected_take_id)}
          shots={shots}
          onDismiss={resetSelect}
        />
      )}
      {selectMutation.error && (
        <p
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {takeActionErrorMessage(selectMutation.error, "select")}
        </p>
      )}
      {flagMutation.data && (
        <FlagResult
          projectId={projectId}
          result={flagMutation.data}
          takeName={takeLabelFor(takeById, flagMutation.data.take_id)}
          onDismiss={resetFlag}
        />
      )}
      {flagMutation.error && flagTakeId === null && (
        <p
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {takeActionErrorMessage(flagMutation.error, "flag")}
        </p>
      )}

      <QueryState
        isLoading={takesQuery.isPending}
        isError={takesQuery.isError}
        hasData={Boolean(takesQuery.data)}
        isEmpty={takes.length === 0}
        loadingMessage="Loading takes..."
        errorMessage={
          takesQuery.isError && !isAuthError(takesQuery.error)
            ? takeActionErrorMessage(takesQuery.error, "load")
            : authErrorMessage(takesQuery.error, activeAdminToken, "takes")
        }
        emptyTitle="No takes yet"
        emptyDescription="Generate this shot; each finished generation adds a take here."
        onRetry={refresh}
      >
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={compareIds.length !== 2}
              aria-expanded={compareOpen}
              onClick={() => setCompareOpen(true)}
            >
              <Columns2 aria-hidden="true" />
              Compare A/B
            </Button>
            <span className="text-xs text-muted-foreground" aria-live="polite">
              {compareIds.length === 2
                ? `A: ${takeLabelFor(takeById, compareIds[0]!)} · B: ${takeLabelFor(takeById, compareIds[1]!)}`
                : `Choose two takes to compare (${compareIds.length}/2).`}
            </span>
          </div>

          {comparePair && (
            <div>
              {compareQuery.isPending && (
                <p role="status" className="text-sm text-muted-foreground">
                  Loading comparison...
                </p>
              )}
              {compareQuery.isError && (
                <div
                  role="alert"
                  className="flex flex-wrap items-center gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
                >
                  {takeActionErrorMessage(compareQuery.error, "compare")}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void compareQuery.refetch()}
                  >
                    Retry
                  </Button>
                </div>
              )}
              {compareQuery.data && (
                <TakeCompare
                  comparison={compareQuery.data}
                  selectingTakeId={selectMutation.isPending ? selectTakeId : null}
                  canSelect={!actionPending}
                  onSelect={select}
                  onSwap={() => setCompareIds((current) => [...current].reverse())}
                  onClose={() => setCompareOpen(false)}
                />
              )}
            </div>
          )}

          <TakeList
            takes={takes}
            compareIds={compareIds}
            previewTakeId={previewTakeId}
            busyTakeId={busyTakeId}
            actionsEnabled={!actionPending}
            onToggleCompare={toggleCompare}
            onTogglePreview={(takeId) =>
              setPreviewTakeId((current) => (current === takeId ? null : takeId))
            }
            onSelect={select}
            onFlag={openFlag}
            onClearFlag={clearFlag}
            renderTakeDetail={(take) =>
              flagTakeId === take.id ? (
                <ContinuityFlagForm
                  take={take}
                  shotLabel={shotLabel(shot)}
                  connection={connection}
                  modelId={modelId}
                  regenerateDisabledReason={regenerateDisabledReason}
                  isSubmitting={flagMutation.isPending}
                  errorMessage={
                    flagMutation.error ? takeActionErrorMessage(flagMutation.error, "flag") : null
                  }
                  onSubmit={(request) =>
                    flagMutation.mutate({ shotId: shot.id, takeId: take.id, request })
                  }
                  onCancel={() => {
                    resetFlag();
                    setFlagTakeId(null);
                  }}
                />
              ) : null
            }
          />
        </div>
      </QueryState>
    </section>
  );
}

function takeLabelFor(takes: Map<string, ShotTake>, takeId: string | null): string {
  if (!takeId) return "the take";
  const take = takes.get(takeId);
  return take ? takeLabel(take) : `take ${takeId}`;
}

function SelectionResult({
  selection,
  takeName,
  shots,
  onDismiss,
}: {
  selection: ShotTakeSelection;
  takeName: string;
  shots: readonly ProjectShot[];
  onDismiss: () => void;
}) {
  const downstream = downstreamShotLabels(selection.propagated_downstream_shots, shots);
  return (
    <div
      role="status"
      data-testid="take-selection-result"
      className="space-y-2 rounded-md border border-success/30 bg-success/5 p-3 text-sm"
    >
      <p className="font-medium text-success">{takeName} selected.</p>
      <p>
        Shot last frame:{" "}
        {selection.last_frame_asset_id ? (
          <span className="break-all font-mono text-xs">{selection.last_frame_asset_id}</span>
        ) : (
          "none (this take has no last frame, so frame-chained shots cannot continue from it)"
        )}
      </p>
      {downstream.length > 0 ? (
        <div>
          <p>Re-pointed frame-chained downstream shots:</p>
          <ul className="ml-5 list-disc" data-testid="take-selection-downstream">
            {downstream.map((label) => (
              <li key={label}>{label}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-muted-foreground">No downstream shots were re-pointed.</p>
      )}
      <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  );
}

function FlagResult({
  projectId,
  result,
  takeName,
  onDismiss,
}: {
  projectId: string;
  result: ContinuityFlagResult;
  takeName: string;
  onDismiss: () => void;
}) {
  return (
    <div
      role="status"
      data-testid="take-flag-result"
      className="space-y-2 rounded-md border border-border bg-muted/30 p-3 text-sm"
    >
      <p className="font-medium">
        {result.continuity_flagged
          ? `Continuity flag saved on ${takeName}.`
          : `Continuity flag cleared on ${takeName}.`}
      </p>
      {result.regenerated_task_id ? (
        <p>
          Regeneration queued as task{" "}
          <span className="break-all font-mono text-xs" data-testid="regenerated-task-id">
            {result.regenerated_task_id}
          </span>
          .
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        {result.regenerated_task_id && (
          <QueueMonitorLink projectId={projectId} label="Follow regeneration in Queue Monitor" />
        )}
        <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  );
}

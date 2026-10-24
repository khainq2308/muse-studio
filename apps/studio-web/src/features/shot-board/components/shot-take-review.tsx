import { Columns2, LoaderCircle, RefreshCw } from "lucide-react";
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
  flagErrorFeedback,
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
}

/**
 * Take review for one shot: list takes with frames, preview on demand, A/B compare, select a
 * take and flag continuity (optionally regenerating). Requests start only while it is mounted.
 */
export function ShotTakeReview({
  projectId,
  shot,
  shots,
  connection,
  modelId,
  regenerateDisabledReason,
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
  // Swapping A/B is a client-side view change: it must not refetch or remount the compare.
  const [swapped, setSwapped] = useState(false);
  const panelRef = useRef<HTMLElement | null>(null);
  const compareButtonRef = useRef<HTMLButtonElement | null>(null);
  const focusCompareButtonNext = useRef(false);
  const focusFlagButtonFor = useRef<string | null>(null);

  const comparePair =
    compareOpen && compareIds.length === 2 ? ([compareIds[0]!, compareIds[1]!] as const) : null;
  const compareQuery = useCompareTakes(projectId, shot.id, comparePair);

  const takes = useMemo(() => sortTakes(takesQuery.data ?? []), [takesQuery.data]);
  const displayIds = swapped ? [...compareIds].reverse() : compareIds;
  const comparison = useMemo(() => {
    const data = compareQuery.data;
    if (!data || !swapped) return data;
    return { ...data, take_a: data.take_b, take_b: data.take_a };
  }, [compareQuery.data, swapped]);

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
  const flagFeedback = flagMutation.error
    ? flagErrorFeedback(flagMutation.error, Boolean(flagMutation.variables?.request.regenerate_now))
    : null;

  const closeFlagForm = (takeId: string | null) => {
    focusFlagButtonFor.current = takeId;
    setFlagTakeId(null);
  };
  // Closing the flag form returns focus to the take's "Flag continuity" button.
  useEffect(() => {
    if (flagTakeId !== null || !focusFlagButtonFor.current) return;
    const takeId = focusFlagButtonFor.current;
    focusFlagButtonFor.current = null;
    const buttons = panelRef.current?.querySelectorAll<HTMLButtonElement>("[data-flag-take]");
    [...(buttons ?? [])].find((button) => button.dataset.flagTake === takeId)?.focus();
  }, [flagTakeId]);

  const closeCompare = () => {
    focusCompareButtonNext.current = true;
    setCompareOpen(false);
  };
  // Closing the compare (Close or Esc) returns focus to the "Compare A/B" button.
  useEffect(() => {
    if (compareOpen || !focusCompareButtonNext.current) return;
    focusCompareButtonNext.current = false;
    compareButtonRef.current?.focus();
  }, [compareOpen]);

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
    setSwapped(false);
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

  // A saved flag closes its form (focus returns to its button); the result shows above.
  const flagSucceeded = flagMutation.isSuccess;
  const succeededFlagTakeId = flagSucceeded ? (flagMutation.variables?.takeId ?? null) : null;
  useEffect(() => {
    if (!flagSucceeded) return;
    setFlagTakeId((current) => {
      if (current !== null) focusFlagButtonFor.current = succeededFlagTakeId ?? current;
      return null;
    });
  }, [flagSucceeded, succeededFlagTakeId]);

  const refresh = () => void takesQuery.refetch({ cancelRefetch: true });

  return (
    <section
      ref={panelRef}
      aria-labelledby={headingId}
      data-testid={`shot-take-review-${shot.id}`}
      className="space-y-4"
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
      {flagFeedback && flagTakeId === null && (
        <p
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {flagFeedback.message}
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
              ref={compareButtonRef}
              disabled={compareIds.length !== 2}
              aria-expanded={compareOpen}
              onClick={() => setCompareOpen(true)}
            >
              <Columns2 aria-hidden="true" />
              Compare A/B
            </Button>
            <span className="text-xs text-muted-foreground" aria-live="polite">
              {displayIds.length === 2
                ? `A: ${takeLabelFor(takeById, displayIds[0]!)} · B: ${takeLabelFor(takeById, displayIds[1]!)}`
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
              {comparison && (
                <TakeCompare
                  comparison={comparison}
                  selectingTakeId={selectMutation.isPending ? selectTakeId : null}
                  canSelect={!actionPending}
                  onSelect={select}
                  onSwap={() => setSwapped((current) => !current)}
                  onClose={closeCompare}
                />
              )}
            </div>
          )}

          <TakeList
            takes={takes}
            compareIds={displayIds}
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
                  projectId={projectId}
                  take={take}
                  shotLabel={shotLabel(shot)}
                  connection={connection}
                  modelId={modelId}
                  regenerateDisabledReason={regenerateDisabledReason}
                  isSubmitting={flagMutation.isPending}
                  error={flagFeedback}
                  onRefreshTakes={refresh}
                  onSubmit={(request) =>
                    flagMutation.mutate({ shotId: shot.id, takeId: take.id, request })
                  }
                  onCancel={() => {
                    resetFlag();
                    closeFlagForm(take.id);
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
          <QueueMonitorLink projectId={projectId} label="Follow regeneration in Generation Queue" />
        )}
        <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  );
}

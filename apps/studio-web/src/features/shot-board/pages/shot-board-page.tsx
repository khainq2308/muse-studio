import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, RefreshCw, Rocket } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import { useConnections } from "@/entities/connection/api";
import { useRecentProjectTasks } from "@/entities/task/api";
import { isQueueTaskActive } from "@/entities/task/state";
import {
  useEnqueueShotTask,
  useLaunchBatch,
  useProjectShots,
  useShotTakes,
  useUpdateShot,
} from "@/features/shot-board/api";
import { BatchLaunchPanel } from "@/features/shot-board/components/batch-launch-panel";
import { GenerationActivityBar } from "@/features/shot-board/components/generation-activity";
import type { GenerationSettingsValue } from "@/features/shot-board/components/generation-settings";
import { GenerationSetup } from "@/features/shot-board/components/generation-setup";
import { ShotLatestTask } from "@/features/shot-board/components/shot-latest-task";
import {
  EMPTY_GENERATION_SETTINGS,
  useGenerationSettingsStore,
  withDefaultConnection,
} from "@/features/shot-board/generation-settings-store";
import { latestTaskByShot, summarizeGenerationActivity } from "@/features/shot-board/queue-status";
import { QueueMonitorLink } from "@/features/shot-board/components/queue-monitor-link";
import { ShotEditPanel } from "@/features/shot-board/components/shot-edit-panel";
import {
  ShotEnqueueResult,
  ShotGenerateConfirm,
} from "@/features/shot-board/components/shot-generate-confirm";
import { ShotRail } from "@/features/shot-board/components/shot-rail";
import { ShotTakeReview } from "@/features/shot-board/components/shot-take-review";
import { ShotWorkspace } from "@/features/shot-board/components/shot-workspace";
import {
  isShotBoardTab,
  type ShotBoardSearch,
  type ShotBoardTab,
} from "@/features/shot-board/search";
import {
  buildEnqueueRequest,
  generationErrorMessage,
  groupShotsByScene,
  mapShotUpdateError,
  type ProjectShot,
  type ShotEditField,
  type ShotUpdate,
} from "@/features/shot-board/state";
import {
  defaultShotTab,
  orderedShots,
  resolveSelectedShot,
  type ShotFilter,
} from "@/features/shot-board/workspace";
import { authErrorMessage } from "@/lib/api/errors";
import { queryKeys } from "@/lib/api/queryKeys";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/shared/page-header";

export interface ShotBoardNavigateOptions {
  /** Replace the current history entry instead of pushing a new one. */
  replace?: boolean;
}

interface ShotBoardPageProps {
  projectId: string;
  /** Navigation state from the URL (`?shot=&tab=`), validated by the route. */
  search: ShotBoardSearch;
  /** Writes navigation state back to the URL. */
  onSearchChange: (search: ShotBoardSearch, options?: ShotBoardNavigateOptions) => void;
}

const NO_FIELD_ERRORS: Partial<Record<ShotEditField, string>> = {};

/** Shot list refresh while tasks run; task progress itself polls with the queue's backoff. */
const ACTIVE_SHOTS_POLL_MS = 5_000;

export function ShotBoardPage({ projectId, search, onSearchChange }: ShotBoardPageProps) {
  const { t } = useTranslation("shot-board");
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const tasksQuery = useRecentProjectTasks(projectId);
  const latestTasks = useMemo(
    () => latestTaskByShot(tasksQuery.data?.items ?? []),
    [tasksQuery.data],
  );
  const activity = tasksQuery.data
    ? summarizeGenerationActivity(tasksQuery.data, latestTasks)
    : null;
  // Shot states (queued → running → succeeded/failed) change while tasks are active. Shots only
  // poll while the task query itself polls, so they stop with its bounded window or its errors.
  const shotsQuery = useProjectShots(projectId, {
    refetchInterval:
      tasksQuery.isPolling && activity && activity.active > 0 ? ACTIVE_SHOTS_POLL_MS : false,
  });
  const connectionsQuery = useConnections();
  const updateMutation = useUpdateShot(projectId);
  const enqueueMutation = useEnqueueShotTask(projectId);
  const batchMutation = useLaunchBatch(projectId);
  const batchPanelId = useId();

  const storedSettings = useGenerationSettingsStore(
    (state) => state.byProject[projectId] ?? EMPTY_GENERATION_SETTINGS,
  );
  const setStoredSettings = useGenerationSettingsStore((state) => state.setForProject);
  const settings = withDefaultConnection(storedSettings, connectionsQuery.data);
  const setSettings = (value: GenerationSettingsValue) => setStoredSettings(projectId, value);
  const [setupOpen, setSetupOpen] = useState(false);
  /** Bumped to move focus to the generation setup (from the shortcut next to Generate). */
  const [setupFocusRequest, setSetupFocusRequest] = useState(0);
  const [editingShotId, setEditingShotId] = useState<string | null>(null);
  /** The shot whose generate confirmation is open; `forceRetry` for a new attempt. */
  const [generateRequest, setGenerateRequest] = useState<{
    shotId: string;
    forceRetry: boolean;
  } | null>(null);
  const generateShotId = generateRequest?.shotId ?? null;
  const [filter, setFilter] = useState<ShotFilter>("all");
  const [batchOpen, setBatchOpen] = useState(false);

  const shots = useMemo(() => shotsQuery.data ?? [], [shotsQuery.data]);
  const sceneIds = useMemo(() => groupShotsByScene(shots).map((group) => group.sceneId), [shots]);
  const navigatorOrder = useMemo(() => orderedShots(shots), [shots]);
  const selectedShot = useMemo(() => resolveSelectedShot(shots, search.shot), [shots, search.shot]);
  const missingShotId =
    shotsQuery.data && search.shot && selectedShot?.id !== search.shot ? search.shot : null;
  const selectedIndex = selectedShot ? navigatorOrder.indexOf(selectedShot) : -1;

  // Loaded for the selected shot only: it decides the default tab and shares its cache entry
  // with the take review.
  const takesQuery = useShotTakes(projectId, selectedShot?.id ?? "", {
    enabled: Boolean(selectedShot),
  });
  const tab: ShotBoardTab = isShotBoardTab(search.tab)
    ? search.tab
    : selectedShot
      ? defaultShotTab(selectedShot, {
          status: takesQuery.status,
          count: takesQuery.data?.length ?? 0,
        })
      : "details";

  const connection = (connectionsQuery.data ?? []).find(
    (item) => item.enabled && item.id === settings.connectionId,
  );

  // When a task that was active is no longer active, it ended: refresh shots and takes once so
  // new states and takes appear without waiting for the next poll. Compared by task ID, so a task
  // finishing while another starts is still noticed; reset per project and session.
  const activeTaskIds = useMemo(
    () =>
      tasksQuery.data
        ? new Set(
            tasksQuery.data.items
              .filter((task) => isQueueTaskActive(task.state))
              .map((task) => task.id),
          )
        : null,
    [tasksQuery.data],
  );
  const previousActiveIds = useRef<{ scope: string; ids: Set<string> } | null>(null);
  useEffect(() => {
    const scope = `${sessionVersion}:${projectId}`;
    const before = previousActiveIds.current;
    if (!activeTaskIds) return;
    previousActiveIds.current = { scope, ids: activeTaskIds };
    if (!before || before.scope !== scope) return;
    if (![...before.ids].some((id) => !activeTaskIds.has(id))) return;
    void queryClient.invalidateQueries({
      queryKey: queryKeys.projectShots(sessionVersion, projectId),
    });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.shotTakesPrefix(sessionVersion, projectId),
    });
  }, [activeTaskIds, projectId, queryClient, sessionVersion]);

  const { reset: resetUpdate } = updateMutation;
  const { reset: resetEnqueue } = enqueueMutation;
  const { reset: resetBatch } = batchMutation;
  // A new admin session must not show results or errors from the previous one.
  const startedSession = useRef(sessionVersion);
  useEffect(() => {
    if (startedSession.current === sessionVersion) return;
    startedSession.current = sessionVersion;
    resetUpdate();
    resetEnqueue();
    resetBatch();
    setGenerateRequest(null);
  }, [resetBatch, resetEnqueue, resetUpdate, sessionVersion]);

  const generateDisabledReason = !connection
    ? "Choose a provider connection to generate."
    : !settings.modelId.trim()
      ? "Enter a model ID to generate."
      : null;

  const updateShotId = updateMutation.variables?.shotId ?? null;
  const updateErrors = useMemo(
    () => (updateMutation.error ? mapShotUpdateError(updateMutation.error) : null),
    [updateMutation.error],
  );

  const selectShot = (shotId: string) => {
    if (shotId !== selectedShot?.id) setGenerateRequest(null);
    // A new shot opens on its default tab.
    onSearchChange({ shot: shotId });
  };
  const changeTab = (next: ShotBoardTab) => {
    if (!selectedShot) return;
    onSearchChange({ shot: selectedShot.id, tab: next }, { replace: true });
  };

  const openEditor = (shotId: string) => {
    resetUpdate();
    setGenerateRequest(null);
    setEditingShotId(shotId);
  };
  const closeEditor = () => {
    resetUpdate();
    setEditingShotId(null);
  };
  const saveShot = (shotId: string, update: ShotUpdate) => {
    updateMutation.mutate({ shotId, update });
  };

  const openGenerate = (shotId: string, forceRetry = false) => {
    resetEnqueue();
    setEditingShotId((current) => (current === shotId ? null : current));
    setGenerateRequest({ shotId, forceRetry });
  };
  const { resetPollingWindow } = tasksQuery;
  const confirmGenerate = useCallback(
    (shot: ProjectShot, forceRetry: boolean) => {
      if (!connection || generateDisabledReason) return;
      // New work restarts the bounded polling window.
      resetPollingWindow();
      enqueueMutation.mutate(
        buildEnqueueRequest({
          shotId: shot.id,
          connectionId: connection.id,
          modelId: settings.modelId,
          taskType: settings.taskType,
          forceRetry,
        }),
      );
      setGenerateRequest(null);
    },
    [connection, enqueueMutation, generateDisabledReason, resetPollingWindow, settings],
  );
  const enqueueShotId = enqueueMutation.variables?.request.shot_id ?? null;

  const refresh = () => {
    void shotsQuery.refetch({ cancelRefetch: true });
    void tasksQuery.refetch({ cancelRefetch: true });
    resetPollingWindow();
  };

  const renderShotActions = (shot: ProjectShot) => (
    <>
      {latestTasks.get(shot.id) && (
        <ShotLatestTask
          projectId={projectId}
          task={latestTasks.get(shot.id)!}
          retryDisabledReason={
            generateDisabledReason ??
            (enqueueMutation.isPending ? "A request is in progress." : null)
          }
          onRetry={() => openGenerate(shot.id, true)}
        />
      )}
      {editingShotId === shot.id && (
        <ShotEditPanel
          key={shot.id}
          shot={shot}
          shots={shots}
          isSaving={updateMutation.isPending && updateShotId === shot.id}
          fieldErrors={
            updateShotId === shot.id && updateErrors ? updateErrors.fieldErrors : NO_FIELD_ERRORS
          }
          formError={updateShotId === shot.id ? (updateErrors?.formError ?? null) : null}
          review={updateShotId === shot.id ? (updateMutation.data ?? null) : null}
          onSave={(update) => saveShot(shot.id, update)}
          onClose={closeEditor}
        />
      )}
      {generateShotId === shot.id && connection && (
        <ShotGenerateConfirm
          shot={shot}
          connection={connection}
          modelId={settings.modelId}
          taskType={settings.taskType}
          isSubmitting={enqueueMutation.isPending}
          forceRetry={generateRequest?.forceRetry ?? false}
          onConfirm={() => confirmGenerate(shot, generateRequest?.forceRetry ?? false)}
          onCancel={() => setGenerateRequest(null)}
        />
      )}
      {enqueueShotId === shot.id && enqueueMutation.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Queueing generation...
        </p>
      )}
      {enqueueShotId === shot.id && enqueueMutation.data && (
        <ShotEnqueueResult
          projectId={projectId}
          task={enqueueMutation.data}
          onDismiss={resetEnqueue}
          onRetry={generateDisabledReason ? undefined : () => openGenerate(shot.id, true)}
        />
      )}
      {enqueueShotId === shot.id && enqueueMutation.error && (
        <p
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {generationErrorMessage(enqueueMutation.error, "enqueue")}
        </p>
      )}
    </>
  );

  return (
    <section
      data-testid="shot-board-page"
      data-project-id={projectId}
      aria-labelledby="shot-board-heading"
      className="space-y-4"
    >
      <PageHeader
        title={t("title")}
        headingId="shot-board-heading"
        description={
          shotsQuery.data
            ? t("workspace.headerDescriptionWithShots", {
                sceneCount: sceneIds.length,
                shotCount: shots.length,
              })
            : t("workspace.headerDescriptionEmpty")
        }
        actions={
          <>
            <QueueMonitorLink projectId={projectId} />
            {shots.length > 0 && (
              <Button
                type="button"
                variant="outline"
                aria-expanded={batchOpen}
                aria-controls={batchPanelId}
                onClick={() => setBatchOpen((current) => !current)}
              >
                <Rocket className="size-4" aria-hidden="true" />
                {t("batchLaunch")}
                <ChevronDown
                  className={cn("size-4 transition-transform", batchOpen && "rotate-180")}
                  aria-hidden="true"
                />
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              onClick={refresh}
              aria-busy={shotsQuery.isFetching}
            >
              <RefreshCw
                className={cn("size-4", shotsQuery.isFetching && "animate-spin")}
                aria-hidden="true"
              />
              {t("refresh")}
            </Button>
          </>
        }
      />

      <GenerationSetup
        connections={connectionsQuery.data ?? []}
        isLoading={connectionsQuery.isPending}
        isError={connectionsQuery.isError}
        errorMessage={authErrorMessage(
          connectionsQuery.error,
          activeAdminToken,
          "provider connections",
        )}
        onRetry={() => void connectionsQuery.refetch()}
        value={settings}
        onChange={setSettings}
        connection={connection}
        open={setupOpen}
        onOpenChange={setSetupOpen}
        focusRequest={setupFocusRequest}
      />

      <GenerationActivityBar
        projectId={projectId}
        activity={activity}
        autoRefreshStopped={Boolean(activity && activity.active > 0 && !tasksQuery.isPolling)}
        loadError={
          tasksQuery.isError
            ? authErrorMessage(tasksQuery.error, activeAdminToken, "generation status")
            : null
        }
        onRefresh={refresh}
      />

      {shots.length > 0 && batchOpen && (
        <div id={batchPanelId}>
          <BatchLaunchPanel
            projectId={projectId}
            shots={shots}
            sceneIds={sceneIds}
            connection={connection}
            modelId={settings.modelId}
            taskType={settings.taskType}
            isLaunching={batchMutation.isPending}
            result={batchMutation.data ?? null}
            errorMessage={
              batchMutation.error ? generationErrorMessage(batchMutation.error, "batch") : null
            }
            onLaunch={(request) => {
              resetPollingWindow();
              batchMutation.mutate(request);
            }}
          />
        </div>
      )}

      <section aria-label="Shots">
        <QueryState
          isLoading={shotsQuery.isPending}
          isError={shotsQuery.isError}
          hasData={Boolean(shotsQuery.data)}
          isEmpty={shots.length === 0}
          loadingMessage={t("loading")}
          errorMessage={authErrorMessage(shotsQuery.error, activeAdminToken, "shots")}
          emptyTitle={t("workspace.emptyListTitle")}
          emptyDescription={t("workspace.emptyListDescription")}
          onRetry={refresh}
        >
          <div className="grid items-start gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
            <ShotRail
              shots={shots}
              selectedShotId={selectedShot?.id}
              filter={filter}
              onFilterChange={setFilter}
              onSelect={selectShot}
            />
            <div className="min-w-0 space-y-3">
              {missingShotId && (
                <p
                  role="note"
                  className="rounded-lg border border-warning/30 bg-warning/5 p-2.5 text-xs text-warning"
                >
                  {t("workspace.missingShotNote", { shotId: missingShotId })}
                </p>
              )}
              {selectedShot && (
                <ShotWorkspace
                  key={selectedShot.id}
                  shot={selectedShot}
                  shots={shots}
                  previousInOrder={
                    selectedIndex > 0 ? navigatorOrder[selectedIndex - 1] : undefined
                  }
                  nextInOrder={selectedIndex >= 0 ? navigatorOrder[selectedIndex + 1] : undefined}
                  onSelectShot={selectShot}
                  tab={tab}
                  onTabChange={changeTab}
                  takeCount={takesQuery.data ? takesQuery.data.length : null}
                  takesFailed={takesQuery.isError}
                  canGenerate={!generateDisabledReason && !enqueueMutation.isPending}
                  generateDisabledReason={generateDisabledReason}
                  onSetUpGeneration={() => {
                    setSetupOpen(true);
                    setSetupFocusRequest((current) => current + 1);
                  }}
                  onGenerate={() => openGenerate(selectedShot.id)}
                  isEditing={editingShotId === selectedShot.id}
                  onEdit={() => openEditor(selectedShot.id)}
                  actions={renderShotActions(selectedShot)}
                  takesPanel={
                    <ShotTakeReview
                      key={`${selectedShot.id}:${sessionVersion}`}
                      projectId={projectId}
                      shot={selectedShot}
                      shots={shots}
                      connection={connection}
                      modelId={settings.modelId}
                      regenerateDisabledReason={generateDisabledReason}
                    />
                  }
                />
              )}
            </div>
          </div>
        </QueryState>
      </section>
    </section>
  );
}

export default ShotBoardPage;

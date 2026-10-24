import { Link } from "@tanstack/react-router";
import {
  BookOpenText,
  ChevronDown,
  Clapperboard,
  FolderCog,
  LayoutGrid,
  RefreshCw,
  Rocket,
} from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import { useConnections } from "@/entities/connection/api";
import {
  useEnqueueShotTask,
  useLaunchBatch,
  useProjectShots,
  useShotTakes,
  useUpdateShot,
} from "@/features/shot-board/api";
import { BatchLaunchPanel } from "@/features/shot-board/components/batch-launch-panel";
import {
  GenerationSettings,
  type GenerationSettingsValue,
} from "@/features/shot-board/components/generation-settings";
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
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

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

export function ShotBoardPage({ projectId, search, onSearchChange }: ShotBoardPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const shotsQuery = useProjectShots(projectId);
  const connectionsQuery = useConnections();
  const updateMutation = useUpdateShot(projectId);
  const enqueueMutation = useEnqueueShotTask(projectId);
  const batchMutation = useLaunchBatch(projectId);
  const batchPanelId = useId();

  const [settings, setSettings] = useState<GenerationSettingsValue>({
    connectionId: "",
    modelId: "",
    taskType: "video",
  });
  const [editingShotId, setEditingShotId] = useState<string | null>(null);
  const [generateShotId, setGenerateShotId] = useState<string | null>(null);
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
    setGenerateShotId(null);
  }, [resetBatch, resetEnqueue, resetUpdate, sessionVersion]);

  const generateDisabledReason = !connection
    ? "Select a connection in Generation settings first."
    : !settings.modelId.trim()
      ? "Enter a model ID in Generation settings first."
      : null;

  const updateShotId = updateMutation.variables?.shotId ?? null;
  const updateErrors = useMemo(
    () => (updateMutation.error ? mapShotUpdateError(updateMutation.error) : null),
    [updateMutation.error],
  );

  const selectShot = (shotId: string) => {
    if (shotId !== selectedShot?.id) setGenerateShotId(null);
    // A new shot opens on its default tab.
    onSearchChange({ shot: shotId });
  };
  const changeTab = (next: ShotBoardTab) => {
    if (!selectedShot) return;
    onSearchChange({ shot: selectedShot.id, tab: next }, { replace: true });
  };

  const openEditor = (shotId: string) => {
    resetUpdate();
    setGenerateShotId(null);
    setEditingShotId(shotId);
  };
  const closeEditor = () => {
    resetUpdate();
    setEditingShotId(null);
  };
  const saveShot = (shotId: string, update: ShotUpdate) => {
    updateMutation.mutate({ shotId, update });
  };

  const openGenerate = (shotId: string) => {
    resetEnqueue();
    setEditingShotId((current) => (current === shotId ? null : current));
    setGenerateShotId(shotId);
  };
  const confirmGenerate = (shot: ProjectShot) => {
    if (!connection || generateDisabledReason) return;
    enqueueMutation.mutate(
      buildEnqueueRequest({
        shotId: shot.id,
        connectionId: connection.id,
        modelId: settings.modelId,
        taskType: settings.taskType,
      }),
    );
    setGenerateShotId(null);
  };
  const enqueueShotId = enqueueMutation.variables?.request.shot_id ?? null;

  const refresh = () => {
    void shotsQuery.refetch({ cancelRefetch: true });
  };

  const renderShotActions = (shot: ProjectShot) => (
    <>
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
          onConfirm={() => confirmGenerate(shot)}
          onCancel={() => setGenerateShotId(null)}
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
      <header className="flex flex-col gap-3 rounded-xl border border-border bg-card px-4 py-3 shadow-2xs lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-primary/20 bg-primary/10 text-primary">
            <LayoutGrid className="size-4" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="shot-board-heading" className="text-xl font-bold tracking-tight">
                Shot Board
              </h2>
              <span className="break-all rounded-full bg-secondary px-2.5 py-0.5 font-mono text-xs text-muted-foreground">
                {projectId}
              </span>
              {shotsQuery.data && (
                <span className="font-mono text-xs text-muted-foreground">
                  {sceneIds.length} {sceneIds.length === 1 ? "scene" : "scenes"} · {shots.length}{" "}
                  {shots.length === 1 ? "shot" : "shots"}
                </span>
              )}
            </div>
            <nav
              aria-label="Related modules"
              className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
            >
              <Link
                to="/projects/$projectId/$module"
                params={{ projectId, module: "projects" }}
                className="inline-flex items-center gap-1.5 font-medium text-muted-foreground transition-colors hover:text-foreground"
              >
                <FolderCog className="size-3.5 text-primary" aria-hidden="true" />
                Project settings
              </Link>
              <Link
                to="/projects/$projectId/$module"
                params={{ projectId, module: "timeline-editor" }}
                className="inline-flex items-center gap-1.5 font-medium text-muted-foreground transition-colors hover:text-foreground"
              >
                <Clapperboard className="size-3.5 text-primary" aria-hidden="true" />
                Timeline
              </Link>
              <Link
                to="/projects/$projectId/$module"
                params={{ projectId, module: "script-studio" }}
                className="inline-flex items-center gap-1.5 font-medium text-muted-foreground transition-colors hover:text-foreground"
              >
                <BookOpenText className="size-3.5 text-primary" aria-hidden="true" />
                Characters &amp; Story Bible
              </Link>
            </nav>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
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
              Batch launch
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
            Refresh
          </Button>
        </div>
      </header>

      <GenerationSettings
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
            onLaunch={(request) => batchMutation.mutate(request)}
          />
        </div>
      )}

      <section aria-label="Shots">
        <QueryState
          isLoading={shotsQuery.isPending}
          isError={shotsQuery.isError}
          hasData={Boolean(shotsQuery.data)}
          isEmpty={shots.length === 0}
          loadingMessage="Loading shots..."
          errorMessage={authErrorMessage(shotsQuery.error, activeAdminToken, "shots")}
          emptyTitle="No shots yet"
          emptyDescription="Import a script to break it into scenes and shots; they will appear here."
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
                  Shot <span className="break-all font-mono">{missingShotId}</span> is not in this
                  project; showing the first shot instead.
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

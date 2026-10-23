import { Link } from "@tanstack/react-router";
import {
  BookOpenText,
  Clapperboard,
  FolderCog,
  LayoutGrid,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import { useConnections } from "@/entities/connection/api";
import {
  useEnqueueShotTask,
  useLaunchBatch,
  useProjectShots,
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
import { ShotList } from "@/features/shot-board/components/shot-list";
import { ShotTakeReview } from "@/features/shot-board/components/shot-take-review";
import {
  buildEnqueueRequest,
  generationErrorMessage,
  groupShotsByScene,
  mapShotUpdateError,
  type ProjectShot,
  type ShotEditField,
  type ShotUpdate,
} from "@/features/shot-board/state";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

interface ShotBoardPageProps {
  projectId: string;
}

const NO_FIELD_ERRORS: Partial<Record<ShotEditField, string>> = {};

export function ShotBoardPage({ projectId }: ShotBoardPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const shotsQuery = useProjectShots(projectId);
  const connectionsQuery = useConnections();
  const updateMutation = useUpdateShot(projectId);
  const enqueueMutation = useEnqueueShotTask(projectId);
  const batchMutation = useLaunchBatch(projectId);

  const [settings, setSettings] = useState<GenerationSettingsValue>({
    connectionId: "",
    modelId: "",
    taskType: "video",
  });
  const [editingShotId, setEditingShotId] = useState<string | null>(null);
  const [generateShotId, setGenerateShotId] = useState<string | null>(null);
  const [reviewShotId, setReviewShotId] = useState<string | null>(null);
  const [boardMode, setBoardMode] = useState<"storyboard" | "shot-editor">("storyboard");

  const shots = useMemo(() => shotsQuery.data ?? [], [shotsQuery.data]);
  const sceneIds = useMemo(() => groupShotsByScene(shots).map((group) => group.sceneId), [shots]);
  const shotStats = useMemo(() => {
    let approved = 0;
    let succeeded = 0;
    let frameChained = 0;
    let blocked = 0;
    for (const shot of shots) {
      if (shot.approval_state === "approved") approved += 1;
      if (shot.generation_state === "succeeded") succeeded += 1;
      if (shot.continuity_mode === "frame_chain") frameChained += 1;
      if (
        shot.generation_state === "blocked_reference" ||
        shot.generation_state === "blocked_upstream_frame" ||
        shot.lint_report.some((issue) => issue.severity === "blocker")
      ) {
        blocked += 1;
      }
    }
    return { total: shots.length, approved, succeeded, frameChained, blocked };
  }, [shots]);
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
    setReviewShotId(null);
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

  const openEditor = (shotId: string) => {
    resetUpdate();
    setGenerateShotId(null);
    setEditingShotId(shotId);
    setBoardMode("shot-editor");
  };
  const closeEditor = () => {
    resetUpdate();
    setEditingShotId(null);
    setBoardMode("storyboard");
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

  const toggleTakeReview = (shotId: string) => {
    setReviewShotId((current) => (current === shotId ? null : shotId));
  };

  const refresh = () => {
    void shotsQuery.refetch({ cancelRefetch: true });
  };

  const renderShotDetail = (shot: ProjectShot) => (
    <>
      {reviewShotId === shot.id && (
        <ShotTakeReview
          projectId={projectId}
          shot={shot}
          shots={shots}
          connection={connection}
          modelId={settings.modelId}
          regenerateDisabledReason={generateDisabledReason}
          onClose={() => setReviewShotId(null)}
        />
      )}
      {editingShotId === shot.id && (
        <ShotEditPanel
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
        <p role="status" className="mt-4 text-sm text-muted-foreground">
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
          className="mt-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
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
      className="space-y-5"
    >
      {/* LTX Studio-inspired Top Bar + Secondary Studio Ribbon */}
      <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-2xs">
        <header className="flex flex-col gap-4 border-b border-border px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-start gap-3.5">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-primary/20 bg-primary/10 text-primary">
              <LayoutGrid className="size-5" aria-hidden="true" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded bg-primary px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-primary-foreground">
                  STUDIO
                </span>
                <h2
                  id="shot-board-heading"
                  className="text-xl font-bold tracking-tight sm:text-2xl"
                >
                  Shot Board
                </h2>
                <span className="rounded-full bg-secondary px-2.5 py-0.5 font-mono text-xs text-muted-foreground">
                  {projectId}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground sm:text-sm">
                Review, edit and generate shots for project{" "}
                <span className="font-medium text-foreground">{projectId}</span>.
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {/* Segmented Mode Switcher: Storyboard | Shot Editor */}
            <div className="inline-flex rounded-xl border border-border bg-secondary/60 p-1">
              <button
                type="button"
                onClick={() => setBoardMode("storyboard")}
                className={cn(
                  "rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-all",
                  boardMode === "storyboard"
                    ? "bg-foreground text-background shadow-xs"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                Storyboard
              </button>
              <button
                type="button"
                onClick={() => {
                  setBoardMode("shot-editor");
                  if (!editingShotId && shots[0]) {
                    openEditor(shots[0].id);
                  }
                }}
                className={cn(
                  "rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-all",
                  boardMode === "shot-editor"
                    ? "bg-foreground text-background shadow-xs"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                Shot Editor
              </button>
            </div>

            <QueueMonitorLink projectId={projectId} />

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

        {/* Secondary Studio Quick-Links Bar (LTX Studio Project Settings / Soundtrack / Characters ribbon) */}
        <div className="flex flex-wrap items-center justify-between gap-3 bg-secondary/35 px-5 py-2.5 text-xs">
          <div className="flex flex-wrap items-center gap-4">
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
              Soundtrack &amp; Timeline
            </Link>
            <Link
              to="/projects/$projectId/$module"
              params={{ projectId, module: "script-studio" }}
              className="inline-flex items-center gap-1.5 font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              <BookOpenText className="size-3.5 text-primary" aria-hidden="true" />
              Characters &amp; Story Bible
            </Link>
          </div>

          <div className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
            <SlidersHorizontal className="size-3.5 text-primary" aria-hidden="true" />
            <span>
              {sceneIds.length} {sceneIds.length === 1 ? "Scene" : "Scenes"} · {shotStats.total}{" "}
              {shotStats.total === 1 ? "Shot" : "Shots"}
            </span>
          </div>
        </div>
      </div>

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

      {shots.length > 0 && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <div className="rounded-xl border border-border bg-card px-3.5 py-2.5 shadow-2xs">
              <p className="text-xs font-medium text-muted-foreground">Total Shots</p>
              <p className="mt-0.5 text-lg font-bold tabular-nums">{shotStats.total}</p>
            </div>
            <div className="rounded-xl border border-border bg-card px-3.5 py-2.5 shadow-2xs">
              <p className="text-xs font-medium text-muted-foreground">Scenes</p>
              <p className="mt-0.5 text-lg font-bold tabular-nums">{sceneIds.length}</p>
            </div>
            <div className="rounded-xl border border-border bg-card px-3.5 py-2.5 shadow-2xs">
              <p className="text-xs font-medium text-muted-foreground">Rendered / Succeeded</p>
              <p className="mt-0.5 text-lg font-bold tabular-nums text-success">
                {shotStats.succeeded}
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card px-3.5 py-2.5 shadow-2xs">
              <p className="text-xs font-medium text-muted-foreground">Frame-Chained</p>
              <p className="mt-0.5 text-lg font-bold tabular-nums text-primary">
                {shotStats.frameChained}
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card px-3.5 py-2.5 shadow-2xs">
              <p className="text-xs font-medium text-muted-foreground">Blocked / Needs Lock</p>
              <p className="mt-0.5 text-lg font-bold tabular-nums text-destructive">
                {shotStats.blocked}
              </p>
            </div>
          </div>

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
        </>
      )}

      <section aria-label="Shots" className="space-y-3">
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
          <ShotList
            shots={shots}
            editingShotId={editingShotId}
            onEdit={openEditor}
            onGenerate={openGenerate}
            reviewingShotId={reviewShotId}
            onReviewTakes={toggleTakeReview}
            canGenerate={!generateDisabledReason && !enqueueMutation.isPending}
            generateDisabledReason={generateDisabledReason}
            renderShotDetail={renderShotDetail}
          />
        </QueryState>
      </section>
    </section>
  );
}

export default ShotBoardPage;

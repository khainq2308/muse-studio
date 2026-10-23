import { RefreshCw } from "lucide-react";
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

  const shots = useMemo(() => shotsQuery.data ?? [], [shotsQuery.data]);
  const sceneIds = useMemo(() => groupShotsByScene(shots).map((group) => group.sceneId), [shots]);
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

  const renderShotDetail = (shot: ProjectShot) => (
    <>
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
      className="space-y-6"
    >
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-primary">Project shots</p>
          <h2 id="shot-board-heading" className="mt-1 text-2xl font-semibold tracking-tight">
            Shot Board
          </h2>
          <p className="mt-2 break-all text-sm text-muted-foreground">
            Review, edit and generate shots for project{" "}
            <span className="font-medium text-foreground">{projectId}</span>.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
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

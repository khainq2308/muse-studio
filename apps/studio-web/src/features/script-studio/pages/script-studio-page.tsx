import { Link } from "@tanstack/react-router";
import {
  ArrowRight,
  BookOpenText,
  Download,
  FileText,
  LoaderCircle,
  Plus,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import {
  useApplyScriptRevision,
  useExportProjectShots,
  useImportScript,
  useProjectShots,
  useScriptRevisions,
  useStoryBible,
  useUploadReferenceAsset,
  useUpsertStoryBibleEntry,
} from "@/features/script-studio/api";
import { isShotBlocked } from "@/entities/shot/status";
import {
  entriesNeedingReference,
  isBlockedOnCharacterReference,
} from "@/features/script-studio/attention";
import { ScriptImportPanel } from "@/features/script-studio/components/script-import-panel";
import { StoryBibleAttention } from "@/features/script-studio/components/story-bible-attention";
import { StoryBibleEditor } from "@/features/script-studio/components/story-bible-editor";
import { StoryBibleList } from "@/features/script-studio/components/story-bible-list";
import {
  mapBibleEntryError,
  mapScriptImportError,
  referenceUploadErrorMessage,
  shotExportErrorMessage,
  type BibleEntryField,
  type ScriptImportField,
  type ShotExportFormat,
  type StoryBibleEntry,
} from "@/features/script-studio/state";
import {
  SCRIPT_STUDIO_VIEWS,
  defaultScriptStudioView,
  type ScriptStudioView,
} from "@/features/script-studio/search";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/shared/page-header";

interface ScriptStudioPageProps {
  projectId: string;
  /** View from the URL (`?view=`); undefined lets the page pick one from the project's data. */
  view: ScriptStudioView | undefined;
  onViewChange: (view: ScriptStudioView, options?: { replace?: boolean }) => void;
}

const NO_BIBLE_FIELD_ERRORS: Partial<Record<BibleEntryField, string>> = {};
const NO_IMPORT_FIELD_ERRORS: Partial<Record<ScriptImportField, string>> = {};
const NEW_EDITOR_KEY = "__new__";

export function ScriptStudioPage({ projectId, view, onViewChange }: ScriptStudioPageProps) {
  const { t } = useTranslation("script-studio");
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const sessionVersion = useSessionStore((state) => state.sessionVersion);

  const bibleQuery = useStoryBible(projectId);
  const shotsQuery = useProjectShots(projectId);
  const revisionsQuery = useScriptRevisions(projectId);
  const upsertMutation = useUpsertStoryBibleEntry(projectId);
  const importMutation = useImportScript(projectId);
  const applyRevisionMutation = useApplyScriptRevision(projectId);
  const uploadMutation = useUploadReferenceAsset(projectId);
  const exportMutation = useExportProjectShots(projectId);

  const [creatingNewEntry, setCreatingNewEntry] = useState(false);
  const [editingCodeName, setEditingCodeName] = useState<string | null>(null);
  const [newEntryNonce, setNewEntryNonce] = useState(0);

  const entries = useMemo(() => bibleQuery.data ?? [], [bibleQuery.data]);
  const revisions = useMemo(() => revisionsQuery.data ?? [], [revisionsQuery.data]);
  // Without loaded data the entry count is unknown; never present it as a real "0".
  const entryCountKnown = bibleQuery.data !== undefined && !bibleQuery.isError;
  const shots = useMemo(() => shotsQuery.data ?? [], [shotsQuery.data]);
  const attentionItems = useMemo(() => entriesNeedingReference(entries, shots), [entries, shots]);
  // Distinct shots blocked by a missing character reference (a shot can use several), and all
  // blocked shots: other blockers are only explained on the Shot Board.
  const blockedByBible = useMemo(() => {
    const codes = new Set(attentionItems.map((item) => item.entry.code_name));
    return shots.filter((shot) => isBlockedOnCharacterReference(shot, codes)).length;
  }, [attentionItems, shots]);
  const blockedTotal = useMemo(() => shots.filter(isShotBlocked).length, [shots]);

  // Without a view in the URL, wait for both lists before choosing so the page never flips
  // from one view to the other, then write the choice into the URL (replacing the entry). From
  // then on the view is explicit: an import that adds entries or a failed refresh never switches
  // the view under the user.
  const dataSettled = !bibleQuery.isPending && !shotsQuery.isPending;
  const derivedView = dataSettled
    ? defaultScriptStudioView({
        hasEntries: entries.length > 0,
        hasShots: shots.length > 0,
        bibleFailed: bibleQuery.isError,
      })
    : undefined;
  const activeView: ScriptStudioView | undefined = view ?? derivedView;
  useEffect(() => {
    if (view === undefined && derivedView) onViewChange(derivedView, { replace: true });
  }, [derivedView, onViewChange, view]);

  const idPrefix = useId();
  const tabId = (value: ScriptStudioView) => `${idPrefix}-tab-${value}`;
  const panelId = (value: ScriptStudioView) => `${idPrefix}-panel-${value}`;
  const tabRefs = useRef<Partial<Record<ScriptStudioView, HTMLButtonElement | null>>>({});
  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!activeView) return;
    const index = SCRIPT_STUDIO_VIEWS.indexOf(activeView);
    const last = SCRIPT_STUDIO_VIEWS.length - 1;
    const next =
      event.key === "ArrowRight"
        ? SCRIPT_STUDIO_VIEWS[index === last ? 0 : index + 1]
        : event.key === "ArrowLeft"
          ? SCRIPT_STUDIO_VIEWS[index === 0 ? last : index - 1]
          : event.key === "Home"
            ? SCRIPT_STUDIO_VIEWS[0]
            : event.key === "End"
              ? SCRIPT_STUDIO_VIEWS[last]
              : undefined;
    if (!next) return;
    event.preventDefault();
    onViewChange(next);
    tabRefs.current[next]?.focus();
  };

  // "Add reference" opens an entry's editor; bring it into view once it has rendered.
  const [scrollToEditor, setScrollToEditor] = useState<string | null>(null);
  useEffect(() => {
    if (!scrollToEditor) return;
    document
      .querySelector(`[data-testid="story-bible-editor-${CSS.escape(scrollToEditor)}"]`)
      ?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    setScrollToEditor(null);
  }, [scrollToEditor]);

  const { reset: resetUpsert } = upsertMutation;
  const { reset: resetImport } = importMutation;
  const { reset: resetApplyRevision } = applyRevisionMutation;
  const { reset: resetUpload } = uploadMutation;
  const { reset: resetExport } = exportMutation;

  // A new admin session must not show results or errors from the previous one.
  const startedSession = useRef(sessionVersion);
  useEffect(() => {
    if (startedSession.current === sessionVersion) return;
    startedSession.current = sessionVersion;
    resetUpsert();
    resetImport();
    resetApplyRevision();
    resetUpload();
    resetExport();
    setCreatingNewEntry(false);
    setEditingCodeName(null);
  }, [resetApplyRevision, resetExport, resetImport, resetUpload, resetUpsert, sessionVersion]);

  const upsertInCurrentSession = upsertMutation.variables?.sessionVersion === sessionVersion;
  const importInCurrentSession = importMutation.variables?.sessionVersion === sessionVersion;
  const applyInCurrentSession = applyRevisionMutation.variables?.sessionVersion === sessionVersion;
  const uploadInCurrentSession = uploadMutation.variables?.sessionVersion === sessionVersion;
  const exportInCurrentSession = exportMutation.variables?.sessionVersion === sessionVersion;

  const isEditorBusy =
    (upsertInCurrentSession && upsertMutation.isPending) ||
    (uploadInCurrentSession && uploadMutation.isPending);

  const upsertErrors = useMemo(
    () =>
      upsertInCurrentSession && upsertMutation.error
        ? mapBibleEntryError(upsertMutation.error)
        : null,
    [upsertInCurrentSession, upsertMutation.error],
  );
  const activeScriptError =
    (applyInCurrentSession && applyRevisionMutation.error) ||
    (importInCurrentSession && importMutation.error) ||
    null;
  const importErrors = useMemo(
    () => (activeScriptError ? mapScriptImportError(activeScriptError) : null),
    [activeScriptError],
  );
  const activeScriptResult =
    (applyInCurrentSession ? applyRevisionMutation.data : undefined) ??
    (importInCurrentSession ? importMutation.data : undefined) ??
    null;
  const uploadError = useMemo(
    () =>
      uploadInCurrentSession && uploadMutation.error
        ? referenceUploadErrorMessage(uploadMutation.error)
        : null,
    [uploadInCurrentSession, uploadMutation.error],
  );
  const exportFormat: ShotExportFormat = exportInCurrentSession
    ? (exportMutation.variables?.format ?? "json")
    : "json";
  const exportError = useMemo(
    () =>
      exportInCurrentSession && exportMutation.error
        ? shotExportErrorMessage(exportMutation.error, exportFormat)
        : null,
    [exportFormat, exportInCurrentSession, exportMutation.error],
  );

  const openNewEditor = () => {
    if (isEditorBusy) return;
    resetUpsert();
    resetUpload();
    setEditingCodeName(null);
    setCreatingNewEntry(true);
    setNewEntryNonce((current) => current + 1);
  };

  const openEditEntry = (entry: StoryBibleEntry) => {
    if (isEditorBusy) return;
    resetUpsert();
    resetUpload();
    setCreatingNewEntry(false);
    setEditingCodeName((current) => (current === entry.code_name ? null : entry.code_name));
  };

  const openReferenceEditor = (entry: StoryBibleEntry) => {
    if (isEditorBusy) return;
    resetUpsert();
    resetUpload();
    setCreatingNewEntry(false);
    setEditingCodeName(entry.code_name);
    setScrollToEditor(entry.code_name);
  };

  const closeEditor = () => {
    if (isEditorBusy) return;
    resetUpsert();
    resetUpload();
    setCreatingNewEntry(false);
    setEditingCodeName(null);
  };

  const renderEditorInstance = (
    entry: StoryBibleEntry | null,
    editorKey: string,
    reactKey: string,
  ) => {
    const isUpsertTarget =
      upsertInCurrentSession && upsertMutation.variables?.editorKey === editorKey;
    const isUploadTarget =
      uploadInCurrentSession && uploadMutation.variables?.editorKey === editorKey;
    return (
      <StoryBibleEditor
        key={reactKey}
        entry={entry}
        isSaving={isUpsertTarget && upsertMutation.isPending}
        fieldErrors={
          isUpsertTarget && upsertErrors ? upsertErrors.fieldErrors : NO_BIBLE_FIELD_ERRORS
        }
        formError={isUpsertTarget ? (upsertErrors?.formError ?? null) : null}
        upsertResult={isUpsertTarget ? (upsertMutation.data ?? null) : null}
        isUploadingAsset={isUploadTarget && uploadMutation.isPending}
        uploadError={isUploadTarget ? uploadError : null}
        uploadedAsset={isUploadTarget ? (uploadMutation.data ?? null) : null}
        onUploadAsset={(file) => {
          resetUpload();
          uploadMutation.mutate({ file, isLockedReference: true, editorKey });
        }}
        onSave={(request) => {
          resetUpsert();
          upsertMutation.mutate(request, { editorKey });
        }}
        onClose={closeEditor}
      />
    );
  };

  // Shots too: blocked-shot counts on the tab, the attention panel and the next step use them.
  const refresh = () => {
    void bibleQuery.refetch({ cancelRefetch: true });
    void shotsQuery.refetch({ cancelRefetch: true });
    void revisionsQuery.refetch({ cancelRefetch: true });
  };

  const renderEntryEditor = (entry: StoryBibleEntry) => {
    if (editingCodeName !== entry.code_name) return null;
    return renderEditorInstance(
      entry,
      entry.code_name,
      `${sessionVersion}:edit:${entry.code_name}`,
    );
  };

  const exportPending = exportInCurrentSession && exportMutation.isPending;

  return (
    <section
      data-testid="script-studio-page"
      data-project-id={projectId}
      aria-labelledby="script-studio-heading"
      className="space-y-5"
    >
      <PageHeader
        title={t("title")}
        headingId="script-studio-heading"
        description={t("description")}
        actions={
          <>
            <div
              role="group"
              aria-label={t("exportShots")}
              className="flex items-center gap-1 rounded-md border border-border p-0.5"
            >
              <span className="px-2 text-xs text-muted-foreground">{t("exportShots")}</span>
              {(["json", "csv"] as const).map((format) => (
                <Button
                  key={format}
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={exportPending}
                  aria-label={t("exportAria", { format: format.toUpperCase() })}
                  onClick={() => {
                    resetExport();
                    exportMutation.mutate(format);
                  }}
                >
                  {exportPending && exportFormat === format ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Download className="size-4" aria-hidden="true" />
                  )}
                  {format.toUpperCase()}
                </Button>
              ))}
            </div>

            <Button
              type="button"
              variant="outline"
              onClick={refresh}
              aria-busy={bibleQuery.isFetching}
            >
              <RefreshCw
                className={cn("size-4", bibleQuery.isFetching && "animate-spin")}
                aria-hidden="true"
              />
              {t("refresh")}
            </Button>
          </>
        }
      />

      {exportError && (
        <p
          role="alert"
          data-testid="shot-export-error"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {exportError}
        </p>
      )}

      {!activeView ? (
        <p role="status" className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
          <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          {t("loading")}
        </p>
      ) : (
        <div>
          <div
            role="tablist"
            aria-label="Script & Story Bible views"
            onKeyDown={onTabKeyDown}
            className="flex gap-1 overflow-x-auto border-b border-border"
          >
            {SCRIPT_STUDIO_VIEWS.map((value) => {
              const active = value === activeView;
              const Icon = value === "script" ? FileText : BookOpenText;
              return (
                <button
                  key={value}
                  ref={(element) => {
                    tabRefs.current[value] = element;
                  }}
                  type="button"
                  role="tab"
                  id={tabId(value)}
                  aria-selected={active}
                  aria-controls={panelId(value)}
                  tabIndex={active ? 0 : -1}
                  onClick={() => onViewChange(value)}
                  className={cn(
                    "-mb-px inline-flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    active
                      ? "border-primary text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Icon className="size-4" aria-hidden="true" />
                  {t(`views.${value}`)}
                  {value === "bible" && entryCountKnown && (
                    <span className="rounded-full bg-secondary px-1.5 text-xs tabular-nums text-muted-foreground">
                      <span className="sr-only">, </span>
                      {t("views.entriesCount", { count: entries.length })}
                    </span>
                  )}
                  {value === "bible" && attentionItems.length > 0 && (
                    <span className="inline-flex items-center gap-0.5 rounded-full bg-warning/15 px-1.5 text-xs font-semibold tabular-nums text-warning">
                      <TriangleAlert className="size-3" aria-hidden="true" />
                      <span className="sr-only">, </span>
                      {t("views.toFixCount", { count: attentionItems.length })}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Both panels stay mounted (the inactive one hidden) so switching views never
              discards a script being typed or an unsaved Story Bible edit. */}
          <div
            role="tabpanel"
            id={panelId("script")}
            aria-labelledby={tabId("script")}
            hidden={activeView !== "script"}
            data-testid="script-studio-panel-script"
            className="pt-5"
          >
            <ScriptImportPanel
              key={sessionVersion}
              projectId={projectId}
              existingEntriesCount={entries.length}
              existingShotsCount={shots.length}
              isCheckingPipeline={bibleQuery.isPending || shotsQuery.isPending}
              pipelineCheckError={Boolean(bibleQuery.isError || shotsQuery.isError)}
              revisions={revisions}
              isImporting={importInCurrentSession && importMutation.isPending}
              isApplyingRevision={applyInCurrentSession && applyRevisionMutation.isPending}
              applyingRevisionId={
                applyInCurrentSession ? (applyRevisionMutation.variables?.revisionId ?? null) : null
              }
              fieldErrors={importErrors ? importErrors.fieldErrors : NO_IMPORT_FIELD_ERRORS}
              formError={importErrors?.formError ?? null}
              requiresReplaceConfirmation={Boolean(importErrors?.requiresReplaceConfirmation)}
              result={activeScriptResult}
              onImport={(request) => {
                resetImport();
                resetApplyRevision();
                importMutation.mutate(request);
              }}
              onApplyRevision={(revisionId, replaceExisting) => {
                resetApplyRevision();
                applyRevisionMutation.mutate({
                  revisionId,
                  request: { replace_existing: replaceExisting },
                });
              }}
              onDismissResult={() => {
                resetImport();
                resetApplyRevision();
              }}
              onReviewStoryBible={() => onViewChange("bible")}
            />
          </div>
          <div
            role="tabpanel"
            id={panelId("bible")}
            aria-labelledby={tabId("bible")}
            hidden={activeView !== "bible"}
            data-testid="script-studio-panel-bible"
            className="pt-5"
          >
            <section aria-labelledby="story-bible-section-heading" className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 id="story-bible-section-heading" className="text-lg font-semibold">
                    {t("bible.title", { count: entryCountKnown ? entries.length : "—" })}
                  </h3>
                  <p
                    className="mt-0.5 text-xs text-muted-foreground"
                    data-testid="story-bible-summary"
                  >
                    {t("bible.summary")}
                  </p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant={creatingNewEntry ? "secondary" : "default"}
                  disabled={isEditorBusy}
                  aria-expanded={creatingNewEntry}
                  onClick={openNewEditor}
                >
                  <Plus className="size-4" aria-hidden="true" />
                  {t("bible.newEntry")}
                </Button>
              </div>

              <StoryBibleAttention
                items={attentionItems}
                shotCountsKnown={shotsQuery.isSuccess}
                isEditorBusy={isEditorBusy}
                onAddReference={openReferenceEditor}
              />

              {creatingNewEntry &&
                renderEditorInstance(
                  null,
                  NEW_EDITOR_KEY,
                  `${sessionVersion}:new:${newEntryNonce}`,
                )}

              <QueryState
                isLoading={bibleQuery.isPending}
                isError={bibleQuery.isError}
                hasData={Boolean(bibleQuery.data)}
                isEmpty={entries.length === 0}
                loadingMessage={t("loading")}
                errorMessage={authErrorMessage(bibleQuery.error, activeAdminToken, "Story Bible")}
                emptyTitle={t("bible.emptyTitle")}
                emptyDescription={t("bible.emptyDescription")}
                onRetry={refresh}
              >
                <StoryBibleList
                  entries={entries}
                  editingCodeName={editingCodeName}
                  isEditorBusy={isEditorBusy}
                  onEdit={openEditEntry}
                  renderEntryEditor={renderEntryEditor}
                />
              </QueryState>

              {shots.length > 0 && (
                <div
                  data-testid="script-studio-next-step"
                  className="flex flex-col gap-3 rounded-xl border border-border bg-secondary/40 p-4 sm:flex-row sm:items-center sm:justify-between"
                >
                  <p className="text-sm">
                    <span className="font-medium">{t("nextStep.title")}</span>{" "}
                    <span className="text-muted-foreground">
                      {blockedByBible > 0
                        ? t("nextStep.blockedByBible", { count: blockedByBible })
                        : blockedTotal > 0
                          ? t("nextStep.blockedTotal", { count: blockedTotal })
                          : t("nextStep.ready", { count: shots.length })}
                    </span>
                  </p>
                  <Link
                    to="/projects/$projectId/$module"
                    params={{ projectId, module: "shot-board" }}
                    className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:opacity-90"
                  >
                    {t("nextStep.action")}
                    <ArrowRight className="size-4" aria-hidden="true" />
                  </Link>
                </div>
              )}
            </section>
          </div>
        </div>
      )}
    </section>
  );
}

export default ScriptStudioPage;

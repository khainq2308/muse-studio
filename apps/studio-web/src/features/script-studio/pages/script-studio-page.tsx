import { Link } from "@tanstack/react-router";
import { Download, LayoutGrid, LoaderCircle, Plus, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import {
  useExportProjectShots,
  useImportScript,
  useProjectShots,
  useStoryBible,
  useUploadReferenceAsset,
  useUpsertStoryBibleEntry,
} from "@/features/script-studio/api";
import { ScriptImportPanel } from "@/features/script-studio/components/script-import-panel";
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
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

interface ScriptStudioPageProps {
  projectId: string;
}

const NO_BIBLE_FIELD_ERRORS: Partial<Record<BibleEntryField, string>> = {};
const NO_IMPORT_FIELD_ERRORS: Partial<Record<ScriptImportField, string>> = {};
const NEW_EDITOR_KEY = "__new__";

export function ScriptStudioPage({ projectId }: ScriptStudioPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const sessionVersion = useSessionStore((state) => state.sessionVersion);

  const bibleQuery = useStoryBible(projectId);
  const shotsQuery = useProjectShots(projectId);
  const upsertMutation = useUpsertStoryBibleEntry(projectId);
  const importMutation = useImportScript(projectId);
  const uploadMutation = useUploadReferenceAsset(projectId);
  const exportMutation = useExportProjectShots(projectId);

  const [creatingNewEntry, setCreatingNewEntry] = useState(false);
  const [editingCodeName, setEditingCodeName] = useState<string | null>(null);
  const [newEntryNonce, setNewEntryNonce] = useState(0);

  const entries = useMemo(() => bibleQuery.data ?? [], [bibleQuery.data]);
  const shots = useMemo(() => shotsQuery.data ?? [], [shotsQuery.data]);

  const { reset: resetUpsert } = upsertMutation;
  const { reset: resetImport } = importMutation;
  const { reset: resetUpload } = uploadMutation;
  const { reset: resetExport } = exportMutation;

  // A new admin session must not show results or errors from the previous one.
  const startedSession = useRef(sessionVersion);
  useEffect(() => {
    if (startedSession.current === sessionVersion) return;
    startedSession.current = sessionVersion;
    resetUpsert();
    resetImport();
    resetUpload();
    resetExport();
    setCreatingNewEntry(false);
    setEditingCodeName(null);
  }, [resetExport, resetImport, resetUpload, resetUpsert, sessionVersion]);

  const upsertInCurrentSession = upsertMutation.variables?.sessionVersion === sessionVersion;
  const importInCurrentSession = importMutation.variables?.sessionVersion === sessionVersion;
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
  const importErrors = useMemo(
    () =>
      importInCurrentSession && importMutation.error
        ? mapScriptImportError(importMutation.error)
        : null,
    [importInCurrentSession, importMutation.error],
  );
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

  const refresh = () => {
    void bibleQuery.refetch({ cancelRefetch: true });
  };

  const renderEntryEditor = (entry: StoryBibleEntry) => {
    if (editingCodeName !== entry.code_name) return null;
    return renderEditorInstance(
      entry,
      entry.code_name,
      `${sessionVersion}:edit:${entry.code_name}`,
    );
  };

  return (
    <section
      data-testid="script-studio-page"
      data-project-id={projectId}
      aria-labelledby="script-studio-heading"
      className="space-y-6"
    >
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-primary">Script &amp; Story Bible</p>
          <h2 id="script-studio-heading" className="mt-1 text-2xl font-semibold tracking-tight">
            Script Studio
          </h2>
          <p className="mt-2 break-all text-sm text-muted-foreground">
            Import scripts, lock Story Bible references and export the compiled shot list for
            project <span className="font-medium text-foreground">{projectId}</span>.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Link
            to="/projects/$projectId/$module"
            params={{ projectId, module: "shot-board" }}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <LayoutGrid className="size-4" aria-hidden="true" />
            Shot Board
          </Link>

          <Button
            type="button"
            variant="outline"
            disabled={exportInCurrentSession && exportMutation.isPending}
            onClick={() => {
              resetExport();
              exportMutation.mutate("json");
            }}
          >
            {exportInCurrentSession && exportMutation.isPending && exportFormat === "json" ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Download className="size-4" aria-hidden="true" />
            )}
            Export JSON
          </Button>

          <Button
            type="button"
            variant="outline"
            disabled={exportInCurrentSession && exportMutation.isPending}
            onClick={() => {
              resetExport();
              exportMutation.mutate("csv");
            }}
          >
            {exportInCurrentSession && exportMutation.isPending && exportFormat === "csv" ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Download className="size-4" aria-hidden="true" />
            )}
            Export CSV
          </Button>

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
            Refresh
          </Button>
        </div>
      </header>

      {exportError && (
        <p
          role="alert"
          data-testid="shot-export-error"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {exportError}
        </p>
      )}

      <ScriptImportPanel
        key={sessionVersion}
        projectId={projectId}
        existingEntriesCount={entries.length}
        existingShotsCount={shots.length}
        isImporting={importInCurrentSession && importMutation.isPending}
        fieldErrors={importErrors ? importErrors.fieldErrors : NO_IMPORT_FIELD_ERRORS}
        formError={importErrors?.formError ?? null}
        requiresReplaceConfirmation={Boolean(importErrors?.requiresReplaceConfirmation)}
        result={importInCurrentSession ? (importMutation.data ?? null) : null}
        onImport={(request) => {
          resetImport();
          importMutation.mutate(request);
        }}
        onDismissResult={resetImport}
      />

      <section aria-labelledby="story-bible-section-heading" className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 id="story-bible-section-heading" className="text-lg font-semibold">
              Story Bible ({entries.length})
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Characters, locations, style rules and props compiled into downstream shot prompts.
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
            New Bible entry
          </Button>
        </div>

        {creatingNewEntry &&
          renderEditorInstance(null, NEW_EDITOR_KEY, `${sessionVersion}:new:${newEntryNonce}`)}

        <QueryState
          isLoading={bibleQuery.isPending}
          isError={bibleQuery.isError}
          hasData={Boolean(bibleQuery.data)}
          isEmpty={entries.length === 0}
          loadingMessage="Loading Story Bible..."
          errorMessage={authErrorMessage(bibleQuery.error, activeAdminToken, "Story Bible")}
          emptyTitle="No Story Bible entries yet"
          emptyDescription="Import a script with auto-apply enabled or create an entry to define characters, locations and style locks."
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
      </section>
    </section>
  );
}

export default ScriptStudioPage;

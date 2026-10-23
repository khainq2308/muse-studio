import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "@tanstack/react-router";
import {
  AlignLeft,
  BookOpen,
  CheckCircle2,
  Clapperboard,
  FileUp,
  LayoutGrid,
  LoaderCircle,
  MapPin,
  MessageSquare,
  Sparkles,
  TriangleAlert,
  UserRound,
  Wand2,
  X,
} from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import {
  SCRIPT_IMPORT_LIMITS,
  SCRIPT_SOURCE_FORMATS,
  SCRIPT_SOURCE_FORMAT_LABELS,
  buildScriptImportRequest,
  type ScriptImportField,
  type ScriptImportFormValues,
  type ScriptImportRequest,
  type ScriptImportResult,
} from "@/features/script-studio/state";
import { cn } from "@/lib/utils";

const SAMPLE_MUSEFLOW_SCRIPT = `SCENE 1: Neon Alley - Rainy Night
SHOT 1: Detective Linh arrives under the flickering sign
- Character: Detective Linh
- Location: Neon Alley
- Shot_Size: medium_shot
- Camera_Motion: slow_dolly_in
- Action: Linh pauses under the awning and scans the wet pavement
- Action_Start: stepping beneath the neon sign
- Action_End: looking toward the alley exit
- Duration: 4s

SHOT 2: Close-up on the mysterious cipher key
- Character: Detective Linh
- Location: Neon Alley
- Shot_Size: close_up
- Camera_Motion: static
- Action: Linh holds up the brass cipher key under the neon glow
- Duration: 3s`;

const SCREENPLAY_SNIPPETS: ReadonlyArray<{
  id: string;
  label: string;
  snippet: string;
}> = [
  {
    id: "scene",
    label: "Scene Heading",
    snippet: "\nSCENE 2: INT. GRAND HOTEL LOBBY - NIGHT\n",
  },
  {
    id: "shot",
    label: "Shot Block",
    snippet:
      "\nSHOT 1: Wide establishing frame\n- Shot_Size: wide_shot\n- Camera_Motion: slow_pan\n- Duration: 4s\n",
  },
  {
    id: "action",
    label: "Action",
    snippet: "- Action: Crystals cast warm reflections across the marble floor\n",
  },
  {
    id: "character",
    label: "Character",
    snippet: "- Character: Detective Linh\n",
  },
  {
    id: "location",
    label: "Location",
    snippet: "- Location: Grand Hotel Lobby\n",
  },
  {
    id: "dialogue",
    label: "Dialogue",
    snippet: '- Dialogue: "I am in position. Visual on the main terrace."\n',
  },
];

/**
 * Client-side outline of the textarea content. This is a rough local preview only: it is
 * derived with simple line patterns and is NOT the server analyzer's result, which is
 * reported in the import result after submission.
 */
interface LocalOutlineGroup {
  key: string;
  /** 1-based position among detected scene headings; `null` for shots before any heading. */
  sceneNumber: number | null;
  heading: string | null;
  shotCount: number;
  preview: string;
}

function extractLocalSceneOutline(rawContent: string): LocalOutlineGroup[] {
  const lines = rawContent.split(/\r?\n/);
  const groups: LocalOutlineGroup[] = [];
  let current: LocalOutlineGroup | null = null;
  let sceneCount = 0;

  for (const [lineIndex, rawLine] of lines.entries()) {
    const line = rawLine.trim();
    if (!line) continue;

    if (/^(?:SCENE\s+\d+|INT\.|EXT\.|#\s*SCENE)/i.test(line)) {
      if (current) groups.push(current);
      sceneCount += 1;
      current = {
        key: `scene-${lineIndex}`,
        sceneNumber: sceneCount,
        heading: line.replace(/^#\s*/, ""),
        shotCount: 0,
        preview: "",
      };
      continue;
    }

    if (/^SHOT\s+\d+/i.test(line)) {
      if (!current) {
        // Shots before any scene heading: group them neutrally instead of inventing a heading.
        current = {
          key: "before-first-scene",
          sceneNumber: null,
          heading: null,
          shotCount: 0,
          preview: "",
        };
      }
      current.shotCount += 1;
      if (!current.preview) {
        current.preview = line.replace(/^SHOT\s+\d+:\s*/i, "");
      }
      continue;
    }

    if (current && !current.preview && line.startsWith("- Action:")) {
      current.preview = line.replace(/^- Action:\s*/i, "");
    }
  }

  if (current) groups.push(current);
  return groups;
}

const scriptImportSchema = z.object({
  raw_content: z
    .string()
    .refine(
      (value) => value.trim().length >= SCRIPT_IMPORT_LIMITS.minScriptChars,
      `Script content must be at least ${SCRIPT_IMPORT_LIMITS.minScriptChars} characters.`,
    )
    .max(
      SCRIPT_IMPORT_LIMITS.maxScriptChars,
      `Script content must be at most ${SCRIPT_IMPORT_LIMITS.maxScriptChars.toLocaleString()} characters.`,
    ),
  source_format: z
    .string()
    .trim()
    .min(1, "Source format is required.")
    .max(
      SCRIPT_IMPORT_LIMITS.sourceFormatMaxChars,
      `Source format must be at most ${SCRIPT_IMPORT_LIMITS.sourceFormatMaxChars} characters.`,
    ),
  auto_apply_proposal: z.boolean(),
  replace_existing: z.boolean(),
}) satisfies z.ZodType<ScriptImportFormValues>;

const inputClass = cn(
  "block w-full rounded-lg border border-input bg-background px-3 py-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "aria-[invalid=true]:border-destructive disabled:cursor-not-allowed disabled:opacity-60",
);

interface ScriptImportPanelProps {
  projectId: string;
  existingEntriesCount?: number;
  existingShotsCount?: number;
  isCheckingPipeline?: boolean;
  pipelineCheckError?: boolean;
  isImporting: boolean;
  fieldErrors: Partial<Record<ScriptImportField, string>>;
  formError: string | null;
  requiresReplaceConfirmation: boolean;
  result: ScriptImportResult | null;
  onImport: (request: ScriptImportRequest) => void;
  onDismissResult: () => void;
}

export function ScriptImportPanel({
  projectId,
  existingEntriesCount = 0,
  existingShotsCount = 0,
  isCheckingPipeline = false,
  pipelineCheckError = false,
  isImporting,
  fieldErrors,
  formError,
  requiresReplaceConfirmation,
  result,
  onImport,
  onDismissResult,
}: ScriptImportPanelProps) {
  const headingId = useId();
  const outlineHeadingId = useId();
  const idPrefix = useId();
  const [activeFormatTag, setActiveFormatTag] = useState("action");

  const form = useForm<ScriptImportFormValues>({
    resolver: zodResolver(scriptImportSchema),
    defaultValues: {
      raw_content: "",
      source_format: "museflow",
      auto_apply_proposal: false,
      replace_existing: false,
    },
    mode: "onSubmit",
  });

  const {
    register,
    handleSubmit,
    setError,
    setValue,
    clearErrors,
    watch,
    formState: { errors },
  } = form;

  useEffect(() => {
    clearErrors();
    for (const [field, message] of Object.entries(fieldErrors) as Array<
      [ScriptImportField, string]
    >) {
      setError(field, { type: "server", message });
    }
  }, [clearErrors, fieldErrors, setError]);

  useEffect(() => {
    if (!result) return;
    setValue("replace_existing", false);
  }, [result, setValue]);

  const rawContent = watch("raw_content");
  const autoApply = watch("auto_apply_proposal");

  const outlineGroups = useMemo(() => extractLocalSceneOutline(rawContent), [rawContent]);
  const localSceneCount = outlineGroups.filter((group) => group.sceneNumber !== null).length;
  const shotCountUnknown = isCheckingPipeline || pipelineCheckError;

  const fieldId = (field: ScriptImportField) => `${idPrefix}-${field}`;
  const errorId = (field: ScriptImportField) => `${idPrefix}-${field}-error`;
  const fieldProps = (field: ScriptImportField) => ({
    id: fieldId(field),
    "aria-invalid": errors[field] ? true : undefined,
    "aria-describedby": errors[field] ? errorId(field) : undefined,
    disabled: isImporting,
  });
  const renderFieldError = (field: ScriptImportField) =>
    errors[field]?.message ? (
      <p id={errorId(field)} className="text-xs text-destructive">
        {errors[field]?.message}
      </p>
    ) : null;

  const insertSample = () => {
    setValue("raw_content", SAMPLE_MUSEFLOW_SCRIPT, { shouldDirty: true });
    clearErrors("raw_content");
  };

  const appendSnippet = (id: string, snippet: string) => {
    setActiveFormatTag(id);
    const next = rawContent ? `${rawContent.trimEnd()}\n${snippet.trimStart()}` : snippet.trim();
    setValue("raw_content", next, { shouldDirty: true });
    clearErrors("raw_content");
  };

  const submit = handleSubmit((values) => {
    onImport(buildScriptImportRequest(values));
  });

  return (
    <section
      aria-labelledby={headingId}
      data-testid="script-import-panel"
      className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm"
    >
      {/* Top Studio Header Bar (WrittaShot-inspired) */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-secondary/40 px-5 py-3.5">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <BookOpen className="size-4" aria-hidden="true" />
            </span>
            <div>
              <h3 id={headingId} className="text-sm font-semibold tracking-tight sm:text-base">
                Script Import &amp; Screenplay Desk
              </h3>
              <p className="text-[11px] text-muted-foreground">
                3-pane screenplay breakdown · MuseFlow, Markdown &amp; plaintext analyzer
              </p>
            </div>
          </div>

          <span className="hidden h-4 w-px bg-border sm:inline-block" aria-hidden="true" />

          <div className="flex items-center gap-1.5 text-xs">
            <span
              className="rounded-md bg-amber-500/15 px-2 py-0.5 font-mono text-[11px] font-semibold text-amber-700 dark:text-amber-300"
              title="Counted locally from scene headings in the text box; the server analyzer may detect a different number."
              data-testid="local-scene-count"
            >
              Local preview: {localSceneCount} {localSceneCount === 1 ? "scene" : "scenes"}
            </span>
            <span
              className="rounded-md bg-primary/10 px-2 py-0.5 font-mono text-[11px] font-medium text-primary"
              data-testid="active-shots-count"
              title={
                isCheckingPipeline
                  ? "Checking existing shots..."
                  : pipelineCheckError
                    ? "Could not load existing shots."
                    : undefined
              }
            >
              {shotCountUnknown
                ? "— active shots"
                : `${existingShotsCount} active ${existingShotsCount === 1 ? "shot" : "shots"}`}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={isImporting}
            onClick={insertSample}
            className="bg-background shadow-2xs"
          >
            <Sparkles className="size-3.5 text-primary" aria-hidden="true" />
            Insert sample script
          </Button>
        </div>
      </div>

      {formError && (
        <div className="px-5 pt-4">
          <p
            role="alert"
            className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
          >
            {formError}
          </p>
        </div>
      )}

      {result && (
        <div className="px-5 pt-4">
          <div
            role="status"
            data-testid="script-import-result"
            className="space-y-3 rounded-xl border border-success/30 bg-success/10 p-4 text-sm"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="flex items-center gap-2 font-medium text-success">
                <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
                <span>
                  Imported script revision v{result.version_number} ({result.script_revision_id})
                </span>
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={onDismissResult}
                aria-label="Dismiss script import result"
              >
                <X className="size-3.5" aria-hidden="true" />
                Dismiss
              </Button>
            </div>

            <p className="text-xs text-foreground">{result.diff_summary}</p>

            <dl className="grid grid-cols-2 gap-2 rounded-lg bg-background/90 p-3 text-xs sm:grid-cols-4">
              <div>
                <dt className="text-muted-foreground">Characters detected</dt>
                <dd className="mt-0.5 text-sm font-semibold">{result.detected_characters}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Locations detected</dt>
                <dd className="mt-0.5 text-sm font-semibold">{result.detected_locations}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Scenes detected</dt>
                <dd className="mt-0.5 text-sm font-semibold">{result.detected_scenes}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Generated shots</dt>
                <dd className="mt-0.5 text-sm font-semibold">{result.generated_shots}</dd>
              </div>
            </dl>

            {result.warnings && result.warnings.length > 0 && (
              <div
                role="note"
                data-testid="script-import-warnings"
                className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning"
              >
                <p className="flex items-center gap-1.5 font-medium">
                  <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
                  Analyzer warnings ({result.warnings.length})
                </p>
                <ul className="mt-1.5 list-inside list-disc space-y-1">
                  {result.warnings.map((warning, index) => (
                    <li key={`${index}-${warning}`}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <Link
                to="/projects/$projectId/$module"
                params={{ projectId, module: "shot-board" }}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 text-xs font-medium text-primary hover:bg-primary/20"
              >
                <LayoutGrid className="size-3.5" aria-hidden="true" />
                Open Shot Board
              </Link>
              {result.raw_content_preserved && (
                <span className="text-xs text-muted-foreground">
                  Original raw script preserved verbatim.
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 3-Column WrittaShot-Style Screenplay Workspace */}
      <form
        className="grid divide-y divide-border lg:grid-cols-12 lg:divide-x lg:divide-y-0"
        onSubmit={submit}
        noValidate
      >
        {/* Left Column: local, non-interactive outline preview of the textarea content */}
        <div className="bg-secondary/20 p-4 lg:col-span-3">
          <div className="flex items-center justify-between gap-2">
            <h4
              id={outlineHeadingId}
              className="text-xs font-bold uppercase tracking-wider text-muted-foreground"
            >
              Scene Outline
            </h4>
            <span className="rounded-full bg-amber-500/15 px-2 py-0.5 font-mono text-[11px] font-semibold text-amber-700 dark:text-amber-300">
              {localSceneCount} {localSceneCount === 1 ? "Scene" : "Scenes"}
            </span>
          </div>
          <p
            data-testid="local-outline-disclaimer"
            className="mt-1 text-[11px] leading-snug text-muted-foreground"
          >
            Local outline preview — not analyzer output. The server analyzer reports detected scenes
            after import.
          </p>

          {outlineGroups.length === 0 ? (
            <div className="mt-3 rounded-xl border border-dashed border-border bg-background/70 p-3.5 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">No scene headings found yet</p>
              <p className="mt-1 leading-relaxed">
                Paste your screenplay or click <strong>Insert sample script</strong> to preview its
                scene headings here.
              </p>
            </div>
          ) : (
            <ol
              aria-labelledby={outlineHeadingId}
              className="mt-3 space-y-2"
              data-testid="local-scene-outline"
            >
              {outlineGroups.map((group) => (
                <li
                  key={group.key}
                  className="w-full rounded-xl border border-border bg-background p-3 text-left"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs font-bold text-amber-700 dark:text-amber-300">
                      {group.sceneNumber === null ? "—" : `#${group.sceneNumber}`}
                    </span>
                    <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                      {group.shotCount} {group.shotCount === 1 ? "shot" : "shots"}
                    </span>
                  </div>
                  {group.heading === null ? (
                    <p className="mt-1 text-xs font-medium italic text-muted-foreground">
                      Before first scene
                    </p>
                  ) : (
                    <p
                      className="mt-1 truncate text-xs font-semibold uppercase tracking-tight text-foreground"
                      title={group.heading}
                    >
                      {group.heading}
                    </p>
                  )}
                  {group.preview && (
                    <p className="mt-1 line-clamp-2 text-[11px] italic text-muted-foreground">
                      {group.preview}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          )}

          <div className="mt-4 rounded-xl border border-border bg-background p-3 text-xs">
            <p className="font-semibold text-foreground">Key Elements Tracked</p>
            <ul className="mt-2 space-y-1.5 text-[11px] text-muted-foreground">
              <li className="flex items-center gap-1.5">
                <Clapperboard className="size-3.5 text-primary" aria-hidden="true" />
                <span>SCENE / SHOT boundaries &amp; durations</span>
              </li>
              <li className="flex items-center gap-1.5">
                <UserRound className="size-3.5 text-primary" aria-hidden="true" />
                <span>Character traits &amp; reference locks</span>
              </li>
              <li className="flex items-center gap-1.5">
                <MapPin className="size-3.5 text-primary" aria-hidden="true" />
                <span>Locations &amp; lighting continuity</span>
              </li>
              <li className="flex items-center gap-1.5">
                <MessageSquare className="size-3.5 text-primary" aria-hidden="true" />
                <span>Action start/end &amp; camera motion</span>
              </li>
            </ul>
          </div>
        </div>

        {/* Center Column: Screenplay Formatting Toolbar & Script Page */}
        <div className="space-y-3 bg-secondary/10 p-4 sm:p-5 lg:col-span-5">
          <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-border bg-background p-1.5 shadow-2xs">
            <span className="px-2 text-[11px] font-medium text-muted-foreground">Type:</span>
            {SCREENPLAY_SNIPPETS.map((item) => {
              const isTagActive = activeFormatTag === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  disabled={isImporting}
                  onClick={() => appendSnippet(item.id, item.snippet)}
                  className={cn(
                    "rounded-md px-2 py-1 text-[11px] font-semibold transition-colors",
                    isTagActive
                      ? "bg-amber-500 text-slate-950 shadow-2xs"
                      : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                  )}
                >
                  {item.label}
                </button>
              );
            })}
          </div>

          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label
                htmlFor={fieldId("raw_content")}
                className="flex items-center gap-1.5 text-sm font-semibold"
              >
                <AlignLeft className="size-4 text-primary" aria-hidden="true" />
                Script content
              </label>
              <span className="font-mono text-xs text-muted-foreground">
                {rawContent.length.toLocaleString()} /{" "}
                {SCRIPT_IMPORT_LIMITS.maxScriptChars.toLocaleString()} chars
              </span>
            </div>

            <div className="rounded-xl border border-border bg-background p-1 shadow-inner">
              <textarea
                rows={12}
                placeholder="Paste SCENE / SHOT blocks or screenplay paragraphs here..."
                maxLength={SCRIPT_IMPORT_LIMITS.maxScriptChars}
                className={cn(
                  inputClass,
                  "border-0 bg-transparent font-mono text-xs leading-relaxed shadow-none focus-visible:ring-1",
                )}
                {...fieldProps("raw_content")}
                {...register("raw_content")}
              />
            </div>
            {renderFieldError("raw_content")}
          </div>
        </div>

        {/* Right Column: Analyzer Settings, Pipeline Verification & Import Actions */}
        <div className="flex flex-col justify-between space-y-4 bg-card p-4 sm:p-5 lg:col-span-4">
          <div className="space-y-4">
            <div className="flex items-center gap-2 border-b border-border pb-2.5">
              <Wand2 className="size-4 text-primary" aria-hidden="true" />
              <h4 className="text-sm font-semibold">Analyzer &amp; Pipeline Controls</h4>
            </div>

            <div className="space-y-1.5">
              <label htmlFor={fieldId("source_format")} className="text-sm font-medium">
                Source format
              </label>
              <select
                className={inputClass}
                {...fieldProps("source_format")}
                {...register("source_format")}
              >
                {SCRIPT_SOURCE_FORMATS.map((format) => (
                  <option key={format} value={format}>
                    {SCRIPT_SOURCE_FORMAT_LABELS[format]}
                  </option>
                ))}
              </select>
              {renderFieldError("source_format")}
            </div>

            <div className="space-y-3 rounded-xl border border-border bg-secondary/25 p-3.5">
              <label className="flex items-start gap-2.5 text-sm font-medium">
                <input
                  type="checkbox"
                  disabled={isImporting}
                  className="mt-0.5 size-4 rounded border-input"
                  {...register("auto_apply_proposal")}
                />
                <span>
                  Apply changes directly to Story Bible &amp; shots
                  <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                    Uncheck to save a versioned screenplay revision and run analyzer diagnostics
                    without modifying active shots or Story Bible.
                  </span>
                </span>
              </label>

              {(() => {
                if (!autoApply) return null;
                if (isCheckingPipeline) {
                  return (
                    <div
                      role="status"
                      data-testid="pipeline-checking-status"
                      className="flex items-center gap-2 rounded-lg border border-muted bg-muted/30 p-2.5 text-xs text-muted-foreground"
                    >
                      <LoaderCircle className="size-3.5 shrink-0 animate-spin" aria-hidden="true" />
                      <span>Verifying existing scenes and shots before applying...</span>
                    </div>
                  );
                }
                if (pipelineCheckError) {
                  return (
                    <div
                      role="note"
                      data-testid="pipeline-check-unverified-warning"
                      className="rounded-lg border border-warning/40 bg-warning/10 p-2.5 text-xs text-warning"
                    >
                      <p className="flex items-center gap-1.5 font-medium">
                        <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
                        Could not verify current pipeline status.
                      </p>
                      <p className="mt-1 text-foreground/80">
                        Active scenes, Story Bible entries, or shots on the server will be replaced
                        if applied. Confirm replacement below if this project was already populated.
                      </p>
                    </div>
                  );
                }
                const hasExistingPipeline = existingEntriesCount > 0 || existingShotsCount > 0;
                if (!hasExistingPipeline) return null;
                const summary =
                  existingEntriesCount > 0 && existingShotsCount > 0
                    ? `Project already has ${existingEntriesCount} Story Bible ${
                        existingEntriesCount === 1 ? "entry" : "entries"
                      } and ${existingShotsCount} ${existingShotsCount === 1 ? "shot" : "shots"}.`
                    : existingEntriesCount > 0
                      ? `Project already has ${existingEntriesCount} Story Bible ${
                          existingEntriesCount === 1 ? "entry" : "entries"
                        }.`
                      : `Project already has ${existingShotsCount} ${
                          existingShotsCount === 1 ? "shot" : "shots"
                        }.`;
                return (
                  <div
                    role="note"
                    data-testid="existing-pipeline-overwrite-warning"
                    className="rounded-lg border border-warning/40 bg-warning/10 p-2.5 text-xs text-warning"
                  >
                    <p className="flex items-center gap-1.5 font-medium">
                      <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
                      {summary}
                    </p>
                    <p className="mt-1 text-foreground/80">
                      Applying this script will replace active scenes, Story Bible locks, and shots.
                    </p>
                  </div>
                );
              })()}

              <div
                className={cn(
                  "rounded-lg p-2 transition-colors",
                  requiresReplaceConfirmation && "border border-destructive/40 bg-destructive/5",
                )}
              >
                <label className="flex items-start gap-2.5 text-sm font-medium">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 rounded border-input"
                    {...fieldProps("replace_existing")}
                    disabled={isImporting || !autoApply}
                    {...register("replace_existing")}
                  />
                  <span>
                    Confirm replacing existing shots and generated takes
                    <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                      Required when re-applying a script to a project that already has active Story
                      Bible entries, shots, or generated takes.
                    </span>
                  </span>
                </label>
                {renderFieldError("replace_existing")}
              </div>
            </div>
          </div>

          <div className="pt-2">
            <Button
              type="submit"
              className="w-full"
              disabled={isImporting || (autoApply && isCheckingPipeline)}
            >
              {isImporting ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : isCheckingPipeline && autoApply ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <FileUp className="size-4" aria-hidden="true" />
              )}
              {isCheckingPipeline && autoApply
                ? "Verifying pipeline..."
                : autoApply
                  ? "Import & apply script"
                  : "Save draft revision & analyze"}
            </Button>
          </div>
        </div>
      </form>
    </section>
  );
}

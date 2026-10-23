import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "@tanstack/react-router";
import {
  CheckCircle2,
  FileUp,
  LayoutGrid,
  LoaderCircle,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEffect, useId } from "react";
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
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "aria-[invalid=true]:border-destructive disabled:cursor-not-allowed disabled:opacity-60",
);

interface ScriptImportPanelProps {
  projectId: string;
  existingEntriesCount?: number;
  existingShotsCount?: number;
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
  isImporting,
  fieldErrors,
  formError,
  requiresReplaceConfirmation,
  result,
  onImport,
  onDismissResult,
}: ScriptImportPanelProps) {
  const headingId = useId();
  const idPrefix = useId();

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

  const submit = handleSubmit((values) => {
    onImport(buildScriptImportRequest(values));
  });

  return (
    <section
      aria-labelledby={headingId}
      data-testid="script-import-panel"
      className="rounded-xl border border-border bg-card p-5 shadow-xs"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id={headingId} className="text-lg font-semibold">
            Script Import &amp; Analyzer
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Parse a MuseFlow, Markdown or plaintext script into scenes, Story Bible locks and
            compiled shots while preserving 100% of the original raw script in a new revision.
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={isImporting}
          onClick={insertSample}
        >
          <Sparkles className="size-3.5" aria-hidden="true" />
          Insert sample script
        </Button>
      </div>

      {formError && (
        <p
          role="alert"
          className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {formError}
        </p>
      )}

      {result && (
        <div
          role="status"
          data-testid="script-import-result"
          className="mt-4 space-y-3 rounded-lg border border-success/30 bg-success/10 p-4 text-sm"
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

          <dl className="grid grid-cols-2 gap-2 rounded-md bg-background/80 p-3 text-xs sm:grid-cols-4">
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
              className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs text-warning"
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
      )}

      <form className="mt-4 space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label htmlFor={fieldId("raw_content")} className="text-sm font-medium">
              Script content
            </label>
            <span className="text-xs text-muted-foreground">
              {rawContent.length.toLocaleString()} /{" "}
              {SCRIPT_IMPORT_LIMITS.maxScriptChars.toLocaleString()} chars
            </span>
          </div>
          <textarea
            rows={8}
            placeholder="Paste SCENE / SHOT blocks or prose paragraphs here..."
            maxLength={SCRIPT_IMPORT_LIMITS.maxScriptChars}
            className={cn(inputClass, "font-mono text-xs leading-relaxed")}
            {...fieldProps("raw_content")}
            {...register("raw_content")}
          />
          {renderFieldError("raw_content")}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
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

          <div className="space-y-2.5 pt-1">
            <label className="flex items-start gap-2 text-sm font-medium">
              <input
                type="checkbox"
                disabled={isImporting}
                className="mt-0.5 size-4 rounded border-input"
                {...register("auto_apply_proposal")}
              />
              <span>
                Apply changes directly to Story Bible &amp; shots
                <span className="block text-xs font-normal text-muted-foreground">
                  Uncheck to save a versioned screenplay revision and run analyzer diagnostics
                  without modifying active shots or Story Bible.
                </span>
              </span>
            </label>

            {(() => {
              const hasExistingPipeline = existingEntriesCount > 0 || existingShotsCount > 0;
              if (!hasExistingPipeline || !autoApply) return null;
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
                  className="rounded-md border border-warning/40 bg-warning/10 p-2.5 text-xs text-warning"
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
                "rounded-md p-2 transition-colors",
                requiresReplaceConfirmation && "border border-destructive/40 bg-destructive/5",
              )}
            >
              <label className="flex items-start gap-2 text-sm font-medium">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 rounded border-input"
                  {...fieldProps("replace_existing")}
                  disabled={isImporting || !autoApply}
                  {...register("replace_existing")}
                />
                <span>
                  Confirm replacing existing shots and generated takes
                  <span className="block text-xs font-normal text-muted-foreground">
                    Required when re-applying a script to a project that already has active Story
                    Bible entries, shots, or generated takes.
                  </span>
                </span>
              </label>
              {renderFieldError("replace_existing")}
            </div>
          </div>
        </div>

        <div className="flex justify-end">
          <Button type="submit" disabled={isImporting}>
            {isImporting ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <FileUp className="size-4" aria-hidden="true" />
            )}
            {autoApply ? "Import & apply script" : "Save draft revision & analyze"}
          </Button>
        </div>
      </form>
    </section>
  );
}

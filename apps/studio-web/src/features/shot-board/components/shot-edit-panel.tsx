import { zodResolver } from "@hookform/resolvers/zod";
import { LoaderCircle, Save, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useMemo } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { ShotLintReport } from "@/features/shot-board/components/shot-lint-report";
import {
  SHOT_APPROVAL_LABELS,
  SHOT_APPROVAL_STATES,
  SHOT_CONTINUITY_LABELS,
  SHOT_CONTINUITY_MODES,
  SHOT_LIMITS,
  buildShotUpdate,
  previousShotCandidates,
  shotApprovalLabel,
  shotEditDefaults,
  shotGenerationLabel,
  shotLabel,
  type ProjectShot,
  type ShotEditField,
  type ShotEditValues,
  type ShotReview,
  type ShotUpdate,
} from "@/features/shot-board/state";
import { PREDECESSOR_ORDER_WARNING, isPredecessorOutOfOrder } from "@/features/shot-board/takes";
import { cn } from "@/lib/utils";

/**
 * Text fields may stay empty only if they were already empty: the backend rejects a blank
 * value when it is sent, and unchanged fields are never sent.
 */
function editableText(original: string, maxChars: number, label: string) {
  return z
    .string()
    .max(maxChars, `${label} must be at most ${maxChars} characters.`)
    .refine(
      (value) => value.trim().length > 0 || original.trim().length === 0,
      `${label} must not be blank.`,
    );
}

function shotEditSchema(shot: ProjectShot) {
  return z.object({
    action: editableText(shot.action, SHOT_LIMITS.actionMaxChars, "Action"),
    duration_seconds: z
      .number({ error: "Enter a duration in seconds." })
      .refine(Number.isFinite, "Enter a duration in seconds.")
      .gt(0, "Duration must be greater than 0 seconds.")
      .max(
        SHOT_LIMITS.maxDurationSeconds,
        `Duration must be at most ${SHOT_LIMITS.maxDurationSeconds} seconds.`,
      ),
    shot_size: editableText(shot.shot_size, SHOT_LIMITS.shotSizeMaxChars, "Shot size"),
    camera_motion: editableText(
      shot.camera_motion,
      SHOT_LIMITS.cameraMotionMaxChars,
      "Camera motion",
    ),
    composition: editableText(shot.composition, SHOT_LIMITS.compositionMaxChars, "Composition"),
    continuity_mode: z.enum(SHOT_CONTINUITY_MODES),
    previous_shot_id: z.string(),
    approval_state: z.enum(SHOT_APPROVAL_STATES),
  }) satisfies z.ZodType<ShotEditValues>;
}

interface ShotEditPanelProps {
  shot: ProjectShot;
  /** All shots of the project, used for the previous-shot choices. */
  shots: readonly ProjectShot[];
  isSaving: boolean;
  /** Server field errors from the last failed save (422/409 mapped to fields). */
  fieldErrors: Partial<Record<ShotEditField, string>>;
  formError: string | null;
  /** Response of the last successful save of this shot. */
  review: ShotReview | null;
  onSave: (update: ShotUpdate) => void;
  onClose: () => void;
}

const inputClass = cn(
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "aria-[invalid=true]:border-destructive disabled:cursor-not-allowed disabled:opacity-60",
);

export function ShotEditPanel({
  shot,
  shots,
  isSaving,
  fieldErrors,
  formError,
  review,
  onSave,
  onClose,
}: ShotEditPanelProps) {
  const schema = useMemo(() => shotEditSchema(shot), [shot]);
  const headingId = useId();
  const idPrefix = useId();
  const form = useForm<ShotEditValues>({
    resolver: zodResolver(schema),
    defaultValues: shotEditDefaults(shot),
    mode: "onSubmit",
  });
  const {
    register,
    handleSubmit,
    setError,
    setFocus,
    watch,
    formState: { errors, isDirty },
  } = form;

  useEffect(() => {
    setFocus("action");
  }, [setFocus]);

  // After a successful save the submitted values become the new baseline.
  const { reset, getValues } = form;
  useEffect(() => {
    if (review) reset(getValues());
  }, [review, reset, getValues]);

  useEffect(() => {
    for (const [field, message] of Object.entries(fieldErrors) as Array<[ShotEditField, string]>) {
      setError(field, { type: "server", message });
    }
  }, [fieldErrors, setError]);

  const candidates = previousShotCandidates(shot, shots);
  const continuityMode = watch("continuity_mode");
  const previousShotId = watch("previous_shot_id");
  const chosenPrevious = previousShotId
    ? shots.find((candidate) => candidate.id === previousShotId)
    : undefined;
  const predecessorOutOfOrder = isPredecessorOutOfOrder(shot, chosenPrevious);
  const continuityChanged =
    continuityMode !== shot.continuity_mode || (previousShotId || null) !== shot.previous_shot_id;

  const fieldId = (field: ShotEditField) => `${idPrefix}-${field}`;
  const errorId = (field: ShotEditField) => `${idPrefix}-${field}-error`;
  const fieldProps = (field: ShotEditField) => ({
    id: fieldId(field),
    "aria-invalid": errors[field] ? true : undefined,
    "aria-describedby": errors[field] ? errorId(field) : undefined,
    disabled: isSaving,
  });
  const fieldError = (field: ShotEditField) =>
    errors[field]?.message ? (
      <p id={errorId(field)} className="text-xs text-destructive">
        {errors[field]?.message}
      </p>
    ) : null;

  const submit = handleSubmit((values) => {
    const update = buildShotUpdate(shot, values);
    if (Object.keys(update).length === 0) return;
    onSave(update);
  });

  return (
    <section
      aria-labelledby={headingId}
      data-testid={`shot-edit-panel-${shot.id}`}
      className="mt-4 rounded-lg border border-primary/40 bg-background/60 p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 id={headingId} className="font-semibold">
            Edit {shotLabel(shot)}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            Only changed fields are sent. Saving recompiles the final prompt from the shot fields.
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onClose}
          aria-label={`Close editor for ${shotLabel(shot)}`}
        >
          <X aria-hidden="true" />
          Close
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

      <form className="mt-4 grid gap-4 md:grid-cols-2" onSubmit={submit} noValidate>
        <div className="space-y-1 md:col-span-2">
          <label htmlFor={fieldId("action")} className="text-sm font-medium">
            Action
          </label>
          <textarea
            rows={3}
            maxLength={SHOT_LIMITS.actionMaxChars}
            className={inputClass}
            {...fieldProps("action")}
            {...register("action")}
          />
          {fieldError("action")}
        </div>

        <div className="space-y-1">
          <label htmlFor={fieldId("duration_seconds")} className="text-sm font-medium">
            Duration (seconds)
          </label>
          <input
            type="number"
            step="0.1"
            min="0.1"
            max={SHOT_LIMITS.maxDurationSeconds}
            inputMode="decimal"
            className={inputClass}
            {...fieldProps("duration_seconds")}
            {...register("duration_seconds", { valueAsNumber: true })}
          />
          {fieldError("duration_seconds")}
        </div>

        <div className="space-y-1">
          <label htmlFor={fieldId("shot_size")} className="text-sm font-medium">
            Shot size
          </label>
          <input
            type="text"
            maxLength={SHOT_LIMITS.shotSizeMaxChars}
            className={inputClass}
            {...fieldProps("shot_size")}
            {...register("shot_size")}
          />
          {fieldError("shot_size")}
        </div>

        <div className="space-y-1">
          <label htmlFor={fieldId("camera_motion")} className="text-sm font-medium">
            Camera motion
          </label>
          <input
            type="text"
            maxLength={SHOT_LIMITS.cameraMotionMaxChars}
            className={inputClass}
            {...fieldProps("camera_motion")}
            {...register("camera_motion")}
          />
          {fieldError("camera_motion")}
        </div>

        <div className="space-y-1">
          <label htmlFor={fieldId("approval_state")} className="text-sm font-medium">
            Approval
          </label>
          <select
            className={inputClass}
            {...fieldProps("approval_state")}
            {...register("approval_state")}
          >
            {SHOT_APPROVAL_STATES.map((state) => (
              <option key={state} value={state}>
                {SHOT_APPROVAL_LABELS[state]}
              </option>
            ))}
          </select>
          {fieldError("approval_state")}
        </div>

        <div className="space-y-1 md:col-span-2">
          <label htmlFor={fieldId("composition")} className="text-sm font-medium">
            Composition
          </label>
          <textarea
            rows={2}
            maxLength={SHOT_LIMITS.compositionMaxChars}
            className={inputClass}
            {...fieldProps("composition")}
            {...register("composition")}
          />
          {fieldError("composition")}
        </div>

        <div className="space-y-1">
          <label htmlFor={fieldId("continuity_mode")} className="text-sm font-medium">
            Continuity mode
          </label>
          <select
            className={inputClass}
            {...fieldProps("continuity_mode")}
            {...register("continuity_mode")}
          >
            {SHOT_CONTINUITY_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {SHOT_CONTINUITY_LABELS[mode]}
              </option>
            ))}
          </select>
          {fieldError("continuity_mode")}
        </div>

        <div className="space-y-1">
          <label htmlFor={fieldId("previous_shot_id")} className="text-sm font-medium">
            Previous shot (same scene)
          </label>
          <select
            className={inputClass}
            {...fieldProps("previous_shot_id")}
            {...register("previous_shot_id")}
          >
            <option value="">No previous shot</option>
            {candidates.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {shotLabel(candidate)}
              </option>
            ))}
            {shot.previous_shot_id &&
              !candidates.some((candidate) => candidate.id === shot.previous_shot_id) && (
                <option value={shot.previous_shot_id}>
                  {shot.previous_shot_id} (not in this scene)
                </option>
              )}
          </select>
          {fieldError("previous_shot_id")}
          {predecessorOutOfOrder && chosenPrevious && (
            <p
              role="note"
              data-testid="edit-predecessor-warning"
              className="flex items-start gap-1 text-xs text-warning"
            >
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              {PREDECESSOR_ORDER_WARNING} ({shotLabel(chosenPrevious)} is #
              {chosenPrevious.sequence_index}; this shot is #{shot.sequence_index}). You can still
              save.
            </p>
          )}
        </div>

        {continuityChanged && shot.first_frame_asset_id && (
          <p
            role="note"
            className="rounded-md border border-warning/30 bg-warning/5 p-2 text-xs text-warning md:col-span-2"
          >
            Changing the continuity mode or previous shot clears this shot&apos;s first-frame asset.
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2 md:col-span-2">
          <Button type="submit" disabled={isSaving || !isDirty}>
            {isSaving ? (
              <LoaderCircle className="animate-spin" aria-hidden="true" />
            ) : (
              <Save aria-hidden="true" />
            )}
            {isSaving ? "Saving..." : "Save shot"}
          </Button>
          <Button type="button" variant="outline" onClick={onClose} disabled={isSaving}>
            Cancel
          </Button>
        </div>
      </form>

      {review && (
        <section
          aria-label="Saved shot review"
          data-testid="shot-review-result"
          className="mt-5 space-y-3 border-t border-border pt-4"
        >
          <p role="status" className="text-sm font-medium text-success">
            Shot saved. Approval: {shotApprovalLabel(review.approval_state)} · Generation:{" "}
            {shotGenerationLabel(review.generation_state)}
          </p>
          <div className="space-y-1">
            <p id={`${idPrefix}-prompt-label`} className="text-sm font-medium">
              Final prompt (compiled from the shot fields, read-only)
            </p>
            <p
              aria-labelledby={`${idPrefix}-prompt-label`}
              data-testid="shot-review-prompt"
              className="whitespace-pre-wrap break-words rounded-md border border-border bg-muted/40 p-3 text-sm"
            >
              {review.prompt_final || "The compiler returned an empty prompt."}
            </p>
            <p className="text-xs text-muted-foreground">
              Prompt hash <code className="break-all">{review.prompt_hash}</code>
            </p>
          </div>
          <ShotLintReport issues={review.lint_report} label="Prompt Linter after save" />
        </section>
      )}
    </section>
  );
}

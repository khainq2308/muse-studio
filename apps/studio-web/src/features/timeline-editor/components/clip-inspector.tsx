import { ArrowLeft, ArrowRight, Info, Scissors, Trash2 } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { ConfirmActionDialog } from "@/components/shared/confirm-action-dialog";
import { Button } from "@/components/ui/button";
import {
  MAX_CLIP_TRANSITION_SECONDS,
  MAX_TRIM_SECONDS,
  type ClipFieldErrors,
  type ClipLayout,
  type ClipPatch,
  type DraftClip,
} from "@/features/timeline-editor/clip-draft";
import { formatTimecode } from "@/features/timeline-editor/timecode";
import { cn } from "@/lib/utils";

function formatSeconds(value: number): string {
  return `${Number(value.toFixed(2))}s`;
}

/** Empty input becomes NaN so validation can flag it instead of silently saving 0. */
function parseSeconds(raw: string): number {
  return raw.trim() === "" ? Number.NaN : Number(raw);
}

function inputValue(value: number): number | "" {
  return Number.isFinite(value) ? value : "";
}

interface NumberFieldProps {
  label: string;
  value: number;
  min: number;
  max?: number;
  disabled?: boolean;
  error?: string;
  hint?: ReactNode;
  onChange: (value: number) => void;
}

function NumberField({
  label,
  value,
  min,
  max,
  disabled,
  error,
  hint,
  onChange,
}: NumberFieldProps) {
  const id = useId();
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="space-y-1 text-xs">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      <input
        id={id}
        type="number"
        inputMode="decimal"
        step="0.1"
        min={min}
        max={max}
        disabled={disabled}
        value={inputValue(value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(event) => onChange(parseSeconds(event.target.value))}
        className={cn(
          "h-8 w-full rounded-lg border bg-background px-2.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
          error ? "border-destructive" : "border-input",
        )}
      />
      {hint && (
        <p id={`${id}-hint`} className="text-[11px] text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-[11px] text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

interface ClipInspectorProps {
  clip: DraftClip;
  index: number;
  count: number;
  layout: ClipLayout | undefined;
  errors: ClipFieldErrors;
  /** A 422 from the last save that the server attributed to this clip. */
  serverError: string | null;
  fps: number | undefined;
  disabled?: boolean;
  onPatch: (patch: ClipPatch) => void;
  onMove: (to: number) => void;
  onRemove: () => void;
}

/** Editable details of the selected draft clip: trims, transition, gap, order and removal. */
export function ClipInspector({
  clip,
  index,
  count,
  layout,
  errors,
  serverError,
  fps,
  disabled = false,
  onPatch,
  onMove,
  onRemove,
}: ClipInspectorProps) {
  const transitionId = useId();
  const [confirmingRemoveKey, setConfirmingRemoveKey] = useState<string | null>(null);
  const isFirst = index === 0;
  const isConfirmingRemove = confirmingRemoveKey === clip.key;
  const effective = layout?.effectiveSeconds ?? 0;

  return (
    <div className="space-y-3">
      <dl className="space-y-1.5 text-xs">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Clip</dt>
          <dd className="font-mono font-semibold">
            #{index + 1} of {count}
            {clip.savedPosition !== index + 1 ? ` (saved as #${clip.savedPosition})` : ""}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Shot</dt>
          <dd className="min-w-0 truncate font-mono">{clip.shotId ?? "—"}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Take</dt>
          <dd className="min-w-0 truncate font-mono">{clip.takeId ?? "unassigned"}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Starts at</dt>
          <dd className="font-mono">{formatTimecode(layout?.startSeconds ?? 0, fps)}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Effective duration</dt>
          <dd className="font-mono" data-testid="clip-effective-duration">
            {formatSeconds(effective)} of {formatSeconds(clip.sourceDurationSeconds)} source
          </dd>
        </div>
      </dl>

      {errors.source && <p className="text-[11px] text-destructive">{errors.source}</p>}

      <fieldset className="space-y-2 border-t border-border pt-2.5" disabled={disabled}>
        <legend className="flex items-center gap-1.5 text-xs font-semibold">
          <Scissors className="size-3.5 text-primary" aria-hidden="true" />
          Trim
        </legend>
        <div className="grid grid-cols-2 gap-2">
          <NumberField
            label="Trim start (s)"
            value={clip.trimStartSeconds}
            min={0}
            max={MAX_TRIM_SECONDS}
            error={errors.trimStart}
            onChange={(value) => onPatch({ trimStartSeconds: value })}
          />
          <NumberField
            label="Trim end (s)"
            value={clip.trimEndSeconds}
            min={0}
            max={MAX_TRIM_SECONDS}
            error={errors.trimEnd}
            onChange={(value) => onPatch({ trimEndSeconds: value })}
          />
        </div>
        {errors.trims && (
          <p role="alert" className="text-[11px] text-destructive">
            {errors.trims}
          </p>
        )}
      </fieldset>

      <fieldset className="space-y-2 border-t border-border pt-2.5" disabled={disabled}>
        <legend className="text-xs font-semibold">Transition and placement</legend>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1 text-xs">
            <label htmlFor={transitionId} className="font-medium">
              Transition in
            </label>
            <select
              id={transitionId}
              value={isFirst ? "hard_cut" : clip.transitionType}
              disabled={isFirst}
              onChange={(event) =>
                onPatch({
                  transitionType:
                    event.target.value === "cross_dissolve" ? "cross_dissolve" : "hard_cut",
                })
              }
              className="h-8 w-full rounded-lg border border-input bg-background px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            >
              <option value="hard_cut">Hard cut</option>
              <option value="cross_dissolve">Cross dissolve</option>
            </select>
          </div>
          <NumberField
            label="Dissolve (s)"
            value={
              isFirst || clip.transitionType === "hard_cut" ? 0 : clip.transitionDurationSeconds
            }
            min={0}
            max={MAX_CLIP_TRANSITION_SECONDS}
            disabled={isFirst || clip.transitionType === "hard_cut"}
            error={isFirst ? undefined : errors.transition}
            onChange={(value) => onPatch({ transitionDurationSeconds: value })}
          />
        </div>
        {isFirst && (
          <p className="text-[11px] text-muted-foreground">
            The first clip always opens with a hard cut.
          </p>
        )}
        <NumberField
          label="Gap before clip (s)"
          value={clip.gapBeforeSeconds}
          min={0}
          error={errors.gap}
          hint={
            isFirst
              ? "Black lead-in before the first clip. 0 starts the sequence at 00:00:00."
              : "Black gap after the previous clip ends. 0 places the clip right after it."
          }
          onChange={(value) => onPatch({ gapBeforeSeconds: value })}
        />
      </fieldset>

      {serverError && (
        <p role="alert" className="text-[11px] text-destructive">
          Server rejected this clip: {serverError}
        </p>
      )}

      <div className="flex flex-wrap gap-2 border-t border-border pt-2.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || isFirst}
          onClick={() => onMove(index - 1)}
        >
          <ArrowLeft aria-hidden="true" />
          Move left
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || index >= count - 1}
          onClick={() => onMove(index + 1)}
        >
          <ArrowRight aria-hidden="true" />
          Move right
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="text-destructive"
          disabled={disabled || isConfirmingRemove}
          onClick={() => setConfirmingRemoveKey(clip.key)}
        >
          <Trash2 aria-hidden="true" />
          Remove from sequence
        </Button>
      </div>

      {isConfirmingRemove && (
        <ConfirmActionDialog
          title={`Remove clip #${index + 1} from the draft?`}
          confirmLabel="Remove clip"
          destructive
          onConfirm={() => {
            setConfirmingRemoveKey(null);
            onRemove();
          }}
          onCancel={() => setConfirmingRemoveKey(null)}
        >
          <p>
            Shot {clip.shotId ?? "custom"} is only removed from the timeline when you save clip
            edits. “Discard changes” restores every saved clip. The shot and its takes are not
            deleted.
          </p>
        </ConfirmActionDialog>
      )}

      <p className="flex items-start gap-1.5 border-t border-border pt-2.5 text-[11px] text-muted-foreground">
        <Info className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
        Moving a clip places it right after its new neighbour; a gap you typed in this draft moves
        with it. No in-browser preview: export and download the verified MP4 to review the cut.
      </p>
    </div>
  );
}

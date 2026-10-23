import { Flag, LoaderCircle, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { isMockConnection, type Connection } from "@/features/shot-board/state";
import {
  buildContinuityFlagRequest,
  takeLabel,
  type ContinuityFlagRequest,
  type ShotTake,
} from "@/features/shot-board/takes";
import { cn } from "@/lib/utils";

interface ContinuityFlagFormProps {
  take: ShotTake;
  shotLabel: string;
  /** Connection chosen in Generation settings; required for "Regenerate now". */
  connection: Connection | undefined;
  modelId: string;
  /** Why regeneration cannot be requested (no connection/model), or null when it can. */
  regenerateDisabledReason: string | null;
  isSubmitting: boolean;
  /** Inline error from the last failed request (400/404/409/422/auth). */
  errorMessage: string | null;
  onSubmit: (request: ContinuityFlagRequest) => void;
  onCancel: () => void;
}

const controlClass = cn(
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "aria-[invalid=true]:border-destructive disabled:cursor-not-allowed disabled:opacity-60",
);

/**
 * Flag a continuity problem on a take with director notes. "Regenerate now" starts a new video
 * generation with the Generation settings connection/model; it may consume provider quota, so
 * it needs a separate confirmation step.
 */
export function ContinuityFlagForm({
  take,
  shotLabel,
  connection,
  modelId,
  regenerateDisabledReason,
  isSubmitting,
  errorMessage,
  onSubmit,
  onCancel,
}: ContinuityFlagFormProps) {
  const idPrefix = useId();
  const [notes, setNotes] = useState(take.director_notes);
  const [regenerate, setRegenerate] = useState(false);
  const [notesError, setNotesError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const notesRef = useRef<HTMLTextAreaElement | null>(null);
  const confirmCancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => notesRef.current?.focus(), []);
  useEffect(() => {
    if (confirming) confirmCancelRef.current?.focus();
  }, [confirming]);

  const canRegenerate = !regenerateDisabledReason && Boolean(connection);
  const wantsRegenerate = regenerate && canRegenerate;

  const request = (): ContinuityFlagRequest =>
    buildContinuityFlagRequest({
      take,
      flagged: true,
      directorNotes: notes,
      regenerate:
        wantsRegenerate && connection ? { connectionId: connection.id, modelId } : undefined,
    });

  const submit = () => {
    if (!notes.trim()) {
      setNotesError("Describe the continuity problem for the director notes.");
      notesRef.current?.focus();
      return;
    }
    setNotesError(null);
    if (wantsRegenerate) {
      setConfirming(true);
      return;
    }
    onSubmit(request());
  };

  const label = takeLabel(take);
  const notesId = `${idPrefix}-notes`;

  return (
    <section
      aria-labelledby={`${idPrefix}-title`}
      data-testid={`continuity-flag-form-${take.id}`}
      className="mt-3 space-y-3 rounded-lg border border-warning/40 bg-warning/5 p-4"
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        if (confirming) setConfirming(false);
        else onCancel();
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <h5 id={`${idPrefix}-title`} className="font-semibold">
          Flag continuity on {label}
        </h5>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={isSubmitting}
          aria-label={`Close continuity flag for ${label}`}
        >
          <X aria-hidden="true" />
        </Button>
      </div>

      {errorMessage && (
        <p
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {errorMessage}
        </p>
      )}

      <form
        noValidate
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <div className="space-y-1">
          <label htmlFor={notesId} className="text-sm font-medium">
            Director notes
          </label>
          <textarea
            id={notesId}
            ref={notesRef}
            rows={3}
            value={notes}
            disabled={isSubmitting || confirming}
            aria-invalid={notesError ? true : undefined}
            aria-describedby={notesError ? `${notesId}-error` : undefined}
            onChange={(event) => setNotes(event.target.value)}
            className={controlClass}
            placeholder="What breaks continuity in this take?"
          />
          {notesError && (
            <p id={`${notesId}-error`} className="text-xs text-destructive">
              {notesError}
            </p>
          )}
        </div>

        <div className="space-y-1">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={wantsRegenerate}
              disabled={!canRegenerate || isSubmitting || confirming}
              onChange={(event) => setRegenerate(event.target.checked)}
              className="size-4 accent-primary"
            />
            Regenerate this shot now
          </label>
          <p className="text-xs text-muted-foreground">
            {regenerateDisabledReason ??
              "Starts a new video generation with the connection and model from Generation settings."}
          </p>
        </div>

        {!confirming && (
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={isSubmitting}>
              {isSubmitting ? (
                <LoaderCircle className="animate-spin" aria-hidden="true" />
              ) : (
                <Flag aria-hidden="true" />
              )}
              {isSubmitting
                ? "Saving..."
                : wantsRegenerate
                  ? "Flag and regenerate"
                  : "Flag continuity"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onCancel}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
          </div>
        )}
      </form>

      {confirming && connection && (
        <div
          role="alertdialog"
          aria-labelledby={`${idPrefix}-confirm-title`}
          aria-describedby={`${idPrefix}-confirm-body`}
          className="space-y-3 rounded-lg border border-warning/40 bg-background p-4"
        >
          <p id={`${idPrefix}-confirm-title`} className="font-semibold">
            Flag {label} and regenerate {shotLabel}?
          </p>
          <div id={`${idPrefix}-confirm-body`} className="space-y-1 text-sm">
            <p>
              Connection: <span className="font-medium">{connection.name}</span> ·{" "}
              {isMockConnection(connection)
                ? "Mock provider (no provider cost)"
                : `Live provider (${connection.provider_type}): may consume quota`}
            </p>
            <p>
              Model: <span className="break-all font-medium">{modelId.trim()}</span> · 1 shot ·
              video
            </p>
            <p className="text-xs text-muted-foreground">
              Regeneration always starts a new take, even if an identical request ran before.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              disabled={isSubmitting}
              onClick={() => {
                setConfirming(false);
                onSubmit(request());
              }}
            >
              Confirm and regenerate
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              ref={confirmCancelRef}
              onClick={() => setConfirming(false)}
            >
              Back
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

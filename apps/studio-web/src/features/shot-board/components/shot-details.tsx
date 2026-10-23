import { Link2 } from "lucide-react";
import type { ReactNode } from "react";
import { ShotLintReport } from "@/features/shot-board/components/shot-lint-report";
import {
  shotApprovalLabel,
  shotContinuityLabel,
  shotGenerationLabel,
  shotLabel,
  type ProjectShot,
} from "@/features/shot-board/state";
import { MISSING, formatSeconds, textOrMissing } from "@/features/shot-board/workspace";
import { cn } from "@/lib/utils";

function Field({
  label,
  children,
  wide = false,
  testId,
}: {
  label: string;
  children: ReactNode;
  wide?: boolean;
  testId?: string;
}) {
  return (
    <div
      className={cn(
        "min-w-0 rounded-lg border border-border bg-background px-3 py-2",
        wide && "sm:col-span-2 xl:col-span-3",
      )}
    >
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd
        data-testid={testId}
        className="mt-0.5 whitespace-pre-wrap break-words text-sm text-foreground"
      >
        {children}
      </dd>
    </div>
  );
}

function shotName(shotId: string | null, shots: readonly ProjectShot[]): ReactNode {
  if (!shotId) return MISSING;
  const shot = shots.find((candidate) => candidate.id === shotId);
  return (
    <span className="inline-flex items-center gap-1 break-all">
      <Link2 className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
      {shot ? shotLabel(shot) : shotId}
    </span>
  );
}

function assetId(value: string | null): ReactNode {
  return value ? <code className="break-all text-xs">{value}</code> : MISSING;
}

/** Read-only shot fields; missing values render as a dash, never as invented defaults. */
export function ShotDetails({
  shot,
  shots,
}: {
  shot: ProjectShot;
  /** All shots of the project, to name the previous/next shot. */
  shots: readonly ProjectShot[];
}) {
  return (
    <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3" data-testid="shot-details">
      <Field label="Action & subjects" wide>
        {shot.subject_codes.length > 0 && (
          <span className="mr-1.5 inline-flex flex-wrap gap-1 align-middle">
            {shot.subject_codes.map((code) => (
              <span
                key={code}
                className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-primary"
              >
                @{code}
              </span>
            ))}
          </span>
        )}
        {textOrMissing(shot.action)}
      </Field>
      <Field label="Location">{textOrMissing(shot.location_code)}</Field>
      <Field label="Shot size">{textOrMissing(shot.shot_size)}</Field>
      <Field label="Camera motion">{textOrMissing(shot.camera_motion)}</Field>
      <Field label="Duration">{formatSeconds(shot.duration_seconds)}</Field>
      <Field label="Approval">{shotApprovalLabel(shot.approval_state)}</Field>
      <Field label="Generation">{shotGenerationLabel(shot.generation_state)}</Field>
      <Field label="Composition" wide>
        {textOrMissing(shot.composition)}
      </Field>
      <Field label="Dialogue" wide testId="shot-dialogue">
        {textOrMissing(shot.dialogue)}
      </Field>
      <Field label="Continuity">{shotContinuityLabel(shot.continuity_mode)}</Field>
      <Field label="Previous shot">{shotName(shot.previous_shot_id, shots)}</Field>
      <Field label="Next shot">{shotName(shot.next_shot_id, shots)}</Field>
      <Field label="First frame asset" testId="shot-first-frame">
        {assetId(shot.first_frame_asset_id)}
      </Field>
      <Field label="Last frame asset">{assetId(shot.last_frame_asset_id)}</Field>
      <Field label="Selected take">{assetId(shot.selected_take_id)}</Field>
      <Field label="Reference lock">
        {shot.requires_reference_lock ? "Required" : "Not required"}
      </Field>
      <Field label="Reference assets">
        {shot.reference_asset_ids.length > 0 ? (
          <span className="flex flex-wrap gap-1">
            {shot.reference_asset_ids.map((id) => (
              <code key={id} className="break-all text-xs">
                {id}
              </code>
            ))}
          </span>
        ) : (
          MISSING
        )}
      </Field>
    </dl>
  );
}

/** Compiled prompt (read-only) and the Prompt Linter report of the stored shot. */
export function ShotPromptLint({ shot }: { shot: ProjectShot }) {
  const label = shotLabel(shot);
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <p className="text-sm font-medium">
          Final prompt (compiled from the shot fields, read-only)
        </p>
        <p
          data-testid="shot-prompt-final"
          className="whitespace-pre-wrap break-words rounded-md border border-border bg-muted/40 p-3 text-sm"
        >
          {textOrMissing(shot.prompt_final)}
        </p>
        <p className="text-xs text-muted-foreground">
          Prompt hash{" "}
          <code className="break-all">{shot.prompt_hash ? shot.prompt_hash : MISSING}</code>
        </p>
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium">Negative prompt</p>
        <p
          data-testid="shot-negative-prompt"
          className="whitespace-pre-wrap break-words rounded-md border border-border bg-muted/40 p-3 text-sm"
        >
          {textOrMissing(shot.negative_prompt)}
        </p>
      </div>
      <div className="space-y-2">
        <p className="text-sm font-medium">Prompt Linter</p>
        <ShotLintReport issues={shot.lint_report} label={`Prompt Linter for ${label}`} />
      </div>
    </div>
  );
}

import { Clapperboard, ImageIcon, ImageOff, Link2, Pencil, Play } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/features/shot-board/components/shot-badges";
import { ShotLintReport } from "@/features/shot-board/components/shot-lint-report";
import {
  groupShotsByScene,
  shotApprovalLabel,
  shotApprovalTone,
  shotContinuityLabel,
  shotGenerationLabel,
  shotGenerationTone,
  shotLabel,
  shotLintBlockers,
  shotLintWarnings,
  type ProjectShot,
} from "@/features/shot-board/state";
import { cn } from "@/lib/utils";

interface ShotListProps {
  shots: readonly ProjectShot[];
  /** Shot whose edit panel is open, if any. */
  editingShotId: string | null;
  onEdit: (shotId: string) => void;
  onGenerate: (shotId: string) => void;
  /** Whether single-shot generation can be started (connection + model chosen). */
  canGenerate: boolean;
  generateDisabledReason: string | null;
  /** Slot rendered inside a shot card: the edit panel, generation confirmation or result. */
  renderShotDetail: (shot: ProjectShot) => ReactNode;
}

export function ShotList({
  shots,
  editingShotId,
  onEdit,
  onGenerate,
  canGenerate,
  generateDisabledReason,
  renderShotDetail,
}: ShotListProps) {
  const groups = groupShotsByScene(shots);
  const shotsById = new Map(shots.map((shot) => [shot.id, shot]));

  return (
    <div className="space-y-6">
      {groups.map((group) => (
        <section
          key={group.sceneId}
          aria-label={`Scene ${group.sceneId}`}
          data-testid={`shot-scene-${group.sceneId}`}
          className="space-y-3"
        >
          <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            <Clapperboard className="size-4" aria-hidden="true" />
            Scene <span className="break-all normal-case">{group.sceneId}</span>
            <span className="font-normal normal-case">
              · {group.shots.length} {group.shots.length === 1 ? "shot" : "shots"}
            </span>
          </h3>
          {group.shots.map((shot) => (
            <ShotCard
              key={shot.id}
              shot={shot}
              previousShot={
                shot.previous_shot_id ? shotsById.get(shot.previous_shot_id) : undefined
              }
              isEditing={editingShotId === shot.id}
              onEdit={() => onEdit(shot.id)}
              onGenerate={() => onGenerate(shot.id)}
              canGenerate={canGenerate}
              generateDisabledReason={generateDisabledReason}
            >
              {renderShotDetail(shot)}
            </ShotCard>
          ))}
        </section>
      ))}
    </div>
  );
}

interface ShotCardProps {
  shot: ProjectShot;
  previousShot: ProjectShot | undefined;
  isEditing: boolean;
  onEdit: () => void;
  onGenerate: () => void;
  canGenerate: boolean;
  generateDisabledReason: string | null;
  children: ReactNode;
}

function ShotCard({
  shot,
  previousShot,
  isEditing,
  onEdit,
  onGenerate,
  canGenerate,
  generateDisabledReason,
  children,
}: ShotCardProps) {
  const label = shotLabel(shot);
  const blockers = shotLintBlockers(shot.lint_report);
  const warnings = shotLintWarnings(shot.lint_report);
  const hasFirstFrame = Boolean(shot.first_frame_asset_id);

  return (
    <article
      data-testid={`shot-card-${shot.id}`}
      aria-label={label}
      className={cn(
        "rounded-xl border bg-card p-4 shadow-sm sm:p-5",
        isEditing
          ? "border-primary/60"
          : blockers.length > 0
            ? "border-destructive/40"
            : "border-border",
      )}
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 space-y-2">
          <h4 className="break-words font-semibold">{label}</h4>
          <div className="flex flex-wrap items-center gap-2">
            <StateBadge
              tone={shotGenerationTone(shot.generation_state)}
              testId="shot-generation-state"
            >
              {shotGenerationLabel(shot.generation_state)}
            </StateBadge>
            <StateBadge tone={shotApprovalTone(shot.approval_state)} testId="shot-approval-state">
              {shotApprovalLabel(shot.approval_state)}
            </StateBadge>
            <StateBadge
              tone={
                blockers.length > 0
                  ? "border border-destructive/30 bg-destructive/10 text-destructive"
                  : warnings.length > 0
                    ? "border border-warning/30 bg-warning/10 text-warning"
                    : "border border-success/30 bg-success/10 text-success"
              }
              testId="shot-lint-status"
            >
              {blockers.length > 0
                ? `${blockers.length} lint ${blockers.length === 1 ? "blocker" : "blockers"}`
                : warnings.length > 0
                  ? `${warnings.length} lint ${warnings.length === 1 ? "warning" : "warnings"}`
                  : "Lint clear"}
            </StateBadge>
            <StateBadge
              tone="border border-border bg-muted text-muted-foreground"
              testId="shot-first-frame"
              title={shot.first_frame_asset_id ?? undefined}
            >
              {hasFirstFrame ? (
                <ImageIcon className="size-3.5" aria-hidden="true" />
              ) : (
                <ImageOff className="size-3.5" aria-hidden="true" />
              )}
              {hasFirstFrame ? "First frame set" : "No first frame"}
            </StateBadge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onEdit}
            disabled={isEditing}
            aria-label={`Edit ${label}`}
          >
            <Pencil aria-hidden="true" />
            Edit
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onGenerate}
            disabled={!canGenerate}
            title={generateDisabledReason ?? undefined}
            aria-label={`Generate ${label}`}
            aria-haspopup="dialog"
          >
            <Play aria-hidden="true" />
            Generate
          </Button>
          {/* Take review, A/B compare, select take and continuity flags ship in Shot Board part B. */}
          <span data-testid="shot-takes-placeholder" className="text-xs text-muted-foreground">
            Take review: coming soon
          </span>
        </div>
      </div>

      <p className="mt-3 whitespace-pre-wrap break-words text-sm">
        {shot.action || <span className="text-muted-foreground">No action described.</span>}
      </p>

      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">Duration</dt>
          <dd className="font-medium tabular-nums">{shot.duration_seconds} s</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Shot size / camera</dt>
          <dd className="break-words font-medium">
            {shot.shot_size || "—"} · {shot.camera_motion || "—"}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Continuity</dt>
          <dd className="break-words font-medium">{shotContinuityLabel(shot.continuity_mode)}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Previous shot</dt>
          <dd className="flex items-center gap-1 break-all font-medium">
            {shot.previous_shot_id ? (
              <>
                <Link2 className="size-3.5 shrink-0" aria-hidden="true" />
                {previousShot ? shotLabel(previousShot) : shot.previous_shot_id}
              </>
            ) : (
              "None"
            )}
          </dd>
        </div>
        {shot.composition && (
          <div className="min-w-0 sm:col-span-2 lg:col-span-4">
            <dt className="text-xs text-muted-foreground">Composition</dt>
            <dd className="whitespace-pre-wrap break-words">{shot.composition}</dd>
          </div>
        )}
      </dl>

      {shot.lint_report.length > 0 && (
        <div className="mt-4">
          <ShotLintReport issues={shot.lint_report} label={`Prompt Linter for ${label}`} />
        </div>
      )}

      {children}
    </article>
  );
}

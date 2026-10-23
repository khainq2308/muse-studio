import {
  Camera,
  ChevronUp,
  Clapperboard,
  Film,
  ImageIcon,
  ImageOff,
  Link2,
  MapPin,
  Pencil,
  Plus,
  Sparkles,
  TriangleAlert,
  Volume2,
} from "lucide-react";
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
import { PREDECESSOR_ORDER_WARNING, isPredecessorOutOfOrder } from "@/features/shot-board/takes";
import { cn } from "@/lib/utils";

interface ShotListProps {
  shots: readonly ProjectShot[];
  /** Shot whose edit panel is open, if any. */
  editingShotId: string | null;
  onEdit: (shotId: string) => void;
  onGenerate: (shotId: string) => void;
  /** Shot whose take review is open, if any. */
  reviewingShotId: string | null;
  onReviewTakes: (shotId: string) => void;
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
  reviewingShotId,
  onReviewTakes,
  canGenerate,
  generateDisabledReason,
  renderShotDetail,
}: ShotListProps) {
  const groups = groupShotsByScene(shots);
  const shotsById = new Map(shots.map((shot) => [shot.id, shot]));

  return (
    <div className="space-y-6">
      {groups.map((group) => {
        const primaryLocation =
          group.shots.find((shot) => shot.location_code)?.location_code ?? "Main Stage";
        const hasFrameChain = group.shots.some((shot) => shot.continuity_mode === "frame_chain");
        const primaryCamera =
          group.shots.find((shot) => shot.camera_motion)?.camera_motion ?? "Cinematic";
        const primaryDialogue =
          group.shots.find((shot) => shot.dialogue?.trim())?.dialogue ??
          group.shots[0]?.action ??
          "Visual sequence beat";
        const totalSceneDuration = group.shots.reduce(
          (sum, shot) => sum + (Number.isFinite(shot.duration_seconds) ? shot.duration_seconds : 0),
          0,
        );

        return (
          <section
            key={group.sceneId}
            aria-label={`Scene ${group.sceneId}`}
            data-testid={`shot-scene-${group.sceneId}`}
            className="rounded-2xl border border-border bg-secondary/35 p-4 shadow-2xs sm:p-5"
          >
            <div className="flex flex-col gap-5 xl:flex-row xl:items-start">
              {/* Left Scene Sidebar (LTX Studio Scene Setting & Sound column) */}
              <aside className="w-full shrink-0 space-y-3 xl:w-64">
                <h3 className="flex items-center gap-2 text-base font-bold tracking-tight text-foreground">
                  <Clapperboard className="size-4 text-primary" aria-hidden="true" />
                  <span>
                    Scene <span className="break-all">{group.sceneId}</span>
                  </span>
                  <span className="ml-auto rounded-full bg-card px-2.5 py-0.5 text-xs font-medium text-muted-foreground shadow-2xs">
                    {group.shots.length} {group.shots.length === 1 ? "shot" : "shots"}
                  </span>
                </h3>

                {/* Scene Setting Card */}
                <div className="space-y-3 rounded-xl border border-border bg-card p-3.5 shadow-2xs">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-foreground">Scene setting</span>
                    <ChevronUp className="size-3.5 text-muted-foreground" aria-hidden="true" />
                  </div>

                  <div className="space-y-2.5">
                    <div className="space-y-1">
                      <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                        Location
                      </p>
                      <div className="flex items-center gap-1.5 rounded-lg border border-border bg-secondary/40 px-2.5 py-1.5 text-xs font-medium text-foreground">
                        <MapPin className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
                        <span className="truncate">{primaryLocation}</span>
                      </div>
                    </div>

                    <div className="space-y-1">
                      <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                        Continuity &amp; Lighting
                      </p>
                      <div className="rounded-lg border border-border bg-secondary/40 px-2.5 py-1.5 text-xs font-medium text-foreground">
                        {hasFrameChain ? "Frame-chained sequence" : "Standard scene cut"}
                      </div>
                    </div>

                    <div className="space-y-1">
                      <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                        Camera Style
                      </p>
                      <div className="rounded-lg border border-border bg-secondary/40 px-2.5 py-1.5 text-xs font-medium text-foreground">
                        {primaryCamera}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Sound & Timing Card */}
                <div className="space-y-3 rounded-xl border border-border bg-card p-3.5 shadow-2xs">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-foreground">Sound &amp; Timing</span>
                    <ChevronUp className="size-3.5 text-muted-foreground" aria-hidden="true" />
                  </div>

                  <div className="flex items-start gap-2 rounded-lg border border-border bg-secondary/40 p-2.5">
                    <Volume2 className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                        Voiceover / Action Beat
                      </p>
                      <p className="mt-0.5 line-clamp-2 text-xs text-foreground">
                        {primaryDialogue}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-2.5 py-1.5 text-xs">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                      Scene Duration
                    </span>
                    <span className="font-mono font-semibold text-foreground">
                      {totalSceneDuration}s
                    </span>
                  </div>
                </div>
              </aside>

              {/* Right Storyboard Columns Grid (LTX Studio Shot Cards) */}
              <div className="grid min-w-0 flex-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
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
                    isReviewing={reviewingShotId === shot.id}
                    onReviewTakes={() => onReviewTakes(shot.id)}
                    canGenerate={canGenerate}
                    generateDisabledReason={generateDisabledReason}
                  >
                    {renderShotDetail(shot)}
                  </ShotCard>
                ))}
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}

interface ShotCardProps {
  shot: ProjectShot;
  previousShot: ProjectShot | undefined;
  isEditing: boolean;
  onEdit: () => void;
  onGenerate: () => void;
  isReviewing: boolean;
  onReviewTakes: () => void;
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
  isReviewing,
  onReviewTakes,
  canGenerate,
  generateDisabledReason,
  children,
}: ShotCardProps) {
  const label = shotLabel(shot);
  const blockers = shotLintBlockers(shot.lint_report);
  const warnings = shotLintWarnings(shot.lint_report);
  const hasFirstFrame = Boolean(shot.first_frame_asset_id);
  const isExpanded = isEditing || isReviewing;
  const motionPercent = Math.min(100, Math.max(12, Math.round((shot.duration_seconds / 10) * 100)));

  return (
    <article
      data-testid={`shot-card-${shot.id}`}
      aria-label={label}
      className={cn(
        "flex flex-col justify-between rounded-2xl border bg-card p-3.5 shadow-xs transition-all sm:p-4",
        isExpanded && "md:col-span-2 2xl:col-span-3",
        isEditing || isReviewing
          ? "border-primary ring-2 ring-primary/25"
          : blockers.length > 0
            ? "border-destructive/50"
            : "border-border hover:border-primary/40",
      )}
    >
      <div className="space-y-3">
        {/* LTX Studio 16:9 Storyboard Frame Header */}
        <div className="relative overflow-hidden rounded-xl border border-slate-800 bg-gradient-to-br from-slate-950 via-slate-900 to-indigo-950 p-3 text-slate-100">
          <div
            className="pointer-events-none absolute inset-0 opacity-20"
            style={{
              backgroundImage:
                "linear-gradient(to right, rgba(255,255,255,0.08) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.08) 1px, transparent 1px)",
              backgroundSize: "32px 32px",
            }}
            aria-hidden="true"
          />

          {/* Top Floating Number Pill & State Badges */}
          <div className="relative flex items-start justify-between gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900/85 px-2.5 py-1 font-mono text-xs font-bold text-white shadow-xs backdrop-blur-md">
              <span className="text-indigo-400">⋮⋮</span> {shot.sequence_index}
            </span>

            <div className="flex flex-wrap justify-end gap-1.5">
              <StateBadge
                tone={shotGenerationTone(shot.generation_state)}
                testId="shot-generation-state"
              >
                {shotGenerationLabel(shot.generation_state)}
              </StateBadge>
              <StateBadge tone={shotApprovalTone(shot.approval_state)} testId="shot-approval-state">
                {shotApprovalLabel(shot.approval_state)}
              </StateBadge>
            </div>
          </div>

          {/* Center Storyboard Visual Frame Title */}
          <div className="relative my-2.5 flex flex-col items-center justify-center text-center">
            <div className="flex size-8 items-center justify-center rounded-full border border-white/15 bg-white/10 text-indigo-300 backdrop-blur-xs">
              <Camera className="size-4" aria-hidden="true" />
            </div>
            <h4 className="mt-1.5 line-clamp-1 text-sm font-semibold text-white">{label}</h4>
            <p className="mt-0.5 font-mono text-[11px] text-slate-300">
              {shot.shot_size || "medium"} · {shot.camera_motion || "static"} ·{" "}
              {shot.duration_seconds}s
            </p>
          </div>

          {/* Bottom Frame Overlay: Lint & First Frame Status */}
          <div className="relative flex flex-wrap items-center justify-between gap-1.5 pt-1">
            <StateBadge
              tone={
                blockers.length > 0
                  ? "border border-destructive/40 bg-destructive/20 text-red-200"
                  : warnings.length > 0
                    ? "border border-warning/40 bg-warning/20 text-amber-200"
                    : "border border-emerald-400/30 bg-emerald-500/20 text-emerald-200"
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
              tone="border border-white/15 bg-slate-900/80 text-slate-200"
              testId="shot-first-frame"
              title={shot.first_frame_asset_id ?? undefined}
            >
              {hasFirstFrame ? (
                <ImageIcon className="size-3.5 text-indigo-300" aria-hidden="true" />
              ) : (
                <ImageOff className="size-3.5 text-slate-400" aria-hidden="true" />
              )}
              {hasFirstFrame ? "First frame set" : "No first frame"}
            </StateBadge>
          </div>
        </div>

        {/* LTX Studio Action Button Strip right below thumbnail */}
        <div className="grid grid-cols-3 gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onGenerate}
            disabled={!canGenerate}
            title={generateDisabledReason ?? undefined}
            aria-label={`Generate ${label}`}
            aria-haspopup="dialog"
            className="border-primary/25 bg-primary/10 text-primary hover:bg-primary/20 hover:text-primary"
          >
            <Sparkles className="size-3.5" aria-hidden="true" />
            Generate
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onEdit}
            disabled={isEditing}
            aria-label={`Edit ${label}`}
          >
            <Pencil className="size-3.5" aria-hidden="true" />
            Edit
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onReviewTakes}
            aria-expanded={isReviewing}
            aria-label={`Review takes for ${label}`}
          >
            <Film className="size-3.5" aria-hidden="true" />
            Review takes
          </Button>
        </div>

        {children}

        {/* LTX Studio PROMPT Box with @Entity pill highlights */}
        <div className="space-y-2 rounded-xl border border-border bg-secondary/20 p-3">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
              Prompt
            </span>
            {shot.location_code && (
              <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                @{shot.location_code}
              </span>
            )}
          </div>

          <div className="rounded-lg border border-border/70 bg-card p-2.5 text-xs leading-relaxed text-foreground">
            {shot.subject_codes && shot.subject_codes.length > 0 && (
              <span className="mr-1.5 inline-flex flex-wrap gap-1">
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
            <span className="whitespace-pre-wrap break-words">
              {shot.action || <span className="text-muted-foreground">No action described.</span>}
            </span>
          </div>

          {/* SHOT TYPE Box inside Prompt container */}
          <dl className="grid gap-2 pt-1 text-xs sm:grid-cols-2">
            <div className="rounded-lg border border-border/70 bg-card px-2.5 py-2">
              <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                Shot size / camera
              </dt>
              <dd className="mt-0.5 break-words font-medium text-foreground">
                {shot.shot_size || "—"} · {shot.camera_motion || "—"}
              </dd>
            </div>

            <div className="rounded-lg border border-border/70 bg-card px-2.5 py-2">
              <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                Continuity
              </dt>
              <dd className="mt-0.5 break-words font-medium text-foreground">
                {shotContinuityLabel(shot.continuity_mode)}
              </dd>
            </div>

            <div className="rounded-lg border border-border/70 bg-card px-2.5 py-2">
              <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                Duration
              </dt>
              <dd className="mt-0.5 font-medium tabular-nums text-foreground">
                {shot.duration_seconds} s
              </dd>
            </div>

            <div className="rounded-lg border border-border/70 bg-card px-2.5 py-2">
              <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                Previous shot
              </dt>
              <dd className="mt-0.5 flex items-center gap-1 break-all font-medium text-foreground">
                {shot.previous_shot_id ? (
                  <>
                    <Link2 className="size-3 shrink-0 text-primary" aria-hidden="true" />
                    {previousShot ? shotLabel(previousShot) : shot.previous_shot_id}
                  </>
                ) : (
                  "None"
                )}
              </dd>
            </div>

            {shot.composition && (
              <div className="rounded-lg border border-border/70 bg-card px-2.5 py-2 sm:col-span-2">
                <dt className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                  Composition
                </dt>
                <dd className="mt-0.5 whitespace-pre-wrap break-words text-foreground">
                  {shot.composition}
                </dd>
              </div>
            )}
          </dl>
        </div>

        {/* LTX Studio MOTION SCALE Bar */}
        <div className="rounded-xl border border-border bg-card px-3 py-2.5">
          <div className="flex items-center justify-between text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
            <span>Motion Scale &amp; Pacing</span>
            <span className="font-mono text-foreground">{shot.duration_seconds}s</span>
          </div>
          <div className="relative mt-2 h-1.5 w-full rounded-full bg-secondary">
            <div
              className="h-full rounded-full bg-foreground/80"
              style={{ width: `${motionPercent}%` }}
            />
            <span
              className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-foreground bg-card shadow-2xs"
              style={{ left: `${motionPercent}%` }}
              aria-hidden="true"
            />
          </div>
        </div>

        {/* LTX Studio SFX / Continuity Footer */}
        <div className="flex items-center justify-between rounded-xl border border-border bg-card px-3 py-2 text-xs">
          <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
            SFX &amp; Dialogue Cue
          </span>
          <span className="inline-flex items-center gap-1 text-muted-foreground">
            <span className="max-w-[160px] truncate text-[11px]">
              {shot.dialogue ? shot.dialogue : "Ambient track"}
            </span>
            <Plus className="size-3.5" aria-hidden="true" />
          </span>
        </div>

        {isPredecessorOutOfOrder(shot, previousShot) && previousShot && (
          <p
            role="note"
            data-testid="shot-predecessor-warning"
            className="flex items-start gap-2 rounded-xl border border-warning/30 bg-warning/5 p-2.5 text-xs text-warning"
          >
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>
              {PREDECESSOR_ORDER_WARNING}: {shotLabel(previousShot)} is #
              {previousShot.sequence_index}; this shot is #{shot.sequence_index}.
            </span>
          </p>
        )}

        {shot.lint_report.length > 0 && (
          <div>
            <ShotLintReport issues={shot.lint_report} label={`Prompt Linter for ${label}`} />
          </div>
        )}
      </div>
    </article>
  );
}

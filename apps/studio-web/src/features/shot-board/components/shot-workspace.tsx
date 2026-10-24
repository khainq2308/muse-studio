import {
  CircleAlert,
  ChevronLeft,
  ChevronRight,
  FileText,
  Film,
  Link2,
  ListChecks,
  Pencil,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/features/shot-board/components/shot-badges";
import { ShotDetails, ShotPromptLint } from "@/features/shot-board/components/shot-details";
import { TakeMedia } from "@/features/shot-board/components/take-media";
import { SHOT_BOARD_TABS, type ShotBoardTab } from "@/features/shot-board/search";
import {
  shotApprovalLabel,
  shotApprovalTone,
  shotGenerationLabel,
  shotGenerationTone,
  shotLabel,
  type ProjectShot,
} from "@/features/shot-board/state";
import {
  FRAME_PREVIEW_MAX_BYTES,
  PREDECESSOR_ORDER_WARNING,
  isPredecessorOutOfOrder,
} from "@/features/shot-board/takes";
import { formatSeconds, shotLintSummary } from "@/features/shot-board/workspace";
import { cn } from "@/lib/utils";

const TAB_LABELS: Record<ShotBoardTab, string> = {
  takes: "Takes",
  details: "Details",
  prompt: "Prompt & lint",
};

const TAB_ICONS: Record<ShotBoardTab, typeof Film> = {
  takes: Film,
  details: FileText,
  prompt: ListChecks,
};

interface ShotWorkspaceProps {
  shot: ProjectShot;
  /** All shots of the project, to name linked shots. */
  shots: readonly ProjectShot[];
  /** Neighbours in navigator order, for previous/next navigation. */
  previousInOrder: ProjectShot | undefined;
  nextInOrder: ProjectShot | undefined;
  onSelectShot: (shotId: string) => void;
  tab: ShotBoardTab;
  onTabChange: (tab: ShotBoardTab) => void;
  /** Number of takes once the take list has loaded; null while unknown. */
  takeCount: number | null;
  /** The take list failed to load (the Takes tab shows the error and Retry). */
  takesFailed: boolean;
  canGenerate: boolean;
  generateDisabledReason: string | null;
  /** Opens the generation setup; offered next to a setup-related disabled reason. */
  onSetUpGeneration?: () => void;
  onGenerate: () => void;
  isEditing: boolean;
  onEdit: () => void;
  /** Inline panels under the header: editor, generate confirmation, queue status/result. */
  actions: ReactNode;
  /** Content of the Takes tab (the take review). */
  takesPanel: ReactNode;
}

/** Selected-shot panel: identity, status, primary actions and the Takes/Details/Prompt tabs. */
export function ShotWorkspace({
  shot,
  shots,
  previousInOrder,
  nextInOrder,
  onSelectShot,
  tab,
  onTabChange,
  takeCount,
  takesFailed,
  canGenerate,
  generateDisabledReason,
  onSetUpGeneration,
  onGenerate,
  isEditing,
  onEdit,
  actions,
  takesPanel,
}: ShotWorkspaceProps) {
  const idPrefix = useId();
  const tabRefs = useRef<Partial<Record<ShotBoardTab, HTMLButtonElement | null>>>({});
  const label = shotLabel(shot);
  const lint = shotLintSummary(shot);
  const predecessor = shot.previous_shot_id
    ? shots.find((candidate) => candidate.id === shot.previous_shot_id)
    : undefined;
  const headingId = `${idPrefix}-heading`;
  const tabId = (value: ShotBoardTab) => `${idPrefix}-tab-${value}`;
  const panelId = `${idPrefix}-panel`;

  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = SHOT_BOARD_TABS.indexOf(tab);
    const next =
      event.key === "ArrowRight"
        ? SHOT_BOARD_TABS[(index + 1) % SHOT_BOARD_TABS.length]
        : event.key === "ArrowLeft"
          ? SHOT_BOARD_TABS[(index - 1 + SHOT_BOARD_TABS.length) % SHOT_BOARD_TABS.length]
          : event.key === "Home"
            ? SHOT_BOARD_TABS[0]
            : event.key === "End"
              ? SHOT_BOARD_TABS[SHOT_BOARD_TABS.length - 1]
              : undefined;
    if (!next) return;
    event.preventDefault();
    onTabChange(next);
    tabRefs.current[next]?.focus();
  };

  const tabBadge = (value: ShotBoardTab) => {
    if (value === "takes" && takeCount !== null) return String(takeCount);
    if (value === "prompt" && shot.lint_report.length > 0) return String(shot.lint_report.length);
    return null;
  };

  return (
    <section
      aria-labelledby={headingId}
      data-testid={`shot-workspace-${shot.id}`}
      className="min-w-0 rounded-xl border border-border bg-card shadow-2xs"
    >
      <header className="space-y-3 border-b border-border p-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          <div className="flex shrink-0 gap-2">
            <figure className="space-y-1">
              <TakeMedia
                assetId={shot.first_frame_asset_id}
                label={`${label} first frame`}
                maxBytes={FRAME_PREVIEW_MAX_BYTES}
                variant="frame"
                emptyText="No first frame"
              />
              <figcaption className="text-xs text-muted-foreground">First frame</figcaption>
            </figure>
            <figure className="space-y-1">
              <TakeMedia
                assetId={shot.last_frame_asset_id}
                label={`${label} last frame`}
                maxBytes={FRAME_PREVIEW_MAX_BYTES}
                variant="frame"
                emptyText="No last frame"
              />
              <figcaption className="text-xs text-muted-foreground">Last frame</figcaption>
            </figure>
          </div>

          <div className="min-w-0 flex-1 space-y-2">
            <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
              <span className="break-all">Scene {shot.scene_id}</span>
              <span aria-hidden="true">·</span>
              <span className="font-mono">#{shot.sequence_index}</span>
              <span aria-hidden="true">·</span>
              <span className="font-mono">{formatSeconds(shot.duration_seconds)}</span>
            </p>
            <h3 id={headingId} className="break-words text-lg font-bold tracking-tight">
              {label}
            </h3>
            <div className="flex flex-wrap gap-1.5" data-testid="shot-status">
              <StateBadge
                tone={shotGenerationTone(shot.generation_state)}
                testId="shot-generation-state"
              >
                {shotGenerationLabel(shot.generation_state)}
              </StateBadge>
              <StateBadge tone={shotApprovalTone(shot.approval_state)} testId="shot-approval-state">
                {shotApprovalLabel(shot.approval_state)}
              </StateBadge>
              <StateBadge tone={lint.tone} testId="shot-lint-status">
                {lint.label}
              </StateBadge>
              {shot.continuity_mode === "frame_chain" && (
                <StateBadge
                  tone="border border-primary/30 bg-primary/10 text-primary"
                  testId="shot-continuity"
                >
                  <Link2 className="size-3.5" aria-hidden="true" />
                  Frame chain
                </StateBadge>
              )}
              <StateBadge
                tone="border border-border bg-secondary text-secondary-foreground"
                testId="shot-take-status"
              >
                <Film className="size-3.5" aria-hidden="true" />
                {shot.selected_take_id ? "Take selected" : "No selected take"}
              </StateBadge>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            onClick={onGenerate}
            disabled={!canGenerate}
            title={generateDisabledReason ?? undefined}
            aria-label={`Generate ${label}`}
            aria-haspopup="dialog"
          >
            <Sparkles aria-hidden="true" />
            Generate
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onEdit}
            disabled={isEditing}
            aria-label={`Edit ${label}`}
          >
            <Pencil aria-hidden="true" />
            Edit
          </Button>
          <div className="ml-auto flex gap-1">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={!previousInOrder}
              onClick={() => previousInOrder && onSelectShot(previousInOrder.id)}
              aria-label={
                previousInOrder ? `Previous shot: ${shotLabel(previousInOrder)}` : "Previous shot"
              }
            >
              <ChevronLeft aria-hidden="true" />
              <span className="hidden sm:inline">Prev</span>
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={!nextInOrder}
              onClick={() => nextInOrder && onSelectShot(nextInOrder.id)}
              aria-label={nextInOrder ? `Next shot: ${shotLabel(nextInOrder)}` : "Next shot"}
            >
              <span className="hidden sm:inline">Next</span>
              <ChevronRight aria-hidden="true" />
            </Button>
          </div>
        </div>
        {generateDisabledReason && (
          <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            {generateDisabledReason}
            {onSetUpGeneration && (
              <button
                type="button"
                onClick={onSetUpGeneration}
                className="font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Set up generation
              </button>
            )}
          </p>
        )}

        {isPredecessorOutOfOrder(shot, predecessor) && predecessor && (
          <p
            role="note"
            data-testid="shot-predecessor-warning"
            className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 p-2.5 text-xs text-warning"
          >
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>
              {PREDECESSOR_ORDER_WARNING}: {shotLabel(predecessor)} is #{predecessor.sequence_index}
              ; this shot is #{shot.sequence_index}.
            </span>
          </p>
        )}

        {actions}
      </header>

      <div
        role="tablist"
        aria-label={`${label} sections`}
        onKeyDown={onTabKeyDown}
        className="flex gap-1 overflow-x-auto border-b border-border px-2 pt-2"
      >
        {SHOT_BOARD_TABS.map((value) => {
          const Icon = TAB_ICONS[value];
          const badge = tabBadge(value);
          const active = value === tab;
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
              aria-controls={panelId}
              tabIndex={active ? 0 : -1}
              onClick={() => onTabChange(value)}
              className={cn(
                "-mb-px inline-flex shrink-0 items-center gap-1.5 rounded-t-md border-b-2 px-2 py-2 sm:px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="hidden size-4 sm:block" aria-hidden="true" />
              {TAB_LABELS[value]}
              {badge && (
                <span className="rounded-full bg-secondary px-1.5 text-xs tabular-nums text-muted-foreground">
                  {badge}
                </span>
              )}
              {value === "takes" && takesFailed && (
                <>
                  <CircleAlert className="size-3.5 text-destructive" aria-hidden="true" />
                  <span className="sr-only">(failed to load)</span>
                </>
              )}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={tabId(tab)}
        data-testid={`shot-tab-panel-${tab}`}
        className="p-4"
      >
        {tab === "takes" ? (
          takesPanel
        ) : tab === "details" ? (
          <ShotDetails shot={shot} shots={shots} />
        ) : (
          <ShotPromptLint shot={shot} />
        )}
      </div>
    </section>
  );
}

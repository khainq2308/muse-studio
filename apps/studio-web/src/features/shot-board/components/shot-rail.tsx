import { ChevronDown, Clapperboard, Film, ImageOff, Link2, ListFilter } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent } from "react";
import { StateBadge } from "@/features/shot-board/components/shot-badges";
import { TakeMedia } from "@/features/shot-board/components/take-media";
import {
  groupShotsByScene,
  shotApprovalLabel,
  shotApprovalTone,
  shotGenerationLabel,
  shotGenerationTone,
  shotLabel,
  type ProjectShot,
} from "@/features/shot-board/state";
import { FRAME_PREVIEW_MAX_BYTES } from "@/features/shot-board/takes";
import {
  SHOT_FILTERS,
  SHOT_FILTER_DESCRIPTIONS,
  SHOT_FILTER_LABELS,
  filterSceneGroups,
  formatSeconds,
  isShotBlocked,
  shotFilterCounts,
  shotLintSummary,
  type ShotFilter,
} from "@/features/shot-board/workspace";
import { cn } from "@/lib/utils";

interface ShotRailProps {
  shots: readonly ProjectShot[];
  /** Shot shown in the main panel. */
  selectedShotId: string | undefined;
  filter: ShotFilter;
  onFilterChange: (filter: ShotFilter) => void;
  onSelect: (shotId: string) => void;
}

const NAV_ITEM_SELECTOR = "[data-shot-nav]";

/**
 * Scene → shot navigator. Each shot is a button (`aria-current` marks the open one); Arrow
 * Up/Down, Home and End move focus through the list and Enter/Space opens the focused shot.
 * Below `lg` the list collapses behind a toggle so the selected shot stays on screen.
 */
export function ShotRail({
  shots,
  selectedShotId,
  filter,
  onFilterChange,
  onSelect,
}: ShotRailProps) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  const counts = shotFilterCounts(shots);
  const groups = filterSceneGroups(shots, filter);
  const sceneTotals = new Map(
    groupShotsByScene(shots).map((group) => [
      group.sceneId,
      {
        count: group.shots.length,
        seconds: group.shots.reduce(
          (sum, shot) => sum + (Number.isFinite(shot.duration_seconds) ? shot.duration_seconds : 0),
          0,
        ),
      },
    ]),
  );
  const selectedShot = shots.find((shot) => shot.id === selectedShotId);
  const selectedHidden =
    selectedShot !== undefined &&
    !groups.some((group) => group.shots.some((shot) => shot.id === selectedShot.id));

  const moveFocus = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = [...(listRef.current?.querySelectorAll<HTMLElement>(NAV_ITEM_SELECTOR) ?? [])];
    if (items.length === 0) return;
    const current = items.indexOf(document.activeElement as HTMLElement);
    if (current < 0 && (event.key === "ArrowDown" || event.key === "ArrowUp")) return;
    event.preventDefault();
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : event.key === "ArrowDown"
            ? Math.min(current + 1, items.length - 1)
            : Math.max(current - 1, 0);
    items[next]?.focus();
  };

  return (
    <nav
      aria-label="Shot navigator"
      data-testid="shot-rail"
      className="rounded-xl border border-border bg-card shadow-2xs lg:sticky lg:top-4 lg:flex lg:max-h-[calc(100vh-2rem)] lg:flex-col"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2.5">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Clapperboard className="size-4 text-primary" aria-hidden="true" />
          Shots
          <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-medium text-muted-foreground tabular-nums">
            {shots.length}
          </span>
        </h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen((current) => !current)}
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs font-medium text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:hidden"
        >
          {open ? "Hide shot list" : "Show shot list"}
          <ChevronDown
            className={cn("size-3.5 transition-transform", open && "rotate-180")}
            aria-hidden="true"
          />
        </button>
      </div>

      <div
        id={listId}
        className={cn(
          "space-y-3 p-3 lg:block lg:min-h-0 lg:flex-1 lg:overflow-y-auto",
          open ? "block" : "hidden",
        )}
      >
        <div role="group" aria-label="Filter shots" className="flex flex-wrap gap-1.5">
          <ListFilter className="mt-1 size-3.5 text-muted-foreground" aria-hidden="true" />
          {SHOT_FILTERS.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={filter === option}
              title={SHOT_FILTER_DESCRIPTIONS[option]}
              onClick={() => onFilterChange(option)}
              className={cn(
                "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                filter === option
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-background text-muted-foreground hover:text-foreground",
              )}
            >
              {SHOT_FILTER_LABELS[option]}
              <span className="tabular-nums">{counts[option]}</span>
            </button>
          ))}
        </div>

        {selectedHidden && selectedShot && (
          <p role="note" className="text-xs text-muted-foreground">
            {shotLabel(selectedShot)} is open but hidden by the &quot;{SHOT_FILTER_LABELS[filter]}
            &quot; filter.
          </p>
        )}

        {groups.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">
            No shots match the &quot;{SHOT_FILTER_LABELS[filter]}&quot; filter.
          </p>
        ) : (
          <div ref={listRef} onKeyDown={moveFocus} className="space-y-4">
            {groups.map((group) => {
              const totals = sceneTotals.get(group.sceneId);
              return (
                <section
                  key={group.sceneId}
                  aria-label={`Scene ${group.sceneId}`}
                  data-testid={`shot-scene-${group.sceneId}`}
                  className="space-y-1.5"
                >
                  <h4 className="flex flex-wrap items-baseline justify-between gap-x-2 px-1 text-xs font-semibold text-muted-foreground">
                    <span className="break-all">Scene {group.sceneId}</span>
                    {totals && (
                      <span className="font-mono font-normal">
                        {totals.count} {totals.count === 1 ? "shot" : "shots"} ·{" "}
                        {formatSeconds(totals.seconds)}
                      </span>
                    )}
                  </h4>
                  <ul className="space-y-1.5">
                    {group.shots.map((shot) => (
                      <li key={shot.id}>
                        <ShotRailItem
                          shot={shot}
                          selected={shot.id === selectedShotId}
                          onSelect={() => {
                            onSelect(shot.id);
                            setOpen(false);
                          }}
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        )}
      </div>
    </nav>
  );
}

function ShotRailItem({
  shot,
  selected,
  onSelect,
}: {
  shot: ProjectShot;
  selected: boolean;
  onSelect: () => void;
}) {
  const statusId = useId();
  const label = shotLabel(shot);
  const lint = shotLintSummary(shot);
  const blocked = isShotBlocked(shot);
  const hasSelectedFrame = Boolean(shot.selected_take_id && shot.last_frame_asset_id);

  return (
    <button
      type="button"
      data-shot-nav=""
      data-testid={`shot-card-${shot.id}`}
      aria-current={selected ? "true" : undefined}
      aria-label={label}
      aria-describedby={statusId}
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-2.5 rounded-lg border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected
          ? "border-primary bg-primary/5 ring-1 ring-primary/30"
          : "border-border bg-background hover:border-primary/40 hover:bg-secondary/40",
        blocked && "border-l-4 border-l-destructive",
      )}
    >
      <span className="shrink-0">
        {hasSelectedFrame ? (
          <TakeMedia
            assetId={shot.last_frame_asset_id}
            label={`${label} selected take last frame`}
            maxBytes={FRAME_PREVIEW_MAX_BYTES}
            variant="thumb"
          />
        ) : (
          <span
            className={cn(
              "flex aspect-video w-16 items-center justify-center rounded-md border text-muted-foreground",
              shot.selected_take_id
                ? "border-border bg-secondary"
                : "border-dashed border-border bg-background",
            )}
            title={shot.selected_take_id ? "Selected take has no last frame" : "No selected take"}
          >
            {shot.selected_take_id ? (
              <Film className="size-3.5" aria-hidden="true" />
            ) : (
              <ImageOff className="size-3.5" aria-hidden="true" />
            )}
          </span>
        )}
      </span>

      <span className="min-w-0 flex-1 space-y-1">
        <span className="flex items-baseline gap-1.5">
          <span className="shrink-0 font-mono text-xs font-semibold text-muted-foreground">
            #{shot.sequence_index}
          </span>
          <span className="truncate text-sm font-medium text-foreground">
            {shot.title.trim() || label}
          </span>
        </span>
        <span id={statusId} className="flex flex-wrap gap-1">
          <StateBadge
            size="sm"
            tone={shotGenerationTone(shot.generation_state)}
            testId="shot-generation-state"
          >
            {shotGenerationLabel(shot.generation_state)}
          </StateBadge>
          <StateBadge
            size="sm"
            tone={shotApprovalTone(shot.approval_state)}
            testId="shot-approval-state"
          >
            {shotApprovalLabel(shot.approval_state)}
          </StateBadge>
          {lint.level !== "clear" && (
            <StateBadge size="sm" tone={lint.tone} testId="shot-lint-status">
              {lint.label}
            </StateBadge>
          )}
          {shot.continuity_mode === "frame_chain" && (
            <StateBadge
              size="sm"
              tone="border border-primary/30 bg-primary/10 text-primary"
              testId="shot-continuity"
            >
              <Link2 className="size-3" aria-hidden="true" />
              Frame chain
            </StateBadge>
          )}
          {!shot.selected_take_id && (
            <span className="sr-only" data-testid="shot-take-status">
              No selected take
            </span>
          )}
        </span>
      </span>
    </button>
  );
}

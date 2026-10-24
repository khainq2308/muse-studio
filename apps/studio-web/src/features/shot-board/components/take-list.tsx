import { CheckCircle2, Eye, EyeOff, Flag, FlagOff, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/features/shot-board/components/shot-badges";
import { TakeMedia } from "@/features/shot-board/components/take-media";
import {
  FRAME_PREVIEW_MAX_BYTES,
  TAKE_PREVIEW_MAX_BYTES,
  parseTakeCreatedAt,
  takeLabel,
  type ShotTake,
} from "@/features/shot-board/takes";
import { cn } from "@/lib/utils";

interface TakeListProps {
  takes: readonly ShotTake[];
  /** Take IDs chosen for A/B compare (at most two, in A/B order). */
  compareIds: readonly string[];
  previewTakeId: string | null;
  /** Take whose request (select or flag) is in flight. */
  busyTakeId: string | null;
  /** False while any take action is in flight. */
  actionsEnabled: boolean;
  onToggleCompare: (takeId: string) => void;
  onTogglePreview: (takeId: string) => void;
  onSelect: (takeId: string) => void;
  onFlag: (takeId: string) => void;
  onClearFlag: (take: ShotTake) => void;
  /** Slot under a take: the continuity flag form or an action result. */
  renderTakeDetail: (take: ShotTake) => ReactNode;
}

export function TakeList({
  takes,
  compareIds,
  previewTakeId,
  busyTakeId,
  actionsEnabled,
  onToggleCompare,
  onTogglePreview,
  onSelect,
  onFlag,
  onClearFlag,
  renderTakeDetail,
}: TakeListProps) {
  return (
    <ul className="space-y-3" aria-label="Takes">
      {takes.map((take) => {
        const label = takeLabel(take);
        const createdAt = parseTakeCreatedAt(take.created_at);
        const compareIndex = compareIds.indexOf(take.id);
        const inCompare = compareIndex >= 0;
        const isBusy = busyTakeId === take.id;
        const previewOpen = previewTakeId === take.id;
        return (
          <li
            key={take.id}
            data-testid={`take-${take.id}`}
            className={cn(
              "rounded-lg border bg-card p-3",
              take.is_selected ? "border-success/50" : "border-border",
            )}
          >
            <div className="flex flex-col gap-3 md:flex-row">
              <div className="flex shrink-0 gap-2">
                <figure className="space-y-1">
                  <TakeMedia
                    assetId={take.first_frame_asset_id}
                    label={`${label} first frame`}
                    maxBytes={FRAME_PREVIEW_MAX_BYTES}
                    variant="frame"
                    emptyText="No first frame"
                  />
                  <figcaption className="text-xs text-muted-foreground">First frame</figcaption>
                </figure>
                <figure className="space-y-1">
                  <TakeMedia
                    assetId={take.last_frame_asset_id}
                    label={`${label} last frame`}
                    maxBytes={FRAME_PREVIEW_MAX_BYTES}
                    variant="frame"
                    emptyText="No last frame"
                  />
                  <figcaption className="text-xs text-muted-foreground">Last frame</figcaption>
                </figure>
              </div>

              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <h5 className="font-semibold">{label}</h5>
                  {take.is_selected && (
                    <StateBadge
                      tone="border border-success/30 bg-success/10 text-success"
                      testId="take-selected-badge"
                    >
                      <CheckCircle2 className="size-3.5" aria-hidden="true" />
                      Selected
                    </StateBadge>
                  )}
                  {take.continuity_flagged && (
                    <StateBadge
                      tone="border border-warning/30 bg-warning/10 text-warning"
                      testId="take-continuity-badge"
                    >
                      <Flag className="size-3.5" aria-hidden="true" />
                      Continuity flagged
                    </StateBadge>
                  )}
                </div>
                <dl className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <div className="min-w-0">
                    <dt className="text-xs text-muted-foreground">Created</dt>
                    <dd className="break-words">
                      {createdAt ? (
                        <time dateTime={createdAt.toISOString()} data-testid="take-created-at">
                          {createdAt.toLocaleString()}
                        </time>
                      ) : (
                        <span data-testid="take-created-at">
                          {take.created_at?.trim() || "Not recorded"}
                        </span>
                      )}
                    </dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-muted-foreground">Model</dt>
                    <dd className="break-all font-medium">{take.model_id}</dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-muted-foreground">Generated by task</dt>
                    <dd className="break-all font-mono text-xs">{take.task_id}</dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-muted-foreground">Account used</dt>
                    <dd className="break-all">{take.account_used ?? "Not recorded"}</dd>
                  </div>
                  {take.continuity_flagged && (
                    <div className="min-w-0 sm:col-span-2 lg:col-span-4">
                      <dt className="text-xs text-muted-foreground">Director notes</dt>
                      <dd className="whitespace-pre-wrap break-words" data-testid="take-notes">
                        {take.director_notes || "None"}
                      </dd>
                    </div>
                  )}
                </dl>

                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onTogglePreview(take.id)}
                    aria-expanded={previewOpen}
                    aria-label={`${previewOpen ? "Hide" : "Preview"} ${label}`}
                  >
                    {previewOpen ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                    {previewOpen ? "Hide preview" : "Preview"}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => onSelect(take.id)}
                    disabled={take.is_selected || !actionsEnabled}
                    aria-label={take.is_selected ? `${label} is selected` : `Select ${label}`}
                  >
                    {isBusy ? (
                      <LoaderCircle className="animate-spin" aria-hidden="true" />
                    ) : (
                      <CheckCircle2 aria-hidden="true" />
                    )}
                    {take.is_selected ? "Selected" : "Select take"}
                  </Button>
                  {take.continuity_flagged ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => onClearFlag(take)}
                      disabled={!actionsEnabled}
                      aria-label={`Clear continuity flag on ${label}`}
                    >
                      <FlagOff aria-hidden="true" />
                      Clear flag
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    data-flag-take={take.id}
                    onClick={() => onFlag(take.id)}
                    disabled={!actionsEnabled}
                    aria-label={`Flag continuity on ${label}`}
                  >
                    <Flag aria-hidden="true" />
                    {take.continuity_flagged ? "Edit flag" : "Flag continuity"}
                  </Button>
                  <label className="inline-flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={inCompare}
                      disabled={!inCompare && compareIds.length >= 2}
                      onChange={() => onToggleCompare(take.id)}
                      className="size-4 accent-primary"
                      aria-label={`Compare ${label}`}
                    />
                    {inCompare ? `Compare (${compareIndex === 0 ? "A" : "B"})` : "Compare"}
                  </label>
                </div>
              </div>
            </div>

            {previewOpen && (
              <div className="mt-3">
                <TakeMedia
                  assetId={take.asset_id}
                  label={`${label} media`}
                  maxBytes={TAKE_PREVIEW_MAX_BYTES}
                  variant="preview"
                />
              </div>
            )}
            {renderTakeDetail(take)}
          </li>
        );
      })}
    </ul>
  );
}

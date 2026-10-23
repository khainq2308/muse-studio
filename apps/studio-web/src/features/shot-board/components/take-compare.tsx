import { ArrowLeftRight, CheckCircle2, Flag, LoaderCircle, X } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/features/shot-board/components/shot-badges";
import { TakeMedia } from "@/features/shot-board/components/take-media";
import {
  FRAME_PREVIEW_MAX_BYTES,
  TAKE_PREVIEW_MAX_BYTES,
  takeLabel,
  type CompareShotTakes,
  type TakeComparison,
} from "@/features/shot-board/takes";

const SAME_TONE = "border border-success/30 bg-success/10 text-success";
const DIFFERENT_TONE = "border border-warning/30 bg-warning/10 text-warning";

interface TakeCompareProps {
  comparison: CompareShotTakes;
  /** Take whose selection request is in flight, if any. */
  selectingTakeId: string | null;
  canSelect: boolean;
  onSelect: (takeId: string) => void;
  onSwap: () => void;
  onClose: () => void;
}

/** Side-by-side A/B review of two takes using the server comparison. */
export function TakeCompare({
  comparison,
  selectingTakeId,
  canSelect,
  onSelect,
  onSwap,
  onClose,
}: TakeCompareProps) {
  const headingId = useId();
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => headingRef.current?.focus(), []);

  return (
    <section
      aria-labelledby={headingId}
      data-testid="take-compare"
      className="mt-4 space-y-4 rounded-lg border border-primary/40 bg-background/60 p-4"
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        onClose();
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-2">
          <h5
            id={headingId}
            ref={headingRef}
            tabIndex={-1}
            className="font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            A/B compare: {takeLabel(comparison.take_a)} vs {takeLabel(comparison.take_b)}
          </h5>
          <div className="flex flex-wrap gap-2">
            <StateBadge
              tone={comparison.same_prompt_hash ? SAME_TONE : DIFFERENT_TONE}
              testId="compare-same-prompt"
            >
              {comparison.same_prompt_hash ? "Same prompt" : "Different prompt"}
            </StateBadge>
            <StateBadge
              tone={comparison.same_model ? SAME_TONE : DIFFERENT_TONE}
              testId="compare-same-model"
            >
              {comparison.same_model ? "Same model" : "Different model"}
            </StateBadge>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" onClick={onSwap}>
            <ArrowLeftRight aria-hidden="true" />
            Swap A/B
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>
            <X aria-hidden="true" />
            Close compare
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <CompareSide
          side="A"
          take={comparison.take_a}
          isSelecting={selectingTakeId === comparison.take_a.take_id}
          canSelect={canSelect}
          onSelect={() => onSelect(comparison.take_a.take_id)}
        />
        <CompareSide
          side="B"
          take={comparison.take_b}
          isSelecting={selectingTakeId === comparison.take_b.take_id}
          canSelect={canSelect}
          onSelect={() => onSelect(comparison.take_b.take_id)}
        />
      </div>
    </section>
  );
}

interface CompareSideProps {
  side: "A" | "B";
  take: TakeComparison;
  isSelecting: boolean;
  canSelect: boolean;
  onSelect: () => void;
}

function CompareSide({ side, take, isSelecting, canSelect, onSelect }: CompareSideProps) {
  const label = takeLabel(take);
  return (
    <article
      aria-label={`Side ${side}: ${label}`}
      data-testid={`compare-side-${side}`}
      className="min-w-0 space-y-3 rounded-md border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-primary/15 px-2 py-0.5 text-xs font-semibold text-primary">
          {side}
        </span>
        <h6 className="font-semibold">{label}</h6>
        {take.is_selected && (
          <StateBadge tone={SAME_TONE} testId="compare-selected">
            <CheckCircle2 className="size-3.5" aria-hidden="true" />
            Selected
          </StateBadge>
        )}
        {take.continuity_flagged && (
          <StateBadge tone={DIFFERENT_TONE}>
            <Flag className="size-3.5" aria-hidden="true" />
            Continuity flagged
          </StateBadge>
        )}
      </div>

      <TakeMedia
        assetId={take.asset_id}
        label={`${label} media`}
        maxBytes={TAKE_PREVIEW_MAX_BYTES}
        variant="preview"
      />

      <div className="flex flex-wrap gap-3">
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

      <dl className="grid gap-2 text-sm">
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Model</dt>
          <dd className="break-all font-medium">{take.model_id}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Prompt hash</dt>
          <dd className="break-all font-mono text-xs">{take.prompt_hash}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Account used</dt>
          <dd className="break-all">{take.account_used ?? "Not recorded"}</dd>
        </div>
        {take.continuity_flagged && (
          <div className="min-w-0">
            <dt className="text-xs text-muted-foreground">Director notes</dt>
            <dd className="whitespace-pre-wrap break-words">{take.director_notes || "None"}</dd>
          </div>
        )}
      </dl>

      <Button
        type="button"
        size="sm"
        onClick={onSelect}
        disabled={take.is_selected || !canSelect}
        aria-label={take.is_selected ? `${label} is selected` : `Select ${label} (side ${side})`}
      >
        {isSelecting ? (
          <LoaderCircle className="animate-spin" aria-hidden="true" />
        ) : (
          <CheckCircle2 aria-hidden="true" />
        )}
        {take.is_selected ? "Selected" : isSelecting ? "Selecting..." : `Select ${label}`}
      </Button>
    </article>
  );
}

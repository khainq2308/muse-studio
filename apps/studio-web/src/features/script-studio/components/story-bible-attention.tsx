import { ImagePlus, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import type { BibleAttentionItem } from "@/features/script-studio/attention";
import type { StoryBibleEntry } from "@/features/script-studio/state";

interface StoryBibleAttentionProps {
  items: BibleAttentionItem[];
  /** Shot counts are unknown while the shot list is loading or failed. */
  shotCountsKnown: boolean;
  isEditorBusy: boolean;
  onAddReference: (entry: StoryBibleEntry) => void;
}

/** Characters missing a required reference image: the Story Bible fixes that unblock shots. */
export function StoryBibleAttention({
  items,
  shotCountsKnown,
  isEditorBusy,
  onAddReference,
}: StoryBibleAttentionProps) {
  const { t } = useTranslation("script-studio");
  if (items.length === 0) return null;
  return (
    <section
      aria-labelledby="story-bible-attention-heading"
      data-testid="story-bible-attention"
      className="space-y-3 rounded-xl border border-warning/40 bg-warning/5 p-4"
    >
      <div>
        <h3
          id="story-bible-attention-heading"
          className="flex items-center gap-2 text-sm font-semibold text-foreground"
        >
          <TriangleAlert className="size-4 shrink-0 text-warning" aria-hidden="true" />
          {t("bible.attention.title", { count: items.length })}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">{t("bible.attention.description")}</p>
      </div>
      <ul className="divide-y divide-warning/20">
        {items.map(({ entry, blockedShots }) => (
          <li
            key={entry.code_name}
            className="flex flex-col gap-2 py-2.5 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">
                {entry.display_name}{" "}
                <span className="font-mono text-xs text-muted-foreground">{entry.code_name}</span>
              </p>
              {shotCountsKnown && (
                <p className="text-xs text-warning">
                  {blockedShots > 0
                    ? t("bible.attention.blocksShots", { count: blockedShots })
                    : t("bible.attention.noBlockedShots")}
                </p>
              )}
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={isEditorBusy}
              aria-label={t("bible.attention.addReferenceAria", { name: entry.display_name })}
              onClick={() => onAddReference(entry)}
            >
              <ImagePlus className="size-4" aria-hidden="true" />
              {t("bible.attention.addReference")}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}

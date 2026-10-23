import { Lock, Pencil, TriangleAlert } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { ReferenceAssetPreview } from "@/features/script-studio/components/reference-asset-preview";
import {
  STORY_BIBLE_ENTRY_LABELS,
  STORY_BIBLE_ENTRY_TYPES,
  filterStoryBibleEntries,
  storyBibleEntryTypeLabel,
  storyBibleEntryTypeTone,
  summarizeMutableAttributes,
  type StoryBibleEntry,
  type StoryBibleFilterType,
} from "@/features/script-studio/state";
import { cn } from "@/lib/utils";

interface StoryBibleListProps {
  entries: readonly StoryBibleEntry[];
  editingCodeName: string | null;
  isEditorBusy?: boolean;
  onEdit: (entry: StoryBibleEntry) => void;
  renderEntryEditor?: (entry: StoryBibleEntry) => ReactNode;
}

export function StoryBibleList({
  entries,
  editingCodeName,
  isEditorBusy = false,
  onEdit,
  renderEntryEditor,
}: StoryBibleListProps) {
  const [filter, setFilter] = useState<StoryBibleFilterType>("all");

  const counts = useMemo(() => {
    const map: Record<StoryBibleFilterType, number> = {
      all: entries.length,
      character: 0,
      location: 0,
      style: 0,
      prop: 0,
    };
    for (const entry of entries) {
      if (entry.entry_type in map) {
        map[entry.entry_type] += 1;
      }
    }
    return map;
  }, [entries]);

  const visibleEntries = useMemo(() => filterStoryBibleEntries(entries, filter), [entries, filter]);

  const filterButtons: ReadonlyArray<{ value: StoryBibleFilterType; label: string }> = [
    { value: "all", label: "All" },
    ...STORY_BIBLE_ENTRY_TYPES.map((type) => ({
      value: type,
      label: STORY_BIBLE_ENTRY_LABELS[type],
    })),
  ];

  return (
    <div className="space-y-4" data-testid="story-bible-list">
      <div
        role="group"
        aria-label="Filter Story Bible entries by type"
        className="flex flex-wrap gap-2"
      >
        {filterButtons.map(({ value, label }) => {
          const active = filter === value;
          return (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={active ? "default" : "outline"}
              aria-pressed={active}
              onClick={() => setFilter(value)}
            >
              {label} ({counts[value]})
            </Button>
          );
        })}
      </div>

      {visibleEntries.length === 0 ? (
        <p
          data-testid="story-bible-filter-empty"
          className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground"
        >
          No {filter === "all" ? "" : `${STORY_BIBLE_ENTRY_LABELS[filter].toLowerCase()} `}entries
          match the selected filter.
        </p>
      ) : (
        <ul className="space-y-3">
          {visibleEntries.map((entry) => {
            const attributes = summarizeMutableAttributes(entry);
            const missingRequiredReference =
              entry.entry_type === "character" &&
              entry.requires_reference_lock &&
              entry.reference_asset_ids.length === 0;
            const isEditing = editingCodeName === entry.code_name;

            return (
              <li key={entry.id}>
                <article
                  data-testid={`story-bible-entry-${entry.code_name}`}
                  aria-label={`${storyBibleEntryTypeLabel(entry.entry_type)} ${entry.display_name}`}
                  className="rounded-lg border border-border bg-card p-4 shadow-xs"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={cn(
                            "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
                            storyBibleEntryTypeTone(entry.entry_type),
                          )}
                        >
                          {storyBibleEntryTypeLabel(entry.entry_type)}
                        </span>
                        <h4 className="text-base font-semibold">{entry.display_name}</h4>
                        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                          {entry.code_name}
                        </code>
                        {entry.entry_type === "character" && entry.requires_reference_lock && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                            <Lock className="size-3" aria-hidden="true" />
                            Reference lock required
                          </span>
                        )}
                      </div>
                      {entry.immutable_traits ? (
                        <p className="text-sm text-foreground">{entry.immutable_traits}</p>
                      ) : (
                        <p className="text-xs italic text-muted-foreground">
                          No immutable traits specified.
                        </p>
                      )}
                    </div>

                    <Button
                      type="button"
                      size="sm"
                      variant={isEditing ? "secondary" : "outline"}
                      disabled={isEditorBusy}
                      aria-expanded={isEditing}
                      onClick={() => onEdit(entry)}
                      aria-label={`Edit ${entry.display_name} (${entry.code_name})`}
                    >
                      <Pencil className="size-3.5" aria-hidden="true" />
                      Edit
                    </Button>
                  </div>

                  {missingRequiredReference && (
                    <p
                      role="note"
                      className="mt-3 flex items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning"
                    >
                      <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
                      Reference lock is required for this entry, but no reference asset is attached
                      yet. Downstream shots referencing {entry.code_name} will be blocked until a
                      reference image is added.
                    </p>
                  )}

                  {attributes.length > 0 && (
                    <dl className="mt-3 grid gap-2 rounded-md bg-muted/40 p-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
                      {attributes.map((item) => (
                        <div key={item.label} className="space-y-0.5">
                          <dt className="font-medium text-muted-foreground">{item.label}</dt>
                          <dd className="text-foreground">{item.value}</dd>
                        </div>
                      ))}
                    </dl>
                  )}

                  {entry.reference_asset_ids.length > 0 && (
                    <div className="mt-3 space-y-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        Reference assets ({entry.reference_asset_ids.length})
                      </p>
                      <div className="flex flex-wrap gap-3">
                        {entry.reference_asset_ids.map((assetId) => (
                          <div key={assetId} className="space-y-1">
                            <ReferenceAssetPreview
                              assetId={assetId}
                              label={`Reference ${assetId} for ${entry.display_name}`}
                            />
                            <p className="w-28 truncate font-mono text-[11px] text-muted-foreground">
                              {assetId}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {renderEntryEditor?.(entry)}
                </article>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

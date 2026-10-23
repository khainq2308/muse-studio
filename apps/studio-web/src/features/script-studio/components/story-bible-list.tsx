import {
  Box,
  LayoutGrid,
  List,
  Lock,
  MapPin,
  Palette,
  Pencil,
  Search,
  TriangleAlert,
  UserRound,
} from "lucide-react";
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

function entryCoverTone(entryType: string): {
  gradient: string;
  iconBg: string;
  Icon: typeof UserRound;
} {
  switch (entryType) {
    case "character":
      return {
        gradient: "from-indigo-950 via-slate-900 to-violet-950",
        iconBg: "bg-indigo-500/20 text-indigo-300 border-indigo-400/30",
        Icon: UserRound,
      };
    case "location":
      return {
        gradient: "from-emerald-950 via-slate-900 to-teal-950",
        iconBg: "bg-emerald-500/20 text-emerald-300 border-emerald-400/30",
        Icon: MapPin,
      };
    case "style":
      return {
        gradient: "from-amber-950 via-slate-900 to-orange-950",
        iconBg: "bg-amber-500/20 text-amber-300 border-amber-400/30",
        Icon: Palette,
      };
    default:
      return {
        gradient: "from-slate-900 via-slate-800 to-zinc-900",
        iconBg: "bg-slate-500/20 text-slate-300 border-slate-400/30",
        Icon: Box,
      };
  }
}

export function StoryBibleList({
  entries,
  editingCodeName,
  isEditorBusy = false,
  onEdit,
  renderEntryEditor,
}: StoryBibleListProps) {
  const [filter, setFilter] = useState<StoryBibleFilterType>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

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

  const visibleEntries = useMemo(() => {
    const byType = filterStoryBibleEntries(entries, filter);
    const trimmed = searchQuery.trim().toLowerCase();
    if (!trimmed) return byType;
    return byType.filter(
      (entry) =>
        entry.display_name.toLowerCase().includes(trimmed) ||
        entry.code_name.toLowerCase().includes(trimmed) ||
        (entry.immutable_traits ?? "").toLowerCase().includes(trimmed),
    );
  }, [entries, filter, searchQuery]);

  const filterButtons: ReadonlyArray<{ value: StoryBibleFilterType; label: string }> = [
    { value: "all", label: "All" },
    ...STORY_BIBLE_ENTRY_TYPES.map((type) => ({
      value: type,
      label: STORY_BIBLE_ENTRY_LABELS[type],
    })),
  ];

  return (
    <div className="space-y-5" data-testid="story-bible-list">
      {/* Nucanon-inspired Command & Filter Bar */}
      <div className="flex flex-col gap-3 rounded-xl border border-border/80 bg-card p-3 shadow-xs lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-1 flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative flex-1 sm:max-w-xs">
            <Search
              className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <input
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search entities, code names, traits..."
              aria-label="Search Story Bible entries"
              className="h-9 w-full rounded-lg border border-input bg-background pr-3 pl-9 text-sm text-foreground placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            />
          </div>

          <div
            role="group"
            aria-label="Filter Story Bible entries by type"
            className="flex flex-wrap items-center gap-1.5"
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
                  className="h-8 rounded-lg px-2.5 text-xs"
                >
                  {label} ({counts[value]})
                </Button>
              );
            })}
          </div>
        </div>

        <div className="flex items-center justify-end gap-1.5 border-t border-border/60 pt-2 lg:border-t-0 lg:pt-0">
          <div
            role="group"
            aria-label="Story Bible view mode"
            className="inline-flex items-center rounded-lg border border-border bg-muted/40 p-0.5"
          >
            <button
              type="button"
              onClick={() => setViewMode("grid")}
              aria-pressed={viewMode === "grid"}
              aria-label="Portrait grid view"
              className={cn(
                "inline-flex size-7 items-center justify-center rounded-md text-xs transition-colors",
                viewMode === "grid"
                  ? "bg-card text-primary shadow-2xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <LayoutGrid className="size-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => setViewMode("list")}
              aria-pressed={viewMode === "list"}
              aria-label="Detailed list view"
              className={cn(
                "inline-flex size-7 items-center justify-center rounded-md text-xs transition-colors",
                viewMode === "list"
                  ? "bg-card text-primary shadow-2xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <List className="size-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      {visibleEntries.length === 0 ? (
        <p
          data-testid="story-bible-filter-empty"
          className="rounded-xl border border-dashed border-border bg-card/50 p-8 text-center text-sm text-muted-foreground"
        >
          No {filter === "all" ? "" : `${STORY_BIBLE_ENTRY_LABELS[filter].toLowerCase()} `}entries
          match the selected filter.
        </p>
      ) : (
        <ul
          className={cn(
            viewMode === "grid"
              ? "grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
              : "space-y-3",
          )}
        >
          {visibleEntries.map((entry) => {
            const attributes = summarizeMutableAttributes(entry);
            const missingRequiredReference =
              entry.entry_type === "character" &&
              entry.requires_reference_lock &&
              entry.reference_asset_ids.length === 0;
            const isEditing = editingCodeName === entry.code_name;
            const primaryAssetId = entry.reference_asset_ids[0] ?? null;
            const extraAssetIds = entry.reference_asset_ids.slice(1);
            const coverTone = entryCoverTone(entry.entry_type);
            const CoverIcon = coverTone.Icon;

            return (
              <li
                key={entry.id}
                className={cn(
                  viewMode === "grid" && isEditing && "sm:col-span-2 lg:col-span-3 xl:col-span-4",
                )}
              >
                <article
                  data-testid={`story-bible-entry-${entry.code_name}`}
                  aria-label={`${storyBibleEntryTypeLabel(entry.entry_type)} ${entry.display_name}`}
                  className={cn(
                    "group flex h-full flex-col overflow-hidden rounded-xl border border-border/80 bg-card shadow-xs transition-all hover:border-primary/40 hover:shadow-md",
                    isEditing && "ring-2 ring-primary/25",
                  )}
                >
                  {viewMode === "grid" ? (
                    <>
                      {/* Nucanon-style Visual Portrait Cover */}
                      <div
                        className={cn(
                          "relative aspect-[16/10] w-full overflow-hidden border-b border-border/60 bg-gradient-to-br",
                          coverTone.gradient,
                        )}
                      >
                        {primaryAssetId ? (
                          <ReferenceAssetPreview
                            assetId={primaryAssetId}
                            label={`Reference ${primaryAssetId} for ${entry.display_name}`}
                            variant="cover"
                          />
                        ) : (
                          <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-4 text-center">
                            <span
                              className={cn(
                                "inline-flex size-12 items-center justify-center rounded-2xl border shadow-inner",
                                coverTone.iconBg,
                              )}
                            >
                              <CoverIcon className="size-6" aria-hidden="true" />
                            </span>
                            <span className="font-mono text-[11px] tracking-wider text-slate-300/80 uppercase">
                              {entry.code_name}
                            </span>
                          </div>
                        )}

                        {/* Top Floating Badges */}
                        <div className="pointer-events-none absolute inset-x-2.5 top-2.5 flex items-center justify-between gap-1.5">
                          <span
                            className={cn(
                              "inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold shadow-xs backdrop-blur-md",
                              storyBibleEntryTypeTone(entry.entry_type),
                            )}
                          >
                            {storyBibleEntryTypeLabel(entry.entry_type)}
                          </span>

                          {entry.entry_type === "character" && entry.requires_reference_lock && (
                            <span className="inline-flex items-center gap-1 rounded-md border border-white/20 bg-slate-950/75 px-2 py-0.5 text-[10px] font-medium text-white backdrop-blur-md">
                              <Lock className="size-2.5 text-amber-300" aria-hidden="true" />
                              Ref Lock
                            </span>
                          )}
                        </div>

                        {primaryAssetId && (
                          <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-slate-950/80 via-slate-950/30 to-transparent px-3 pt-4 pb-1.5">
                            <p className="truncate font-mono text-[10px] text-slate-200/90">
                              {primaryAssetId}
                            </p>
                          </div>
                        )}
                      </div>

                      {/* Portrait Card Body */}
                      <div className="flex flex-1 flex-col justify-between p-4">
                        <div className="space-y-2.5">
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <h4 className="truncate text-base font-semibold tracking-tight text-foreground">
                                {entry.display_name}
                              </h4>
                              <code className="mt-0.5 inline-block rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                                {entry.code_name}
                              </code>
                            </div>

                            <Button
                              type="button"
                              size="sm"
                              variant={isEditing ? "secondary" : "outline"}
                              disabled={isEditorBusy}
                              aria-expanded={isEditing}
                              onClick={() => onEdit(entry)}
                              aria-label={`Edit ${entry.display_name} (${entry.code_name})`}
                              className="h-7 shrink-0 px-2.5 text-xs"
                            >
                              <Pencil className="size-3" aria-hidden="true" />
                              Edit
                            </Button>
                          </div>

                          {entry.immutable_traits ? (
                            <p className="line-clamp-3 text-xs leading-relaxed text-muted-foreground">
                              {entry.immutable_traits}
                            </p>
                          ) : (
                            <p className="text-xs italic text-muted-foreground">
                              No immutable traits specified.
                            </p>
                          )}

                          {missingRequiredReference && (
                            <p
                              role="note"
                              className="flex items-start gap-1.5 rounded-lg border border-warning/40 bg-warning/10 p-2 text-[11px] leading-snug text-warning"
                            >
                              <TriangleAlert
                                className="mt-0.5 size-3.5 shrink-0"
                                aria-hidden="true"
                              />
                              <span>
                                Reference lock is required for this entry, but no reference asset is
                                attached yet. Downstream shots referencing {entry.code_name} will be
                                blocked until a reference image is added.
                              </span>
                            </p>
                          )}

                          {attributes.length > 0 && (
                            <dl className="grid gap-1.5 rounded-lg border border-border/60 bg-muted/35 p-2.5 text-[11px]">
                              {attributes.map((item) => (
                                <div
                                  key={item.label}
                                  className="flex items-baseline justify-between gap-2"
                                >
                                  <dt className="font-medium text-muted-foreground">
                                    {item.label}
                                  </dt>
                                  <dd className="truncate font-medium text-foreground">
                                    {item.value}
                                  </dd>
                                </div>
                              ))}
                            </dl>
                          )}
                        </div>

                        {extraAssetIds.length > 0 && (
                          <div className="mt-3 space-y-1.5 border-t border-border/60 pt-2.5">
                            <p className="text-[11px] font-medium text-muted-foreground">
                              Additional references ({extraAssetIds.length})
                            </p>
                            <div className="flex flex-wrap gap-2">
                              {extraAssetIds.map((assetId) => (
                                <div key={assetId} className="space-y-1">
                                  <ReferenceAssetPreview
                                    assetId={assetId}
                                    label={`Reference ${assetId} for ${entry.display_name}`}
                                    className="w-16"
                                  />
                                  <p className="w-16 truncate font-mono text-[10px] text-muted-foreground">
                                    {assetId}
                                  </p>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </>
                  ) : (
                    /* Detailed List View */
                    <div className="p-4">
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
                          Reference lock is required for this entry, but no reference asset is
                          attached yet. Downstream shots referencing {entry.code_name} will be
                          blocked until a reference image is added.
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
                    </div>
                  )}

                  {isEditing && (
                    <div className="border-t border-border/80 bg-muted/20 p-4">
                      {renderEntryEditor?.(entry)}
                    </div>
                  )}
                </article>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

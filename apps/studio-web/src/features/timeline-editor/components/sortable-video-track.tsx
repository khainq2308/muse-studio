import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type Modifier,
} from "@dnd-kit/core";
import {
  SortableContext,
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { AlertTriangle, GripVertical } from "lucide-react";
import type { ClipLayout, DraftClip } from "@/features/timeline-editor/clip-draft";
import { transitionFor } from "@/features/timeline-editor/clip-draft";
import { cn } from "@/lib/utils";

/** Clips only move along the track. */
const lockToHorizontalAxis: Modifier = ({ transform }) => ({ ...transform, y: 0 });

function formatSeconds(value: number): string {
  return `${Number(value.toFixed(2))}s`;
}

function clipLabel(clip: DraftClip | undefined, position: number): string {
  return clip ? `clip ${position} (shot ${clip.shotId ?? "custom"})` : `clip ${position}`;
}

interface SortableVideoTrackProps {
  clips: readonly DraftClip[];
  layouts: readonly ClipLayout[];
  pixelsPerSecond: number;
  selectedKey: string | null;
  invalidKeys: ReadonlySet<string>;
  disabled?: boolean;
  onSelect: (key: string) => void;
  onMove: (from: number, to: number) => void;
}

/** The video track of the draft: clips placed by duration, reorderable with dnd-kit. */
export function SortableVideoTrack({
  clips,
  layouts,
  pixelsPerSecond,
  selectedKey,
  invalidKeys,
  disabled = false,
  onSelect,
  onMove,
}: SortableVideoTrackProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const keys = clips.map((clip) => clip.key);
  const positionOf = (id: string | number) => keys.indexOf(String(id));
  const describe = (id: string | number) => {
    const index = positionOf(id);
    return clipLabel(clips[index], index + 1);
  };
  const announcements: Announcements = {
    onDragStart: ({ active }) => `Picked up ${describe(active.id)}.`,
    onDragOver: ({ active, over }) =>
      over ? `${describe(active.id)} is over position ${positionOf(over.id) + 1}.` : undefined,
    onDragEnd: ({ active, over }) =>
      over
        ? `${describe(active.id)} dropped at position ${positionOf(over.id) + 1}.`
        : `${describe(active.id)} dropped.`,
    onDragCancel: ({ active }) => `Reorder cancelled; ${describe(active.id)} stayed in place.`,
  };

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const from = positionOf(active.id);
    const to = positionOf(over.id);
    if (from >= 0 && to >= 0) onMove(from, to);
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      modifiers={[lockToHorizontalAxis]}
      accessibility={{
        announcements,
        screenReaderInstructions: {
          draggable:
            "To reorder a clip, press Space or Enter on its grip, use the left and right arrow keys to move it, then press Space or Enter again to drop it, or Escape to cancel.",
        },
      }}
      onDragEnd={handleDragEnd}
    >
      <SortableContext items={keys} strategy={horizontalListSortingStrategy} disabled={disabled}>
        <div className="relative h-24" data-testid="timeline-video-track">
          {clips.map((clip, index) => (
            <SortableClip
              key={clip.key}
              clip={clip}
              index={index}
              layout={layouts[index]}
              pixelsPerSecond={pixelsPerSecond}
              isSelected={clip.key === selectedKey}
              isInvalid={invalidKeys.has(clip.key)}
              onSelect={onSelect}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}

interface SortableClipProps {
  clip: DraftClip;
  index: number;
  layout: ClipLayout | undefined;
  pixelsPerSecond: number;
  isSelected: boolean;
  isInvalid: boolean;
  onSelect: (key: string) => void;
}

function SortableClip({
  clip,
  index,
  layout,
  pixelsPerSecond,
  isSelected,
  isInvalid,
  onSelect,
}: SortableClipProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: clip.key });
  const start = layout?.startSeconds ?? 0;
  const effective = layout?.effectiveSeconds ?? 0;
  const transitionType = transitionFor(clip, index);

  return (
    <div
      ref={setNodeRef}
      data-testid="timeline-clip"
      data-clip-key={clip.key}
      className={cn("absolute inset-y-0", isDragging && "z-10 opacity-90")}
      style={{
        left: start * pixelsPerSecond,
        width: Math.max(24, effective * pixelsPerSecond - 4),
        transform: CSS.Translate.toString(transform),
        transition,
      }}
    >
      <button
        type="button"
        aria-pressed={isSelected}
        onClick={() => onSelect(clip.key)}
        className={cn(
          "h-full w-full overflow-hidden rounded-xl border p-2.5 pr-7 text-left transition-colors focus-visible:ring-2 focus-visible:ring-indigo-300 focus-visible:outline-none",
          isInvalid
            ? "border-red-400 bg-red-950/40"
            : isSelected
              ? "border-indigo-400 bg-slate-900 ring-1 ring-indigo-400/50"
              : "border-slate-800 bg-slate-900/80 hover:border-slate-600",
        )}
      >
        <span className="flex items-center justify-between gap-2 text-[11px]">
          <span className="flex items-center gap-1 rounded bg-indigo-500/25 px-1.5 py-0.5 font-mono font-semibold text-indigo-300">
            #{index + 1}
            {isInvalid && (
              <>
                <AlertTriangle className="size-3 text-red-300" aria-hidden="true" />
                <span className="sr-only">has validation errors</span>
              </>
            )}
          </span>
          <span className="font-mono text-slate-400">{formatSeconds(effective)}</span>
        </span>
        <span className="mt-1.5 block truncate font-mono text-xs font-semibold text-white">
          Shot: {clip.shotId ?? "custom"}
        </span>
        <span className="mt-0.5 block truncate font-mono text-[11px] text-slate-400">
          Take: {clip.takeId ?? "unassigned"}
        </span>
        <span className="mt-1 block truncate font-mono text-[10px] text-slate-500">
          {transitionType}
          {clip.gapBeforeSeconds > 0 ? ` · gap ${formatSeconds(clip.gapBeforeSeconds)}` : ""}
        </span>
      </button>
      <button
        type="button"
        ref={setActivatorNodeRef}
        {...attributes}
        {...listeners}
        aria-label={`Reorder clip ${index + 1} (shot ${clip.shotId ?? "custom"})`}
        className="absolute top-1 right-1 flex h-7 w-5 cursor-grab touch-none items-center justify-center rounded text-slate-400 hover:bg-slate-800 hover:text-white focus-visible:ring-2 focus-visible:ring-indigo-300 focus-visible:outline-none active:cursor-grabbing"
      >
        <GripVertical className="size-3.5" aria-hidden="true" />
      </button>
    </div>
  );
}

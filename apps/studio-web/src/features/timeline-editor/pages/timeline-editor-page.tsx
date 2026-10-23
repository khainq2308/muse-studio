import {
  AlertTriangle,
  CheckCircle2,
  Clapperboard,
  Clock,
  Download,
  Eye,
  Film,
  LoaderCircle,
  Lock,
  Maximize2,
  Music,
  Play,
  Scissors,
  SlidersHorizontal,
  Sparkles,
  Subtitles,
  Volume2,
  Wand2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  useAssembleTimeline,
  useDownloadExportedAsset,
  useLaunchTimelineExport,
  useProjectTimeline,
  useTimelineExportStatus,
} from "@/features/timeline-editor/api";
import {
  EXPORT_ASPECT_RATIOS,
  RESOLUTIONS_BY_ASPECT,
  defaultResolutionFor,
  isExportAspectRatio,
  skippedExportFeatures,
  type ExportAspectRatio,
} from "@/features/timeline-editor/export-options";
import { ApiError } from "@/lib/api/client";
import { actionErrorMessage, authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

/** Mirrors TimelineAssembleIn.transition_duration_seconds bounds (0.0 – 3.0) in openapi.json. */
const MAX_TRANSITION_DURATION_SECONDS = 3;
const MIN_CROSS_DISSOLVE_DURATION_SECONDS = 0.2;

function clampCrossDissolveDuration(value: number): number {
  if (!Number.isFinite(value)) return MIN_CROSS_DISSOLVE_DURATION_SECONDS;
  return Math.min(
    MAX_TRANSITION_DURATION_SECONDS,
    Math.max(MIN_CROSS_DISSOLVE_DURATION_SECONDS, value),
  );
}

function formatTimecode(seconds: number, fps = 24): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const mins = Math.floor(safe / 60);
  const secs = Math.floor(safe % 60);
  const frames = Math.floor((safe - Math.floor(safe)) * fps);
  return `00:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}:${String(frames).padStart(2, "0")}`;
}

interface TimelineEditorPageProps {
  projectId: string;
}

export function TimelineEditorPage({ projectId }: TimelineEditorPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const timelineQuery = useProjectTimeline(projectId);
  const assembleMutation = useAssembleTimeline(projectId);
  const launchExportMutation = useLaunchTimelineExport(projectId);
  const downloadAssetMutation = useDownloadExportedAsset(projectId);

  const [sequenceName, setSequenceName] = useState("Master Cut v1");
  const [defaultTransition, setDefaultTransition] = useState("hard_cut");
  const [transitionDuration, setTransitionDuration] = useState(0);
  const [allowMissingShots, setAllowMissingShots] = useState(true);
  const [subtitleFormat, setSubtitleFormat] = useState("srt");
  const [subtitleContent, setSubtitleContent] = useState("");
  const [bgmName, setBgmName] = useState("Main Score Track");
  const [bgmVolume, setBgmVolume] = useState(0.8);
  const [includeBgm, setIncludeBgm] = useState(false);

  const [exportAspectRatio, setExportAspectRatio] = useState<ExportAspectRatio>("16:9");
  const [exportResolution, setExportResolution] = useState(defaultResolutionFor("16:9"));
  const [exportFps, setExportFps] = useState(24);
  const [activeExportId, setActiveExportId] = useState<string | null>(null);
  const [selectedClipIndex, setSelectedClipIndex] = useState(0);
  const [zoomPercent, setZoomPercent] = useState(75);

  const exportStatusQuery = useTimelineExportStatus(projectId, activeExportId);

  const timeline = timelineQuery.data;
  const isNoTimelineYet =
    timelineQuery.isError &&
    timelineQuery.error instanceof ApiError &&
    timelineQuery.error.status === 404;

  const clips = timeline?.manifest.video_clips ?? [];
  const audioTracks = timeline?.manifest.audio_tracks ?? [];
  const warnings = timeline?.warnings ?? [];
  const exportStatus = exportStatusQuery.data;
  const skippedFeatures = timeline ? skippedExportFeatures(timeline) : [];
  const activeClip = clips[selectedClipIndex] ?? clips[0] ?? null;

  return (
    <div data-testid="timeline-editor-page" data-project-id={projectId} className="space-y-4">
      {/* CapCut-Inspired NLE Top Command Bar */}
      <header className="flex flex-col gap-3 rounded-2xl border border-border bg-card px-5 py-3.5 shadow-2xs lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2.5">
            <div className="flex size-9 items-center justify-center rounded-xl border border-primary/20 bg-primary/10 text-primary">
              <Clapperboard className="size-4" aria-hidden="true" />
            </div>
            <div>
              <h2 className="text-base font-bold tracking-tight sm:text-lg">
                Multi-Track Timeline Editor &amp; FFmpeg Export
              </h2>
              <p className="text-[11px] text-muted-foreground">
                Assemble selected shot takes into a sequence, configure transitions and launch
                FFprobe-verified MP4 exports. Audio tracks and subtitles are stored with the
                timeline.
              </p>
            </div>
          </div>

          <div className="hidden h-5 w-px bg-border xl:block" aria-hidden="true" />

          {/* NLE Mode Ribbon */}
          <div className="hidden items-center gap-1 rounded-xl border border-border bg-secondary/45 p-1 text-xs xl:flex">
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-card px-2.5 py-1 font-semibold text-primary shadow-2xs">
              <Film className="size-3.5" aria-hidden="true" />
              Media
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-muted-foreground">
              <Music className="size-3.5" aria-hidden="true" />
              Audio
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-muted-foreground">
              <Subtitles className="size-3.5" aria-hidden="true" />
              Captions
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-muted-foreground">
              <Sparkles className="size-3.5" aria-hidden="true" />
              Transitions
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <span className="rounded-lg border border-border bg-secondary/50 px-2.5 py-1 font-mono text-xs font-medium text-foreground">
            {timeline ? timeline.name : sequenceName}
          </span>

          <Button
            type="button"
            disabled={assembleMutation.isPending}
            onClick={() => {
              assembleMutation.mutate({
                name: sequenceName.trim() || "Master Cut v1",
                default_transition: defaultTransition,
                transition_duration_seconds:
                  defaultTransition === "cross_dissolve"
                    ? clampCrossDissolveDuration(transitionDuration)
                    : 0,
                allow_missing_shots: allowMissingShots,
                subtitle_format: subtitleFormat,
                subtitle_content: subtitleContent,
                audio_tracks: includeBgm
                  ? [
                      {
                        name: bgmName.trim() || "BGM Track",
                        track_type: "music",
                        volume: bgmVolume,
                        fade_in_seconds: 0.5,
                        fade_out_seconds: 1.0,
                        muted: false,
                        asset_id: null,
                      },
                    ]
                  : [],
              });
            }}
          >
            {assembleMutation.isPending ? (
              <>
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                Assembling sequence...
              </>
            ) : (
              <>
                <Wand2 className="size-4" aria-hidden="true" />
                Assemble / Refresh Timeline
              </>
            )}
          </Button>
        </div>
      </header>

      {assembleMutation.isError && (
        <p
          role="alert"
          className="rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"
        >
          {actionErrorMessage(assembleMutation.error, activeAdminToken, "assemble the timeline")}
        </p>
      )}

      {/* Upper 3-Quadrant NLE Workspace: [Left Media & Config] [Center Player] [Right Details & Export] */}
      <div className="grid gap-4 lg:grid-cols-12">
        {/* Top-Left Quadrant: Media Bin & Sequence Assembly Parameters */}
        <section
          aria-label="Timeline assembly parameters"
          className="flex flex-col justify-between space-y-3.5 rounded-2xl border border-border bg-card p-4 shadow-2xs lg:col-span-4"
        >
          <div className="space-y-3.5">
            <div className="flex items-center justify-between border-b border-border pb-2.5">
              <div className="flex items-center gap-2">
                <SlidersHorizontal className="size-4 text-primary" aria-hidden="true" />
                <h3 className="text-sm font-bold">Sequence Assembly Configuration</h3>
              </div>
              <span className="rounded bg-secondary px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
                {clips.length} {clips.length === 1 ? "clip" : "clips"}
              </span>
            </div>

            {/* Thumbnail Bin Preview (CapCut Top-Left Media Grid) */}
            {clips.length > 0 && (
              <div className="grid grid-cols-2 gap-2">
                {clips.slice(0, 4).map((clip, idx) => {
                  const isSelected = selectedClipIndex === idx;
                  return (
                    <button
                      key={`bin-${clip.order_index}-${clip.shot_id ?? "clip"}`}
                      type="button"
                      onClick={() => setSelectedClipIndex(idx)}
                      className={cn(
                        "group relative aspect-video overflow-hidden rounded-xl border bg-slate-950 p-2 text-left text-slate-100 transition-all",
                        isSelected
                          ? "border-primary ring-2 ring-primary/30"
                          : "border-slate-800 hover:border-slate-700",
                      )}
                    >
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="rounded bg-primary/80 px-1.5 py-0.5 font-mono font-bold text-white">
                          Clip #{clip.order_index + 1}
                        </span>
                        <span className="rounded bg-black/70 px-1.5 py-0.5 font-mono text-slate-200">
                          00:0{Math.max(1, Math.round(clip.effective_duration_seconds))}
                        </span>
                      </div>
                      <p className="mt-4 truncate font-mono text-[11px] font-medium text-slate-200">
                        {clip.shot_id ?? "custom"}
                      </p>
                    </button>
                  );
                })}
              </div>
            )}

            <div className="grid gap-2.5 sm:grid-cols-2">
              <label className="space-y-1 text-xs font-medium sm:col-span-2">
                <span>Sequence name</span>
                <input
                  value={sequenceName}
                  onChange={(event) => setSequenceName(event.target.value)}
                  className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </label>

              <label className="space-y-1 text-xs font-medium">
                <span>Default transition</span>
                <select
                  value={defaultTransition}
                  onChange={(event) => {
                    setDefaultTransition(event.target.value);
                    if (event.target.value === "cross_dissolve" && transitionDuration === 0) {
                      setTransitionDuration(0.5);
                    }
                  }}
                  className="h-8 w-full rounded-lg border border-input bg-background px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="hard_cut">hard_cut (Supported in FFmpeg concat)</option>
                  <option value="cross_dissolve">cross_dissolve</option>
                </select>
              </label>

              <label className="space-y-1 text-xs font-medium">
                <span>Transition duration (s)</span>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={MAX_TRANSITION_DURATION_SECONDS}
                  disabled={defaultTransition === "hard_cut"}
                  value={transitionDuration}
                  onChange={(event) => setTransitionDuration(Number(event.target.value))}
                  className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-xs outline-none disabled:opacity-50"
                />
              </label>
            </div>

            <div className="space-y-2 rounded-xl border border-border bg-secondary/25 p-2.5 text-xs">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={allowMissingShots}
                  onChange={(event) => setAllowMissingShots(event.target.checked)}
                  className="mt-0.5 rounded border-input"
                />
                <span>Allow assembling when some shots do not yet have a selected take</span>
              </label>

              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={includeBgm}
                  onChange={(event) => setIncludeBgm(event.target.checked)}
                  className="mt-0.5 rounded border-input"
                />
                <span>Reserve a background music track (metadata only)</span>
              </label>
            </div>

            {includeBgm && (
              <p className="text-xs text-warning">
                No audio file can be attached yet, and MP4 export does not mix audio tracks. This
                only records a track entry in the timeline manifest.
              </p>
            )}

            {includeBgm && (
              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1 text-xs font-medium">
                  <span>BGM track name</span>
                  <input
                    value={bgmName}
                    onChange={(event) => setBgmName(event.target.value)}
                    className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-xs"
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  <span>BGM volume (0.0 – 1.0)</span>
                  <input
                    type="number"
                    step="0.1"
                    min={0}
                    max={1}
                    value={bgmVolume}
                    onChange={(event) => setBgmVolume(Number(event.target.value))}
                    className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-xs"
                  />
                </label>
              </div>
            )}

            <div className="grid gap-2 sm:grid-cols-3">
              <label className="space-y-1 text-xs font-medium">
                <span className="flex items-center gap-1">
                  <Subtitles className="size-3 text-primary" aria-hidden="true" />
                  Subtitle format
                </span>
                <select
                  value={subtitleFormat}
                  onChange={(event) => setSubtitleFormat(event.target.value)}
                  className="h-8 w-full rounded-lg border border-input bg-background px-2 text-xs"
                >
                  <option value="srt">SRT</option>
                  <option value="vtt">WebVTT</option>
                </select>
              </label>
              <label className="space-y-1 text-xs font-medium sm:col-span-2">
                <span>Subtitle cues (optional SRT / VTT content)</span>
                <textarea
                  rows={3}
                  value={subtitleContent}
                  onChange={(event) => setSubtitleContent(event.target.value)}
                  placeholder={"1\n00:00:00,000 --> 00:00:04,000\nOpening scene dialogue..."}
                  className="w-full rounded-lg border border-input bg-background px-2.5 py-1.5 font-mono text-xs"
                />
              </label>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Subtitles are saved with the timeline; MP4 export does not burn in or embed them yet.
            </p>
          </div>
        </section>

        {/* Top-Center Quadrant: CapCut Program Monitor / Player */}
        <section
          aria-label="Program monitor"
          className="flex flex-col justify-between rounded-2xl border border-border bg-card p-4 shadow-2xs lg:col-span-5"
        >
          <div className="flex items-center justify-between border-b border-border pb-2.5">
            <div className="flex items-center gap-2">
              <Play className="size-4 text-primary" aria-hidden="true" />
              <h3 className="text-sm font-bold">Player · Program Monitor</h3>
            </div>
            <span className="rounded bg-secondary px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
              {timeline ? `${timeline.resolution} · ${timeline.fps}fps` : "1920x1080 · 24fps"}
            </span>
          </div>

          {/* 16:9 Cinema Monitor Canvas */}
          <div className="relative my-3 flex aspect-video w-full flex-col justify-between overflow-hidden rounded-xl border border-slate-800 bg-gradient-to-br from-slate-950 via-slate-900 to-indigo-950 p-4 text-slate-100 shadow-inner">
            <div
              className="pointer-events-none absolute inset-4 rounded border border-white/10"
              aria-hidden="true"
            />

            <div className="relative flex items-center justify-between text-xs">
              <span className="rounded bg-black/60 px-2 py-0.5 font-mono text-[11px] text-indigo-300">
                {activeClip
                  ? `CLIP #${activeClip.order_index + 1} · ${activeClip.shot_id ?? "custom"}`
                  : "STANDBY MONITOR"}
              </span>
              <span className="rounded bg-black/60 px-2 py-0.5 font-mono text-[11px] text-emerald-300">
                REC. 709 SDR
              </span>
            </div>

            <div className="relative my-auto text-center">
              <Film className="mx-auto size-8 text-indigo-400/80" aria-hidden="true" />
              <p className="mt-2 text-sm font-semibold text-white">
                {activeClip
                  ? `Shot ${activeClip.shot_id ?? "clip"} — Take ${activeClip.take_id ?? "unassigned"}`
                  : timeline
                    ? timeline.name
                    : "Assemble sequence to preview master cut"}
              </p>
              <p className="mt-0.5 font-mono text-xs text-slate-400">
                {activeClip
                  ? `Start ${Number(activeClip.start_time_seconds.toFixed(1))}s · Duration ${Number(activeClip.effective_duration_seconds.toFixed(2))}s · ${activeClip.transition_type}`
                  : "Multi-track program output"}
              </p>
            </div>

            {/* Simulated Subtitle Overlay in Player (like CapCut reference) */}
            <div className="relative mx-auto max-w-[85%] rounded bg-black/75 px-3 py-1 text-center text-xs font-medium tracking-wide text-white shadow-xs">
              {subtitleContent.trim()
                ? subtitleContent.trim().split(/\r?\n/).slice(-1)[0]
                : `[${timeline ? timeline.name : sequenceName}: Opening Sequence]`}
            </div>
          </div>

          {/* Transport Controls Bar */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2.5 text-xs">
            <div className="font-mono text-xs">
              <span className="font-semibold text-primary">
                {formatTimecode(activeClip?.start_time_seconds ?? 0, timeline?.fps ?? exportFps)}
              </span>
              <span className="mx-1 text-muted-foreground">/</span>
              <span className="text-muted-foreground">
                {formatTimecode(timeline?.total_duration_seconds ?? 0, timeline?.fps ?? exportFps)}
              </span>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="rounded border border-border bg-secondary/50 px-2 py-0.5 font-mono text-[11px] font-medium">
                {timeline?.aspect_ratio ?? exportAspectRatio}
              </span>
              <span className="inline-flex items-center gap-1 rounded border border-border bg-secondary/50 px-2 py-0.5 text-[11px] text-muted-foreground">
                <Maximize2 className="size-3" aria-hidden="true" />
                Full
              </span>
            </div>
          </div>
        </section>

        {/* Top-Right Quadrant: CapCut Details & Async FFmpeg Export Inspector */}
        <div className="space-y-3.5 lg:col-span-3">
          <section
            aria-label="FFmpeg MP4 export"
            className="space-y-3.5 rounded-2xl border border-border bg-card p-4 shadow-2xs"
          >
            <div className="flex items-center justify-between border-b border-border pb-2.5">
              <h3 className="text-sm font-bold">Async FFmpeg MP4 Render</h3>
              <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-primary">
                FFprobe Gate
              </span>
            </div>

            {/* CapCut Details Key-Value Specification Table */}
            <dl className="space-y-1.5 rounded-xl bg-secondary/35 p-2.5 text-xs">
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Name:</dt>
                <dd className="truncate font-semibold text-foreground">
                  {timeline ? timeline.name : sequenceName}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Color space:</dt>
                <dd className="font-mono text-[11px] text-foreground">Rec. 709 SDR</dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Duration:</dt>
                <dd className="font-mono text-[11px] text-foreground">
                  {timeline ? `${Number(timeline.total_duration_seconds.toFixed(2))}s` : "0.00s"}
                </dd>
              </div>
            </dl>

            <div className="grid gap-2.5">
              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1 text-xs font-medium">
                  <span>Aspect ratio</span>
                  <select
                    value={exportAspectRatio}
                    onChange={(event) => {
                      const next = event.target.value;
                      if (!isExportAspectRatio(next)) return;
                      setExportAspectRatio(next);
                      setExportResolution(defaultResolutionFor(next));
                    }}
                    className="h-8 w-full rounded-lg border border-input bg-background px-2 text-xs"
                  >
                    {EXPORT_ASPECT_RATIOS.map((aspect) => (
                      <option key={aspect} value={aspect}>
                        {aspect}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="space-y-1 text-xs font-medium">
                  <span>Resolution</span>
                  <select
                    value={exportResolution}
                    onChange={(event) => setExportResolution(event.target.value)}
                    className="h-8 w-full rounded-lg border border-input bg-background px-2 text-xs"
                  >
                    {RESOLUTIONS_BY_ASPECT[exportAspectRatio].map((resolution) => (
                      <option key={resolution} value={resolution}>
                        {resolution}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1 text-xs font-medium">
                  <span>FPS</span>
                  <select
                    value={exportFps}
                    onChange={(event) => setExportFps(Number(event.target.value))}
                    className="h-8 w-full rounded-lg border border-input bg-background px-2 text-xs"
                  >
                    <option value={24}>24 fps</option>
                    <option value={25}>25 fps</option>
                    <option value={30}>30 fps</option>
                  </select>
                </label>
              </div>
            </div>

            {skippedFeatures.length > 0 && (
              <div
                data-testid="export-skipped-features"
                className="rounded-lg border border-warning/35 bg-warning/10 p-2.5 text-xs"
              >
                <p className="font-semibold text-warning">This export will not include:</p>
                <ul className="mt-1 list-inside list-disc">
                  {skippedFeatures.map((feature) => (
                    <li key={feature}>{feature}</li>
                  ))}
                </ul>
              </div>
            )}

            {launchExportMutation.isError && (
              <p role="alert" className="text-xs text-destructive">
                {actionErrorMessage(
                  launchExportMutation.error,
                  activeAdminToken,
                  "launch the timeline export",
                )}
              </p>
            )}

            <Button
              type="button"
              className="w-full"
              disabled={!timeline || launchExportMutation.isPending}
              onClick={() => {
                launchExportMutation.mutate(
                  {
                    sequence_id: timeline?.sequence_id ?? null,
                    resolution: exportResolution,
                    aspect_ratio: exportAspectRatio,
                    fps: exportFps,
                  },
                  {
                    onSuccess: (launched) => setActiveExportId(launched.export_id),
                  },
                );
              }}
            >
              {launchExportMutation.isPending ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  Queueing FFmpeg export...
                </>
              ) : (
                <>
                  <Play className="size-4" aria-hidden="true" />
                  Launch Verified MP4 Export
                </>
              )}
            </Button>
          </section>

          {activeExportId && exportStatusQuery.isPending && (
            <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
              <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
              Loading export job status...
            </p>
          )}

          {activeExportId && exportStatusQuery.isError && (
            <div
              role="alert"
              className="space-y-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"
            >
              <p>
                {authErrorMessage(exportStatusQuery.error, activeAdminToken, "export job status")}
                {exportStatus ? " Status shown below may be out of date." : ""}
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={exportStatusQuery.isFetching}
                onClick={() => void exportStatusQuery.refetch()}
              >
                Retry status check
              </Button>
            </div>
          )}

          {exportStatus && (
            <section
              aria-label="Export job status"
              className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-2xs"
            >
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">Export Job Status</h3>
                <span
                  className={cn(
                    "rounded-full border px-2.5 py-0.5 font-mono text-xs font-semibold",
                    exportStatus.state === "succeeded"
                      ? "border-success/30 bg-success/10 text-success"
                      : exportStatus.state === "failed"
                        ? "border-destructive/30 bg-destructive/10 text-destructive"
                        : "border-primary/30 bg-primary/10 text-primary",
                  )}
                >
                  {exportStatus.state}
                </span>
              </div>

              <dl className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <dt className="text-muted-foreground">Export ID</dt>
                  <dd className="truncate font-mono">{exportStatus.export_id}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Codecs</dt>
                  <dd className="font-mono">
                    {exportStatus.video_codec} / {exportStatus.audio_codec}
                  </dd>
                </div>
              </dl>

              {exportStatus.message && (
                <p className="rounded bg-secondary p-2 text-xs text-foreground">
                  {exportStatus.message}
                </p>
              )}

              {exportStatus.unsupported_features.length > 0 && (
                <div className="rounded-lg border border-warning/35 bg-warning/10 p-2.5 text-xs">
                  <p className="font-semibold text-warning">
                    Unsupported Timeline Features Skipped by Concat Renderer:
                  </p>
                  <p className="mt-1 font-mono text-[11px]">
                    {exportStatus.unsupported_features.join(", ")}
                  </p>
                </div>
              )}

              {exportStatus.state === "succeeded" && exportStatus.output_asset_id && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-full"
                  disabled={downloadAssetMutation.isPending}
                  onClick={() =>
                    downloadAssetMutation.mutate({
                      assetId: exportStatus.output_asset_id!,
                      filename: `${projectId}-export-${exportStatus.export_id}.mp4`,
                    })
                  }
                >
                  <Download className="size-4" aria-hidden="true" />
                  Download Verified MP4 ({exportStatus.output_asset_id})
                </Button>
              )}

              {downloadAssetMutation.isError && (
                <p role="alert" className="text-xs text-destructive">
                  {actionErrorMessage(
                    downloadAssetMutation.error,
                    activeAdminToken,
                    "download the exported MP4",
                  )}
                </p>
              )}

              {exportStatus.ffprobe_verification && (
                <div className="rounded-lg border border-success/30 bg-success/5 p-2.5 text-xs">
                  <p className="flex items-center gap-1 font-semibold text-success">
                    <CheckCircle2 className="size-3.5" aria-hidden="true" />
                    FFprobe Verification Passed
                  </p>
                </div>
              )}
            </section>
          )}
        </div>
      </div>

      {/* Bottom Full-Width Multi-Track NLE Timeline & Timecode Ruler (CapCut Bottom Pane) */}
      <section
        aria-label="Multi-track timeline ruler"
        className="space-y-3 overflow-hidden rounded-2xl border border-slate-800 bg-slate-950 p-4 text-slate-100 shadow-sm"
      >
        {/* Timeline Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1 text-xs text-slate-300">
              <Scissors className="size-3.5 text-indigo-400" aria-hidden="true" />
              <span>NLE Tracks</span>
            </div>

            <div className="flex items-center gap-2">
              <Film className="size-4 text-indigo-400" aria-hidden="true" />
              <h3 className="text-sm font-semibold text-white">
                {timeline ? timeline.name : "NLE Sequence Tracks"}
              </h3>
              {timeline && (
                <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs text-slate-300">
                  {timeline.resolution} · {timeline.fps} fps · {timeline.aspect_ratio}
                </span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4">
            {timeline && (
              <span className="inline-flex items-center gap-1 font-mono text-xs font-semibold text-indigo-300">
                <Clock className="size-3.5" aria-hidden="true" />
                Total: {Number(timeline.total_duration_seconds.toFixed(2))}s ({clips.length} clips)
              </span>
            )}

            {/* Zoom Slider */}
            <div className="hidden items-center gap-2 text-slate-400 sm:flex">
              <button
                type="button"
                aria-label="Zoom out timeline"
                onClick={() => setZoomPercent((z) => Math.max(25, z - 25))}
                className="rounded p-1 hover:bg-slate-800 hover:text-white"
              >
                <ZoomOut className="size-3.5" aria-hidden="true" />
              </button>
              <div className="h-1.5 w-20 rounded-full bg-slate-800">
                <div
                  className="h-full rounded-full bg-indigo-400"
                  style={{ width: `${zoomPercent}%` }}
                />
              </div>
              <button
                type="button"
                aria-label="Zoom in timeline"
                onClick={() => setZoomPercent((z) => Math.min(100, z + 25))}
                className="rounded p-1 hover:bg-slate-800 hover:text-white"
              >
                <ZoomIn className="size-3.5" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>

        {timelineQuery.isPending ? (
          <p role="status" className="flex items-center gap-2 py-6 text-sm text-slate-300">
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            Loading project timeline...
          </p>
        ) : timelineQuery.isError && !isNoTimelineYet ? (
          <p role="alert" className="text-xs text-red-300">
            {authErrorMessage(timelineQuery.error, activeAdminToken, "project timeline")}
          </p>
        ) : !timeline ? (
          <div className="rounded-xl border border-dashed border-slate-800 bg-slate-900/50 px-4 py-8 text-center">
            <p className="text-sm font-semibold text-white">
              No timeline assembled for this project yet
            </p>
            <p className="mt-1 text-xs text-slate-400">
              Click “Assemble / Refresh Timeline” above to build a sequence from your project shots
              and selected takes.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {warnings.length > 0 && (
              <div className="space-y-1.5 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3">
                <p className="flex items-center gap-1.5 text-xs font-semibold text-amber-300">
                  <AlertTriangle className="size-3.5" aria-hidden="true" />
                  Timeline Assembly Warnings ({warnings.length})
                </p>
                <ul className="space-y-1 text-xs text-amber-100">
                  {warnings.map((w, idx) => (
                    <li key={`${w.code}-${idx}`}>
                      <span className="font-mono font-semibold">[{w.code}]</span> {w.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Timecode Ruler */}
            <div className="flex items-center border-b border-slate-800 pb-2 font-mono text-[10px] text-slate-400">
              <div className="w-28 shrink-0 pr-2 text-slate-500">TIMECODE</div>
              <div className="grid flex-1 grid-cols-6">
                <span>00:00</span>
                <span>00:05</span>
                <span>00:10</span>
                <span>00:15</span>
                <span>00:20</span>
                <span>00:25</span>
              </div>
            </div>

            {/* Track TI: Captions / Subtitles Lane */}
            <div className="flex items-center gap-2">
              <div className="flex w-28 shrink-0 items-center justify-between rounded-lg bg-slate-900 px-2.5 py-2 text-[11px] font-mono text-slate-300">
                <span className="font-bold text-amber-400">TI</span>
                <div className="flex items-center gap-1.5 text-slate-500">
                  <Lock className="size-3" aria-hidden="true" />
                  <Eye className="size-3" aria-hidden="true" />
                </div>
              </div>
              <div className="flex flex-1 gap-1.5 overflow-x-auto">
                {(clips.length > 0 ? clips : [{ order_index: 0, shot_id: "caption" }]).map(
                  (item, idx) => (
                    <div
                      key={`caption-${idx}`}
                      className="min-w-[160px] flex-1 truncate rounded-md border border-amber-500/40 bg-amber-700/35 px-2.5 py-1 text-[11px] font-medium text-amber-200"
                    >
                      [Cue #{idx + 1}] {subtitleFormat.toUpperCase()} Subtitle Block (
                      {item.shot_id ?? "scene"})
                    </div>
                  ),
                )}
              </div>
            </div>

            {/* Track V1: Main Video Filmstrip Lane */}
            <div className="flex items-stretch gap-2">
              <div className="flex w-28 shrink-0 flex-col justify-between rounded-lg bg-slate-900 px-2.5 py-2.5 text-[11px] font-mono text-slate-200">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-indigo-400">V1</span>
                  <span className="rounded bg-indigo-500/20 px-1 text-[10px] text-indigo-300">
                    {clips.length}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 text-slate-400">
                  <Lock className="size-3" aria-hidden="true" />
                  <Eye className="size-3" aria-hidden="true" />
                  <Volume2 className="size-3" aria-hidden="true" />
                </div>
              </div>

              <div className="flex-1 overflow-x-auto">
                {clips.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-slate-800 p-4 text-xs text-slate-400">
                    No video clips in this sequence yet. Generate and select shot takes in Shot
                    Board, then re-assemble.
                  </p>
                ) : (
                  <div className="flex gap-2 pb-1">
                    {clips.map((clip, idx) => {
                      const isSelected = selectedClipIndex === idx;
                      return (
                        <div
                          key={`${clip.order_index}-${clip.shot_id ?? "clip"}`}
                          onClick={() => setSelectedClipIndex(idx)}
                          className={cn(
                            "min-w-[210px] flex-1 cursor-pointer rounded-xl border p-3 transition-all",
                            isSelected
                              ? "border-indigo-400 bg-slate-900 ring-1 ring-indigo-400/50"
                              : "border-slate-800 bg-slate-900/80 hover:border-slate-700",
                          )}
                        >
                          <div className="flex items-center justify-between text-[11px]">
                            <span className="rounded bg-indigo-500/25 px-1.5 py-0.5 font-mono font-semibold text-indigo-300">
                              #{clip.order_index + 1}
                            </span>
                            <span className="font-mono text-slate-400">
                              {Number(clip.start_time_seconds.toFixed(1))}s
                            </span>
                          </div>
                          <p className="mt-1.5 truncate font-mono text-xs font-semibold text-white">
                            Shot: {clip.shot_id ?? "custom"}
                          </p>
                          <p className="mt-0.5 truncate font-mono text-[11px] text-slate-400">
                            Take: {clip.take_id ?? "unassigned"}
                          </p>
                          <div className="mt-2 flex items-center justify-between border-t border-slate-800 pt-1.5 text-[11px] text-slate-300">
                            <span>{Number(clip.effective_duration_seconds.toFixed(2))}s</span>
                            <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px]">
                              {clip.transition_type}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>

            {/* Track A1 / A2: Audio & BGM Waveform Lane */}
            <div className="flex items-center gap-2">
              <div className="flex w-28 shrink-0 items-center justify-between rounded-lg bg-slate-900 px-2.5 py-2 text-[11px] font-mono text-slate-300">
                <span className="font-bold text-cyan-400">A1 · BGM</span>
                <div className="flex items-center gap-1.5 text-slate-400">
                  <Lock className="size-3" aria-hidden="true" />
                  <Volume2 className="size-3" aria-hidden="true" />
                </div>
              </div>

              <div className="flex-1">
                {audioTracks.length === 0 ? (
                  <div className="rounded-lg border border-cyan-500/30 bg-cyan-950/40 px-3 py-2 text-xs text-cyan-200">
                    No auxiliary audio tracks attached · Default shot audio stream active
                  </div>
                ) : (
                  <div className="grid gap-2 sm:grid-cols-2">
                    {audioTracks.map((track, idx) => (
                      <div
                        key={`${track.name}-${idx}`}
                        className="flex items-center justify-between rounded-lg border border-cyan-500/40 bg-cyan-950/50 px-3 py-2 text-xs text-cyan-100"
                      >
                        <div>
                          <p className="font-semibold">{track.name}</p>
                          <p className="font-mono text-[11px] text-cyan-300/80">
                            {track.track_type} · fade in {track.fade_in_seconds}s / out{" "}
                            {track.fade_out_seconds}s
                          </p>
                        </div>
                        <span className="font-mono font-semibold text-cyan-300">
                          {track.muted ? "Muted" : `Vol ${Math.round(track.volume * 100)}%`}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export default TimelineEditorPage;

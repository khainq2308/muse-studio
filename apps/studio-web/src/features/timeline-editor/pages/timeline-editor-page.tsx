import {
  AlertTriangle,
  CheckCircle2,
  Clapperboard,
  Clock,
  Download,
  Film,
  Info,
  LoaderCircle,
  Music,
  Play,
  SlidersHorizontal,
  Subtitles,
  Wand2,
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
import {
  formatTimecode,
  pixelsPerSecondFor,
  rulerTicks,
} from "@/features/timeline-editor/timecode";
import { ApiError } from "@/lib/api/client";
import { actionErrorMessage, authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import { cn } from "@/lib/utils";

/** Mirrors TimelineAssembleIn.transition_duration_seconds bounds (0.0 – 3.0) in openapi.json. */
const MAX_TRANSITION_DURATION_SECONDS = 3;
const MIN_CROSS_DISSOLVE_DURATION_SECONDS = 0.2;
/** Room after the last clip so the final ruler label is not clipped by the scroll edge. */
const TRACK_END_PADDING_PX = 64;

function clampCrossDissolveDuration(value: number): number {
  if (!Number.isFinite(value)) return MIN_CROSS_DISSOLVE_DURATION_SECONDS;
  return Math.min(
    MAX_TRANSITION_DURATION_SECONDS,
    Math.max(MIN_CROSS_DISSOLVE_DURATION_SECONDS, value),
  );
}

function formatSeconds(value: number): string {
  return `${Number(value.toFixed(2))}s`;
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
  // The selection is scoped to the sequence it was made in, so a re-assembled sequence with a
  // new id starts again from its first clip instead of pointing at a stale index.
  const [clipSelection, setClipSelection] = useState<{
    sequenceId: string | null;
    index: number;
  }>({ sequenceId: null, index: 0 });

  const exportStatusQuery = useTimelineExportStatus(projectId, activeExportId);

  const timeline = timelineQuery.data;
  const isNoTimelineYet =
    timelineQuery.isError &&
    timelineQuery.error instanceof ApiError &&
    timelineQuery.error.status === 404;

  const sequenceId = timeline?.sequence_id ?? null;
  const clips = timeline?.manifest.video_clips ?? [];
  const audioTracks = timeline?.manifest.audio_tracks ?? [];
  const warnings = timeline?.warnings ?? [];
  const exportStatus = exportStatusQuery.data;
  const skippedFeatures = timeline ? skippedExportFeatures(timeline) : [];
  const selectedClipIndex = clipSelection.sequenceId === sequenceId ? clipSelection.index : 0;
  const activeClip = clips[selectedClipIndex] ?? clips[0] ?? null;
  const sequenceFormat = timeline ? `${timeline.resolution} · ${timeline.fps} fps` : "—";

  const pixelsPerSecond = pixelsPerSecondFor(clips.map((clip) => clip.effective_duration_seconds));
  const sequenceEndSeconds = Math.max(
    timeline?.total_duration_seconds ?? 0,
    ...clips.map((clip) => clip.start_time_seconds + clip.effective_duration_seconds),
  );
  const ticks = rulerTicks(sequenceEndSeconds, pixelsPerSecond);
  const trackWidthPx = Math.ceil(sequenceEndSeconds * pixelsPerSecond) + TRACK_END_PADDING_PX;

  return (
    <div data-testid="timeline-editor-page" data-project-id={projectId} className="space-y-4">
      <header className="flex flex-col gap-3 rounded-2xl border border-border bg-card px-5 py-3.5 shadow-2xs lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl border border-primary/20 bg-primary/10 text-primary">
            <Clapperboard className="size-4" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-base font-bold tracking-tight sm:text-lg">
              Timeline Assembly &amp; Export
            </h2>
            <p className="text-[11px] text-muted-foreground">
              Assemble selected shot takes into a sequence, choose transitions and launch
              FFprobe-verified MP4 exports. Clip trim and reorder editing is not available yet.
            </p>
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

      <div className="grid items-start gap-4 lg:grid-cols-2 xl:grid-cols-12">
        <section
          aria-label="Timeline assembly parameters"
          className="space-y-3.5 rounded-2xl border border-border bg-card p-4 shadow-2xs xl:col-span-4"
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
                  <option value="hard_cut">Hard cut</option>
                  <option value="cross_dissolve">Cross dissolve</option>
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
              <p className="text-[11px] text-muted-foreground sm:col-span-2">
                Hard cuts join clips back to back. Cross dissolve blends overlapping clips with an
                FFmpeg fade (0.2 – 3 s).
              </p>
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

        <section
          aria-label="Selected clip"
          className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-2xs xl:col-span-3"
        >
          <div className="flex items-center justify-between gap-2 border-b border-border pb-2.5">
            <div className="flex items-center gap-2">
              <Film className="size-4 text-primary" aria-hidden="true" />
              <h3 className="text-sm font-bold">Selected clip</h3>
            </div>
            <span className="rounded bg-secondary px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
              {sequenceFormat}
            </span>
          </div>

          {activeClip ? (
            <dl className="space-y-1.5 text-xs">
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Clip</dt>
                <dd className="font-mono font-semibold">
                  #{activeClip.order_index + 1} of {clips.length}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Shot</dt>
                <dd className="truncate font-mono">{activeClip.shot_id ?? "—"}</dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Take</dt>
                <dd className="truncate font-mono">{activeClip.take_id ?? "unassigned"}</dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Starts at</dt>
                <dd className="font-mono">
                  {formatTimecode(activeClip.start_time_seconds, timeline?.fps)}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Duration</dt>
                <dd className="font-mono">
                  {formatSeconds(activeClip.effective_duration_seconds)}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Transition</dt>
                <dd className="font-mono">
                  {activeClip.transition_type}
                  {activeClip.transition_type === "cross_dissolve"
                    ? ` · ${formatSeconds(activeClip.transition_duration_seconds)}`
                    : ""}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="text-xs text-muted-foreground">
              {timeline
                ? "This sequence has no clips yet."
                : "Assemble a timeline to inspect its clips."}
            </p>
          )}

          <p className="flex items-start gap-1.5 border-t border-border pt-2.5 text-[11px] text-muted-foreground">
            <Info className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
            No in-browser preview. Export and download the verified MP4 to review the cut.
          </p>
        </section>

        <div className="space-y-3.5 lg:col-span-2 xl:col-span-5">
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

            <dl className="space-y-1.5 rounded-xl bg-secondary/35 p-2.5 text-xs">
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Name:</dt>
                <dd className="truncate font-semibold text-foreground">
                  {timeline ? timeline.name : sequenceName}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Sequence format:</dt>
                <dd className="font-mono text-[11px] text-foreground">{sequenceFormat}</dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">Duration:</dt>
                <dd className="font-mono text-[11px] text-foreground">
                  {timeline ? `${Number(timeline.total_duration_seconds.toFixed(2))}s` : "—"}
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

      <section
        aria-label="Timeline tracks"
        className="space-y-3 rounded-2xl border border-slate-800 bg-slate-950 p-4 text-slate-100 shadow-sm"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
          <div className="flex flex-wrap items-center gap-2">
            <Film className="size-4 text-indigo-400" aria-hidden="true" />
            <h3 className="text-sm font-semibold text-white">
              {timeline ? timeline.name : "Sequence"}
            </h3>
            {timeline && (
              <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs text-slate-300">
                {timeline.resolution} · {timeline.fps} fps · {timeline.aspect_ratio}
              </span>
            )}
          </div>

          {timeline && (
            <span className="inline-flex items-center gap-1 font-mono text-xs font-semibold text-indigo-300">
              <Clock className="size-3.5" aria-hidden="true" />
              Total {formatTimecode(timeline.total_duration_seconds)} ({clips.length}{" "}
              {clips.length === 1 ? "clip" : "clips"})
            </span>
          )}
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

            {clips.length === 0 ? (
              <p className="rounded-lg border border-dashed border-slate-800 p-4 text-xs text-slate-400">
                No video clips in this sequence yet. Generate and select shot takes in Shot Board,
                then re-assemble.
              </p>
            ) : (
              <>
                <p className="text-[11px] text-slate-400">
                  Read-only view: clip widths are proportional to duration. Select a clip to see its
                  details.
                  <span className="sm:hidden"> Scroll sideways to see the whole sequence.</span>
                </p>

                <div className="flex gap-2">
                  <div
                    className="w-12 shrink-0 space-y-2 font-mono text-[10px] text-slate-400 sm:w-20"
                    aria-hidden="true"
                  >
                    <div className="flex h-5 items-end pb-1">Time</div>
                    <div className="flex h-24 items-center rounded-lg bg-slate-900 px-2 font-bold text-indigo-400">
                      Video
                    </div>
                    {timeline.manifest.has_subtitles && (
                      <div className="flex h-7 items-center rounded-lg bg-slate-900 px-2 font-bold text-amber-400">
                        Subs
                      </div>
                    )}
                  </div>

                  <div
                    role="group"
                    aria-label="Sequence tracks"
                    tabIndex={0}
                    className="min-w-0 flex-1 overflow-x-auto rounded-lg pb-2 outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                  >
                    <div className="relative space-y-2" style={{ width: trackWidthPx }}>
                      <ol
                        aria-label="Timecode ruler"
                        className="relative h-5 border-b border-slate-800 font-mono text-[10px] text-slate-400"
                      >
                        {ticks.map((tick) => (
                          <li
                            key={tick}
                            data-testid="timeline-ruler-tick"
                            className="absolute bottom-1 border-l border-slate-700 pl-1 leading-none whitespace-nowrap"
                            style={{ left: tick * pixelsPerSecond }}
                          >
                            {formatTimecode(tick)}
                          </li>
                        ))}
                      </ol>

                      <div className="relative h-24">
                        {clips.map((clip, idx) => {
                          const isSelected = activeClip === clip;
                          return (
                            <button
                              key={`${clip.order_index}-${clip.shot_id ?? "clip"}`}
                              type="button"
                              aria-pressed={isSelected}
                              onClick={() => setClipSelection({ sequenceId, index: idx })}
                              className={cn(
                                "absolute inset-y-0 overflow-hidden rounded-xl border p-2.5 text-left transition-colors focus-visible:ring-2 focus-visible:ring-indigo-300 focus-visible:outline-none",
                                isSelected
                                  ? "border-indigo-400 bg-slate-900 ring-1 ring-indigo-400/50"
                                  : "border-slate-800 bg-slate-900/80 hover:border-slate-600",
                              )}
                              style={{
                                left: clip.start_time_seconds * pixelsPerSecond,
                                width: Math.max(
                                  24,
                                  clip.effective_duration_seconds * pixelsPerSecond - 4,
                                ),
                              }}
                            >
                              <span className="flex items-center justify-between gap-2 text-[11px]">
                                <span className="rounded bg-indigo-500/25 px-1.5 py-0.5 font-mono font-semibold text-indigo-300">
                                  #{clip.order_index + 1}
                                </span>
                                <span className="font-mono text-slate-400">
                                  {formatSeconds(clip.effective_duration_seconds)}
                                </span>
                              </span>
                              <span className="mt-1.5 block truncate font-mono text-xs font-semibold text-white">
                                Shot: {clip.shot_id ?? "custom"}
                              </span>
                              <span className="mt-0.5 block truncate font-mono text-[11px] text-slate-400">
                                Take: {clip.take_id ?? "unassigned"}
                              </span>
                              <span className="mt-1 block truncate font-mono text-[10px] text-slate-500">
                                {clip.transition_type}
                              </span>
                            </button>
                          );
                        })}
                      </div>

                      {timeline.manifest.has_subtitles && (
                        <div
                          data-testid="timeline-subtitle-lane"
                          className="flex h-7 items-center truncate rounded-md border border-amber-500/40 bg-amber-700/25 px-2.5 text-[11px] font-medium text-amber-200"
                          style={{ width: Math.ceil(sequenceEndSeconds * pixelsPerSecond) }}
                        >
                          <Subtitles className="mr-1.5 size-3 shrink-0" aria-hidden="true" />
                          Subtitles saved ({timeline.manifest.subtitle_format.toUpperCase()}) — not
                          rendered into MP4
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </>
            )}

            <div
              data-testid="timeline-audio-summary"
              className="space-y-2 rounded-xl border border-cyan-500/30 bg-cyan-950/30 p-3 text-xs text-cyan-100"
            >
              <p className="flex items-start gap-1.5">
                <Music className="mt-0.5 size-3.5 shrink-0 text-cyan-300" aria-hidden="true" />
                <span>
                  Export audio: a silent AAC track. Shot audio is not included in the MP4.
                </span>
              </p>
              {audioTracks.length > 0 && (
                <>
                  <p className="text-[11px] text-cyan-300/80">
                    Audio track entries saved with this timeline (metadata only, not mixed into the
                    MP4):
                  </p>
                  <ul className="grid gap-2 sm:grid-cols-2">
                    {audioTracks.map((track, idx) => (
                      <li
                        key={`${track.name}-${idx}`}
                        className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 rounded-lg border border-cyan-500/30 bg-cyan-950/50 px-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="truncate font-semibold">{track.name}</p>
                          <p className="font-mono text-[11px] text-cyan-300/80">
                            {track.track_type} · fade in {track.fade_in_seconds}s · out{" "}
                            {track.fade_out_seconds}s
                          </p>
                        </div>
                        <span className="shrink-0 font-mono text-[11px] font-semibold text-cyan-300">
                          {track.muted ? "Muted" : `Volume ${Math.round(track.volume * 100)}%`}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export default TimelineEditorPage;

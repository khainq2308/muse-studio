import {
  AlertTriangle,
  CheckCircle2,
  Clapperboard,
  Clock,
  Download,
  Film,
  LoaderCircle,
  Music,
  Play,
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

  return (
    <div data-testid="timeline-editor-page" data-project-id={projectId} className="space-y-6">
      <div className="flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Clapperboard className="size-5 text-primary" aria-hidden="true" />
            <h2 className="text-xl font-semibold tracking-tight">
              Multi-Track Timeline Editor & FFmpeg Export
            </h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Assemble selected shot takes into a sequence, configure transitions and launch
            FFprobe-verified MP4 exports. Audio tracks and subtitles are stored with the timeline
            but are not rendered into the MP4 yet.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
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
      </div>

      {assembleMutation.isError && (
        <p role="alert" className="text-xs text-destructive">
          {actionErrorMessage(assembleMutation.error, activeAdminToken, "assemble the timeline")}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-12">
        <div className="space-y-5 lg:col-span-8">
          <section
            aria-label="Timeline assembly parameters"
            className="rounded-xl border border-border bg-secondary/30 p-4"
          >
            <h3 className="text-sm font-semibold">Sequence Assembly Configuration</h3>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <label className="space-y-1 text-xs font-medium">
                <span>Sequence name</span>
                <input
                  value={sequenceName}
                  onChange={(event) => setSequenceName(event.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
                  className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
                  className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none disabled:opacity-50"
                />
              </label>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-4 text-xs">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={allowMissingShots}
                  onChange={(event) => setAllowMissingShots(event.target.checked)}
                  className="rounded border-input"
                />
                <span>Allow assembling when some shots do not yet have a selected take</span>
              </label>

              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={includeBgm}
                  onChange={(event) => setIncludeBgm(event.target.checked)}
                  className="rounded border-input"
                />
                <span>Reserve a background music track (metadata only)</span>
              </label>
            </div>

            {includeBgm && (
              <p className="mt-2 text-xs text-warning">
                No audio file can be attached yet, and MP4 export does not mix audio tracks. This
                only records a track entry in the timeline manifest.
              </p>
            )}

            {includeBgm && (
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <label className="space-y-1 text-xs font-medium">
                  <span>BGM track name</span>
                  <input
                    value={bgmName}
                    onChange={(event) => setBgmName(event.target.value)}
                    className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm"
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
                    className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm"
                  />
                </label>
              </div>
            )}

            <div className="mt-3 grid gap-3 sm:grid-cols-4">
              <label className="space-y-1 text-xs font-medium">
                <span className="flex items-center gap-1">
                  <Subtitles className="size-3.5 text-primary" aria-hidden="true" />
                  Subtitle format
                </span>
                <select
                  value={subtitleFormat}
                  onChange={(event) => setSubtitleFormat(event.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm"
                >
                  <option value="srt">SRT</option>
                  <option value="vtt">WebVTT</option>
                </select>
              </label>
              <label className="space-y-1 text-xs font-medium sm:col-span-3">
                <span>Subtitle cues (optional SRT / VTT content)</span>
                <textarea
                  rows={5}
                  value={subtitleContent}
                  onChange={(event) => setSubtitleContent(event.target.value)}
                  placeholder={"1\n00:00:00,000 --> 00:00:04,000\nOpening scene dialogue..."}
                  className="w-full rounded-lg border border-input bg-background px-3 py-2 font-mono text-xs"
                />
              </label>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Subtitles are saved with the timeline; MP4 export does not burn in or embed them yet.
            </p>
          </section>

          <section
            aria-label="Multi-track timeline ruler"
            className="space-y-4 rounded-xl border border-border bg-card p-4"
          >
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-3">
              <div className="flex items-center gap-2">
                <Film className="size-4 text-primary" aria-hidden="true" />
                <h3 className="text-sm font-semibold">
                  {timeline ? timeline.name : "NLE Sequence Tracks"}
                </h3>
                {timeline && (
                  <span className="rounded bg-secondary px-2 py-0.5 font-mono text-xs text-muted-foreground">
                    {timeline.resolution} · {timeline.fps} fps · {timeline.aspect_ratio}
                  </span>
                )}
              </div>
              {timeline && (
                <span className="inline-flex items-center gap-1 font-mono text-xs font-semibold text-primary">
                  <Clock className="size-3.5" aria-hidden="true" />
                  Total: {Number(timeline.total_duration_seconds.toFixed(2))}s ({clips.length}{" "}
                  clips)
                </span>
              )}
            </div>

            {timelineQuery.isPending ? (
              <p
                role="status"
                className="flex items-center gap-2 py-6 text-sm text-muted-foreground"
              >
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                Loading project timeline...
              </p>
            ) : timelineQuery.isError && !isNoTimelineYet ? (
              <p role="alert" className="text-xs text-destructive">
                {authErrorMessage(timelineQuery.error, activeAdminToken, "project timeline")}
              </p>
            ) : !timeline ? (
              <div className="rounded-xl border border-dashed border-border bg-secondary/20 px-4 py-8 text-center">
                <p className="text-sm font-semibold">No timeline assembled for this project yet</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Click “Assemble / Refresh Timeline” above to build a sequence from your project
                  shots and selected takes.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {warnings.length > 0 && (
                  <div className="space-y-1.5 rounded-lg border border-warning/35 bg-warning/10 p-3">
                    <p className="flex items-center gap-1.5 text-xs font-semibold text-warning">
                      <AlertTriangle className="size-3.5" aria-hidden="true" />
                      Timeline Assembly Warnings ({warnings.length})
                    </p>
                    <ul className="space-y-1 text-xs text-foreground">
                      {warnings.map((w, idx) => (
                        <li key={`${w.code}-${idx}`}>
                          <span className="font-mono font-semibold">[{w.code}]</span> {w.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Track V1 — Video Clips ({clips.length})
                  </p>
                  {clips.length === 0 ? (
                    <p className="rounded-lg border border-dashed border-border p-4 text-xs text-muted-foreground">
                      No video clips in this sequence yet. Generate and select shot takes in Shot
                      Board, then re-assemble.
                    </p>
                  ) : (
                    <div className="flex gap-2 overflow-x-auto pb-2">
                      {clips.map((clip) => (
                        <div
                          key={`${clip.order_index}-${clip.shot_id ?? "clip"}`}
                          className="min-w-[190px] shrink-0 rounded-xl border border-primary/25 bg-slate-950 p-3 text-slate-100 shadow-xs"
                        >
                          <div className="flex items-center justify-between text-[11px]">
                            <span className="rounded bg-indigo-500/25 px-1.5 py-0.5 font-mono font-semibold text-indigo-300">
                              #{clip.order_index + 1}
                            </span>
                            <span className="font-mono text-slate-400">
                              {Number(clip.start_time_seconds.toFixed(1))}s
                            </span>
                          </div>
                          <p className="mt-1.5 truncate font-mono text-xs font-medium">
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
                      ))}
                    </div>
                  )}
                </div>

                <div className="space-y-2">
                  <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    <Music className="size-3.5 text-primary" aria-hidden="true" />
                    Audio Tracks ({audioTracks.length})
                  </p>
                  {audioTracks.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No auxiliary audio tracks.</p>
                  ) : (
                    <div className="grid gap-2 sm:grid-cols-2">
                      {audioTracks.map((track, idx) => (
                        <div
                          key={`${track.name}-${idx}`}
                          className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs"
                        >
                          <div>
                            <p className="font-semibold">{track.name}</p>
                            <p className="font-mono text-[11px] text-muted-foreground">
                              {track.track_type} · fade in {track.fade_in_seconds}s / out{" "}
                              {track.fade_out_seconds}s
                            </p>
                          </div>
                          <span className="font-mono font-medium">
                            {track.muted ? "Muted" : `Vol ${Math.round(track.volume * 100)}%`}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </section>
        </div>

        <div className="space-y-4 lg:col-span-4">
          <section
            aria-label="FFmpeg MP4 export"
            className="space-y-4 rounded-xl border border-border bg-secondary/30 p-4"
          >
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">Async FFmpeg MP4 Render</h3>
              <span className="font-mono text-[11px] text-muted-foreground">
                POST .../timeline/exports
              </span>
            </div>

            <p className="text-xs text-muted-foreground">
              Enqueues an asynchronous ARQ FFmpeg render job and verifies output streams with
              FFprobe before creating an asset. Failed renders never fabricate a completed asset.
            </p>

            <div className="grid gap-3">
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
                    className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm"
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
                    className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm"
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
                    className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm"
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
              className="space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"
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
              className="space-y-3 rounded-xl border border-border bg-card p-4"
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
    </div>
  );
}

export default TimelineEditorPage;

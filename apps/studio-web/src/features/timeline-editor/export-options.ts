import type { TimelineSequence } from "@/features/timeline-editor/api";

/** Aspect ratios accepted by ExportLaunchIn.aspect_ratio. */
export const EXPORT_ASPECT_RATIOS = ["16:9", "9:16", "1:1", "21:9"] as const;
export type ExportAspectRatio = (typeof EXPORT_ASPECT_RATIOS)[number];

/**
 * Resolutions offered per aspect ratio. The first entry mirrors the backend's
 * ASPECT_RESOLUTION_MAP default. The backend renders any valid `resolution` as-is without
 * checking it against `aspect_ratio`, so the UI must never send a mismatched pair.
 */
export const RESOLUTIONS_BY_ASPECT: Record<ExportAspectRatio, readonly string[]> = {
  "16:9": ["1920x1080", "1280x720"],
  "9:16": ["1080x1920", "720x1280"],
  "1:1": ["1080x1080", "720x720"],
  "21:9": ["2560x1080"],
};

export function isExportAspectRatio(value: string): value is ExportAspectRatio {
  return (EXPORT_ASPECT_RATIOS as readonly string[]).includes(value);
}

export function defaultResolutionFor(aspect: ExportAspectRatio): string {
  return RESOLUTIONS_BY_ASPECT[aspect][0];
}

/**
 * Timeline features the MP4 exporter will not render, computed before launch. Mirrors
 * `_unsupported_export_features` in apps/orchestrator/muse_orchestrator/api/timeline.py.
 */
export function skippedExportFeatures(timeline: TimelineSequence): string[] {
  const manifest = timeline.manifest;
  const features = ["clip audio (output carries a silent AAC track)"];
  if (manifest.title_card && Object.keys(manifest.title_card).length > 0) {
    features.push("title card");
  }
  if (manifest.has_subtitles) features.push("subtitles");
  if (manifest.audio_tracks.length > 0) features.push("audio tracks (background music)");
  return features;
}

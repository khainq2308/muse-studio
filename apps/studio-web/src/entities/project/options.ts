/**
 * Project creation choices shared by every create-project form.
 *
 * Constraints mirror `ProjectCreateIn` in `apps/orchestrator/muse_orchestrator/api/projects.py`:
 * - `aspect_ratio` must match `^(16:9|9:16|1:1|21:9)$`.
 * - `fps` must be within 12..60.
 * - `style_preset` (≤128 chars) and `language` (≤16 chars) are free-form strings; the lists
 *   below are curated suggestions, not a backend enum.
 * - `output_profile` is stored on the project but not yet read by the export pipeline, so the
 *   forms only submit the backend default instead of offering choices with no effect.
 */
export const PROJECT_ASPECT_RATIO_OPTIONS = ["16:9", "9:16", "1:1", "21:9"] as const;
export const PROJECT_FPS_OPTIONS = [24, 25, 30, 60] as const;
export const PROJECT_STYLE_PRESET_OPTIONS = [
  "2d_anime_cinematic",
  "3d_pixar_stylized",
  "photoreal_film_noir",
  "cyberpunk_neon_anime",
  "watercolor_storybook",
] as const;
export const PROJECT_LANGUAGE_OPTIONS = ["vi", "en", "ja"] as const;
export const PROJECT_DEFAULT_OUTPUT_PROFILE = "1080p_h264";
export const PROJECT_OUTPUT_PROFILE_HINT =
  "Stored on the project; export does not apply output profiles yet.";

export const PROJECT_CREATE_DEFAULTS = {
  aspect_ratio: "16:9",
  fps: 24,
  style_preset: "2d_anime_cinematic",
  language: "vi",
  output_profile: PROJECT_DEFAULT_OUTPUT_PROFILE,
} as const;

/** Honest summary of what `POST /api/v1/projects/{id}/duplicate` copies. */
export const PROJECT_DUPLICATE_SCOPE_NOTE =
  "Only project settings are copied; Story Bible, shots and assets are not copied.";

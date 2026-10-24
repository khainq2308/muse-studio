/** Views of the Script & Story Bible module, kept in the URL as `?view=`. */
export const SCRIPT_STUDIO_VIEWS = ["script", "bible"] as const;
export type ScriptStudioView = (typeof SCRIPT_STUDIO_VIEWS)[number];

export function parseScriptStudioView(value: unknown): ScriptStudioView | undefined {
  return typeof value === "string" && (SCRIPT_STUDIO_VIEWS as readonly string[]).includes(value)
    ? (value as ScriptStudioView)
    : undefined;
}

/**
 * View shown when the URL names none: an empty project starts by importing a script; a project
 * that already has a Story Bible or shots opens on the Story Bible, where follow-up work is. A
 * Story Bible that failed to load also opens there, so its error and Retry are not hidden.
 */
export function defaultScriptStudioView(state: {
  hasEntries: boolean;
  hasShots: boolean;
  bibleFailed: boolean;
}): ScriptStudioView {
  return state.hasEntries || state.hasShots || state.bibleFailed ? "bible" : "script";
}

/** Tabs of the selected-shot panel. */
export const SHOT_BOARD_TABS = ["takes", "details", "prompt"] as const;
export type ShotBoardTab = (typeof SHOT_BOARD_TABS)[number];

/**
 * Shot Board navigation state kept in the URL (`?shot=<id>&tab=<tab>`), so a selected shot
 * deep-links and survives a reload. Both keys are optional: without `shot` the first shot is
 * shown, without `tab` the default tab is derived from the shot's takes.
 */
export interface ShotBoardSearch {
  shot?: string;
  tab?: ShotBoardTab;
}

export function isShotBoardTab(value: unknown): value is ShotBoardTab {
  return typeof value === "string" && (SHOT_BOARD_TABS as readonly string[]).includes(value);
}

/**
 * Route `validateSearch`: keeps only well-formed values. Both keys are always returned
 * (possibly `undefined`) because the router merges the validated result over the raw search;
 * an omitted key would let a malformed raw value through.
 */
export function validateShotBoardSearch(search: Record<string, unknown>): ShotBoardSearch {
  // The router's default parser JSON-decodes values, so a numeric-looking ID arrives as a number.
  const shot = typeof search.shot === "number" ? String(search.shot) : search.shot;
  return {
    shot: typeof shot === "string" && shot.trim() ? shot : undefined,
    tab: isShotBoardTab(search.tab) ? search.tab : undefined,
  };
}

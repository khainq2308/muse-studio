import type { ProjectShot } from "@/entities/shot/api";
import type { StoryBibleEntry } from "@/features/script-studio/state";

export interface BibleAttentionItem {
  entry: StoryBibleEntry;
  /** Shots whose lint report blocks them on this character's missing reference. */
  blockedShots: number;
}

type AttentionShot = Pick<ProjectShot, "subject_codes" | "lint_report">;

/**
 * A character that requires a locked reference image but has none. The prompt compiler reports
 * a `BLOCKED_REFERENCE` blocker on `subject_ids` for every shot using such a character. This is
 * one source of `BLOCKED_REFERENCE`, not the only one: a shot's own reference lock, a reference
 * asset that no longer exists, and (on batch launch) any other lint blocker also block shots.
 * Only this one is fixed in the Story Bible.
 */
export function isBlockingMissingReference(
  entry: Pick<StoryBibleEntry, "entry_type" | "requires_reference_lock" | "reference_asset_ids">,
): boolean {
  return (
    entry.entry_type === "character" &&
    entry.requires_reference_lock &&
    entry.reference_asset_ids.length === 0
  );
}

/** The shot has a character-reference blocker and uses one of `codes`. */
export function isBlockedOnCharacterReference(
  shot: AttentionShot,
  codes: ReadonlySet<string>,
): boolean {
  return (
    shot.lint_report.some(
      (issue) =>
        issue.severity === "blocker" &&
        issue.code === "BLOCKED_REFERENCE" &&
        issue.field === "subject_ids",
    ) && shot.subject_codes.some((code) => codes.has(code))
  );
}

/** Story Bible fixes that unblock generation, most blocked shots first. */
export function entriesNeedingReference(
  entries: readonly StoryBibleEntry[],
  shots: readonly AttentionShot[],
): BibleAttentionItem[] {
  return entries
    .filter(isBlockingMissingReference)
    .map((entry) => {
      const codes = new Set([entry.code_name]);
      return {
        entry,
        blockedShots: shots.filter((shot) => isBlockedOnCharacterReference(shot, codes)).length,
      };
    })
    .sort((a, b) => b.blockedShots - a.blockedShots);
}

import { isShotBlocked } from "@/entities/shot/status";
import type { ShotBoardTab } from "@/features/shot-board/search";
import {
  groupShotsByScene,
  shotLintBlockers,
  shotLintWarnings,
  type ProjectShot,
  type ShotSceneGroup,
} from "@/features/shot-board/state";
import i18n from "@/lib/i18n";

/** Placeholder for a value the API did not provide; never replaced by an invented default. */
export const MISSING = "—";

/** Rounds to centiseconds so float sums never render as 0.30000000000000004. */
export function formatSeconds(value: number): string {
  if (!Number.isFinite(value)) return MISSING;
  return `${Math.round(value * 100) / 100}s`;
}

/** A non-empty trimmed string, or the missing-value dash. */
export function textOrMissing(value: string | null | undefined): string {
  return value && value.trim() ? value : MISSING;
}

export { isShotBlocked };

/** Rail filters, each derived from a real `ProjectShotOut` field. */
export const SHOT_FILTERS = ["all", "needs_take", "blocked", "approved"] as const;
export type ShotFilter = (typeof SHOT_FILTERS)[number];

export const SHOT_FILTER_LABELS: Record<ShotFilter, string> = {
  all: "All",
  needs_take: "Needs take",
  blocked: "Blocked",
  approved: "Approved",
};

export const SHOT_FILTER_DESCRIPTIONS: Record<ShotFilter, string> = {
  all: "Every shot in the project",
  needs_take: "Shots without a selected take",
  blocked: "Shots blocked by a missing reference, a missing upstream frame or a lint blocker",
  approved: "Shots whose approval state is Approved",
};

export function shotMatchesFilter(shot: ProjectShot, filter: ShotFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "needs_take":
      return shot.selected_take_id === null;
    case "blocked":
      return isShotBlocked(shot);
    case "approved":
      return shot.approval_state === "approved";
  }
}

export function shotFilterCounts(shots: readonly ProjectShot[]): Record<ShotFilter, number> {
  const counts = { all: 0, needs_take: 0, blocked: 0, approved: 0 } satisfies Record<
    ShotFilter,
    number
  >;
  for (const shot of shots) {
    for (const filter of SHOT_FILTERS) {
      if (shotMatchesFilter(shot, filter)) counts[filter] += 1;
    }
  }
  return counts;
}

/** Scene groups (scene order, then sequence order) keeping only shots that match the filter. */
export function filterSceneGroups(
  shots: readonly ProjectShot[],
  filter: ShotFilter,
): ShotSceneGroup[] {
  return groupShotsByScene(shots)
    .map((group) => ({
      sceneId: group.sceneId,
      shots: group.shots.filter((shot) => shotMatchesFilter(shot, filter)),
    }))
    .filter((group) => group.shots.length > 0);
}

/** Shots in navigator order: scenes in order of their first shot, then sequence index. */
export function orderedShots(shots: readonly ProjectShot[]): ProjectShot[] {
  return groupShotsByScene(shots).flatMap((group) => group.shots);
}

/**
 * The shot named by the URL, or the first shot in navigator order when the URL names none
 * (or names a shot that is not in this project).
 */
export function resolveSelectedShot(
  shots: readonly ProjectShot[],
  shotId: string | undefined,
): ProjectShot | undefined {
  const ordered = orderedShots(shots);
  return (shotId ? ordered.find((shot) => shot.id === shotId) : undefined) ?? ordered[0];
}

/**
 * Tab shown when the URL does not name one: Takes when the shot has takes, otherwise Details.
 * While the take list is loading, a selected take already proves that takes exist. When the
 * take list failed to load it is unknown whether takes exist, so Details opens and the Takes
 * tab is marked as failed. (Opening Takes by default there would remount the take review,
 * which refetches the failed query, flips it back to pending and could flip the tab again.)
 */
export function defaultShotTab(
  shot: Pick<ProjectShot, "selected_take_id">,
  takes: { status: "pending" | "error" | "success"; count: number },
): ShotBoardTab {
  if (takes.status === "success") return takes.count > 0 ? "takes" : "details";
  if (takes.status === "error") return "details";
  return shot.selected_take_id ? "takes" : "details";
}

export interface ShotLintSummary {
  level: "blocker" | "warning" | "clear";
  label: string;
  tone: string;
}

/** One-line Prompt Linter status: blockers win over warnings. */
export function shotLintSummary(shot: Pick<ProjectShot, "lint_report">): ShotLintSummary {
  const blockers = shotLintBlockers(shot.lint_report).length;
  if (blockers > 0) {
    return {
      level: "blocker",
      label: i18n.t("shot-board:lint.blockerCount", {
        count: blockers,
        defaultValue: `${blockers} lint ${blockers === 1 ? "blocker" : "blockers"}`,
      }),
      tone: "border border-destructive/30 bg-destructive/10 text-destructive",
    };
  }
  const warnings = shotLintWarnings(shot.lint_report).length;
  if (warnings > 0) {
    return {
      level: "warning",
      label: i18n.t("shot-board:lint.warningCount", {
        count: warnings,
        defaultValue: `${warnings} lint ${warnings === 1 ? "warning" : "warnings"}`,
      }),
      tone: "border border-warning/30 bg-warning/10 text-warning",
    };
  }
  return {
    level: "clear",
    label: i18n.t("shot-board:lint.clear", {
      defaultValue: "Lint clear",
    }),
    tone: "border border-success/30 bg-success/10 text-success",
  };
}

import type { ProjectShot } from "@/entities/shot/api";
import { navigatorOrder } from "@/entities/shot/order";
import { isShotBlocked } from "@/entities/shot/status";

/** Shot fields the overview reads; everything else in `ProjectShotOut` is irrelevant here. */
export type ProgressShot = Pick<
  ProjectShot,
  | "id"
  | "scene_id"
  | "sequence_index"
  | "generation_state"
  | "approval_state"
  | "selected_take_id"
  | "lint_report"
>;

export interface ProjectProgress {
  total: number;
  /** Shots with a selected take, i.e. ready for the timeline. */
  withTake: number;
  approved: number;
  blocked: number;
  failed: number;
  /** Queued or running in the generation queue. */
  inFlight: number;
}

/** Where the next action takes the user; module names match the project routes. */
export type NextStep =
  | { kind: "import_script"; module: "script-studio" }
  | {
      kind: "resolve_blocked";
      module: "shot-board";
      count: number;
      shotId: string;
      /** Shot Board tab that explains the block: lint issues or the generation state. */
      tab: "prompt" | "details";
    }
  | { kind: "check_failed"; module: "queue-monitor"; count: number }
  | { kind: "pick_takes"; module: "shot-board"; count: number; shotId: string }
  | { kind: "wait_generation"; module: "queue-monitor"; count: number }
  | { kind: "assemble"; module: "timeline-editor" };

function isInFlight(shot: ProgressShot): boolean {
  return shot.generation_state === "queued" || shot.generation_state === "running";
}

export function summarizeProgress(shots: readonly ProgressShot[]): ProjectProgress {
  return {
    total: shots.length,
    withTake: shots.filter((shot) => shot.selected_take_id !== null).length,
    approved: shots.filter((shot) => shot.approval_state === "approved").length,
    blocked: shots.filter(isShotBlocked).length,
    failed: shots.filter((shot) => shot.generation_state === "failed").length,
    inFlight: shots.filter(isInFlight).length,
  };
}

/**
 * The single most useful next action for a project, derived only from real shot fields.
 * Order: nothing to work on → blockers → failures → missing takes → wait for the queue →
 * assemble. Blockers come first because they stop downstream generation entirely.
 */
export function deriveNextStep(shots: readonly ProgressShot[]): NextStep {
  if (shots.length === 0) return { kind: "import_script", module: "script-studio" };

  // Filter the whole project in Shot Board navigator order, so "first" names the shot the rail
  // lists first. (Ordering a filtered subset would reorder scenes by their first match.)
  const ordered = navigatorOrder(shots);
  const blocked = ordered.filter(isShotBlocked);
  const firstBlocked = blocked[0];
  if (firstBlocked) {
    return {
      kind: "resolve_blocked",
      module: "shot-board",
      count: blocked.length,
      shotId: firstBlocked.id,
      tab: firstBlocked.lint_report.some((issue) => issue.severity === "blocker")
        ? "prompt"
        : "details",
    };
  }

  const failed = shots.filter((shot) => shot.generation_state === "failed");
  if (failed.length > 0)
    return { kind: "check_failed", module: "queue-monitor", count: failed.length };

  const needsTake = ordered.filter((shot) => shot.selected_take_id === null && !isInFlight(shot));
  const firstNeedsTake = needsTake[0];
  if (firstNeedsTake) {
    return {
      kind: "pick_takes",
      module: "shot-board",
      count: needsTake.length,
      shotId: firstNeedsTake.id,
    };
  }

  const inFlight = shots.filter(isInFlight).length;
  if (inFlight > 0) return { kind: "wait_generation", module: "queue-monitor", count: inFlight };

  return { kind: "assemble", module: "timeline-editor" };
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

export function nextStepCopy(step: NextStep): { title: string; detail: string; action: string } {
  switch (step.kind) {
    case "import_script":
      return {
        title: "Import a script",
        detail: "This project has no shots yet. Import a script to break it into scenes and shots.",
        action: "Open Script & Story Bible",
      };
    case "resolve_blocked":
      return {
        title: `Resolve ${plural(step.count, "blocked shot")}`,
        detail:
          "A missing reference, a missing upstream frame or a lint blocker stops generation. " +
          "Open the first blocked shot to see why.",
        action: "Open first blocked shot",
      };
    case "check_failed":
      return {
        title: `Check ${plural(step.count, "failed shot")}`,
        detail: "The last generation of these shots failed. The queue shows each task's error.",
        action: "Open Generation Queue",
      };
    case "pick_takes":
      return {
        title: `${plural(step.count, "shot")} need a take`,
        detail:
          "No take is selected and nothing is generating. Generate a take, or review existing " +
          "takes and select one for the timeline.",
        action: "Open Shot Board",
      };
    case "wait_generation":
      return {
        title: `${plural(step.count, "shot")} generating`,
        detail: "Tasks are queued or running. Follow them in the queue.",
        action: "Open Generation Queue",
      };
    case "assemble":
      return {
        title: "Assemble and export",
        detail: "Every shot has a selected take. Assemble the timeline and export an MP4.",
        action: "Open Timeline & Export",
      };
  }
}

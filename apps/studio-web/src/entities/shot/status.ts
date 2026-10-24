import type { ProjectShot } from "@/entities/shot/api";

/** Blocked by the backend generation state or by a Prompt Linter blocker. */
export function isShotBlocked(
  shot: Pick<ProjectShot, "generation_state" | "lint_report">,
): boolean {
  return (
    shot.generation_state === "blocked_reference" ||
    shot.generation_state === "blocked_upstream_frame" ||
    shot.lint_report.some((issue) => issue.severity === "blocker")
  );
}

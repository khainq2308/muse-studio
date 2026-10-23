import { describe, expect, it } from "vitest";
import { makeShot } from "@/features/shot-board/test-fixtures";
import {
  defaultShotTab,
  filterSceneGroups,
  formatSeconds,
  isShotBlocked,
  resolveSelectedShot,
  shotFilterCounts,
  shotLintSummary,
  textOrMissing,
} from "@/features/shot-board/workspace";

const blocker = {
  code: "MISSING_REFERENCE",
  severity: "blocker" as const,
  message: "Reference image required.",
  field: "reference_asset_ids",
};
const warning = { code: "LONG", severity: "warning" as const, message: "Long.", field: null };

const shots = [
  makeShot({ id: "b", scene_id: "scene-2", sequence_index: 3 }),
  makeShot({ id: "a", sequence_index: 1, selected_take_id: "take-1", approval_state: "approved" }),
  makeShot({ id: "c", sequence_index: 2, generation_state: "blocked_upstream_frame" }),
  makeShot({ id: "d", scene_id: "scene-2", sequence_index: 4, lint_report: [blocker] }),
];

describe("shot workspace helpers", () => {
  it("never invents values for missing data", () => {
    expect(textOrMissing("")).toBe("—");
    expect(textOrMissing("  ")).toBe("—");
    expect(textOrMissing(null)).toBe("—");
    expect(textOrMissing("wide")).toBe("wide");
    expect(formatSeconds(Number.NaN)).toBe("—");
    expect(formatSeconds(0.1 + 0.2)).toBe("0.3s");
  });

  it("derives blocked state from generation state and lint blockers", () => {
    expect(isShotBlocked(shots[2]!)).toBe(true);
    expect(isShotBlocked(shots[3]!)).toBe(true);
    expect(isShotBlocked(makeShot({ generation_state: "blocked_reference" }))).toBe(true);
    expect(isShotBlocked(makeShot({ lint_report: [warning] }))).toBe(false);
  });

  it("counts and filters shots by real fields, keeping scene and sequence order", () => {
    expect(shotFilterCounts(shots)).toEqual({ all: 4, needs_take: 3, blocked: 2, approved: 1 });
    expect(
      filterSceneGroups(shots, "all").map((group) => [
        group.sceneId,
        group.shots.map((shot) => shot.id),
      ]),
    ).toEqual([
      ["scene-1", ["a", "c"]],
      ["scene-2", ["b", "d"]],
    ]);
    expect(
      filterSceneGroups(shots, "blocked").map((group) => group.shots.map((shot) => shot.id)),
    ).toEqual([["c"], ["d"]]);
    expect(filterSceneGroups(shots, "approved")).toHaveLength(1);
  });

  it("resolves the URL shot, falling back to the first shot in navigator order", () => {
    expect(resolveSelectedShot(shots, "d")?.id).toBe("d");
    expect(resolveSelectedShot(shots, undefined)?.id).toBe("a");
    expect(resolveSelectedShot(shots, "missing")?.id).toBe("a");
    expect(resolveSelectedShot([], "a")).toBeUndefined();
  });

  it("opens Takes only when the shot has takes", () => {
    const withTake = { selected_take_id: "take-1" };
    const withoutTake = { selected_take_id: null };
    expect(defaultShotTab(withoutTake, { status: "success", count: 2 })).toBe("takes");
    expect(defaultShotTab(withTake, { status: "success", count: 0 })).toBe("details");
    expect(defaultShotTab(withTake, { status: "pending", count: 0 })).toBe("takes");
    expect(defaultShotTab(withoutTake, { status: "pending", count: 0 })).toBe("details");
    expect(defaultShotTab(withTake, { status: "error", count: 0 })).toBe("details");
  });

  it("summarises lint with blockers before warnings", () => {
    expect(shotLintSummary({ lint_report: [warning, blocker] }).label).toBe("1 lint blocker");
    expect(shotLintSummary({ lint_report: [warning, warning] }).label).toBe("2 lint warnings");
    expect(shotLintSummary({ lint_report: [] }).label).toBe("Lint clear");
  });
});

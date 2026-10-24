import { describe, expect, it } from "vitest";
import {
  deriveNextStep,
  nextStepCopy,
  summarizeProgress,
  type ProgressShot,
} from "@/features/workspace-overview/progress";

let counter = 0;
function shot(overrides: Partial<ProgressShot> = {}): ProgressShot {
  counter += 1;
  return {
    id: `shot-${counter}`,
    scene_id: "scene-1",
    sequence_index: counter,
    generation_state: "not_started",
    approval_state: "draft",
    selected_take_id: null,
    lint_report: [],
    ...overrides,
  };
}

const LINT_BLOCKER = {
  code: "MISSING_REF",
  severity: "blocker" as const,
  message: "Reference missing",
  field: null,
};

describe("summarizeProgress", () => {
  it("counts each shot state from real fields", () => {
    const progress = summarizeProgress([
      shot({ selected_take_id: "take-1", approval_state: "approved" }),
      shot({ generation_state: "blocked_upstream_frame" }),
      shot({ lint_report: [LINT_BLOCKER] }),
      shot({ generation_state: "failed" }),
      shot({ generation_state: "running" }),
      shot({ generation_state: "queued" }),
    ]);
    expect(progress).toEqual({
      total: 6,
      withTake: 1,
      approved: 1,
      blocked: 2,
      failed: 1,
      inFlight: 2,
    });
  });
});

describe("deriveNextStep", () => {
  it("asks for a script when the project has no shots", () => {
    expect(deriveNextStep([])).toEqual({ kind: "import_script", module: "script-studio" });
  });

  it("puts blockers first and deep-links the earliest blocked shot to the tab that explains it", () => {
    const late = shot({ sequence_index: 9, lint_report: [LINT_BLOCKER] });
    const early = shot({ sequence_index: 2, generation_state: "blocked_reference" });
    const step = deriveNextStep([shot({ generation_state: "failed" }), late, early]);
    expect(step).toEqual({
      kind: "resolve_blocked",
      module: "shot-board",
      count: 2,
      shotId: early.id,
      tab: "details",
    });

    const lintOnly = shot({ lint_report: [LINT_BLOCKER] });
    expect(deriveNextStep([lintOnly])).toMatchObject({ shotId: lintOnly.id, tab: "prompt" });
  });

  it("picks the first blocked shot in Shot Board order: scene of the first shot, then sequence", () => {
    // Scene A opens with shot 1 and also holds shot 5; scene B holds shots 2 and 3. The rail
    // lists 1, 5, 2, 3, so shot 5 is the first blocked shot the user sees.
    const shots = [
      shot({ id: "a1", scene_id: "scene-a", sequence_index: 1 }),
      shot({
        id: "a5",
        scene_id: "scene-a",
        sequence_index: 5,
        generation_state: "blocked_reference",
      }),
      shot({
        id: "b2",
        scene_id: "scene-b",
        sequence_index: 2,
        generation_state: "blocked_reference",
      }),
      shot({ id: "b3", scene_id: "scene-b", sequence_index: 3 }),
    ];
    expect(deriveNextStep(shots)).toMatchObject({ kind: "resolve_blocked", shotId: "a5" });
  });

  it("sends failed shots to the queue, where task errors are visible", () => {
    expect(
      deriveNextStep([shot({ generation_state: "failed" }), shot({ selected_take_id: "t" })]),
    ).toEqual({ kind: "check_failed", module: "queue-monitor", count: 1 });
  });

  it("points at shots without a selected take that are not already generating", () => {
    const waiting = shot({ sequence_index: 1, generation_state: "running" });
    const idle = shot({ sequence_index: 2 });
    expect(deriveNextStep([waiting, idle])).toEqual({
      kind: "pick_takes",
      module: "shot-board",
      count: 1,
      shotId: idle.id,
    });
  });

  it("waits for the queue when every remaining shot is generating", () => {
    expect(
      deriveNextStep([shot({ generation_state: "queued" }), shot({ selected_take_id: "t" })]),
    ).toEqual({ kind: "wait_generation", module: "queue-monitor", count: 1 });
  });

  it("moves to assembly once every shot has a selected take", () => {
    expect(
      deriveNextStep([shot({ selected_take_id: "a" }), shot({ selected_take_id: "b" })]),
    ).toEqual({ kind: "assemble", module: "timeline-editor" });
  });
});

describe("nextStepCopy", () => {
  it("pluralizes counts", () => {
    expect(
      nextStepCopy({
        kind: "resolve_blocked",
        module: "shot-board",
        count: 1,
        shotId: "s",
        tab: "prompt",
      }).title,
    ).toBe("Xử lý 1 shot bị chặn");
    expect(nextStepCopy({ kind: "check_failed", module: "queue-monitor", count: 3 }).title).toBe(
      "Kiểm tra 3 shot thất bại",
    );
  });
});

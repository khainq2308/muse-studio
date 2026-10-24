import { describe, expect, it } from "vitest";
import { latestTaskByShot, summarizeGenerationActivity } from "@/features/shot-board/queue-status";
import { makeQueueTask, makeTaskPage } from "@/features/shot-board/test-fixtures";

describe("latestTaskByShot", () => {
  it("keeps the first (newest) task of each shot and skips tasks without a shot", () => {
    const latest = latestTaskByShot([
      makeQueueTask({ id: "t3", shot_id: "shot-1", state: "succeeded" }),
      makeQueueTask({ id: "t2", shot_id: "shot-2", state: "failed" }),
      makeQueueTask({ id: "t1", shot_id: "shot-1", state: "failed" }),
    ]);
    expect([...latest.entries()].map(([shot, task]) => [shot, task.id])).toEqual([
      ["shot-1", "t3"],
      ["shot-2", "t2"],
    ]);
  });
});

describe("summarizeGenerationActivity", () => {
  it("counts failed shots by their latest task, not the all-time failed total", () => {
    const tasks = [
      makeQueueTask({ id: "t3", shot_id: "shot-1", state: "succeeded" }),
      makeQueueTask({ id: "t2", shot_id: "shot-2", state: "poll_timeout" }),
      // An old failure of shot-1, already retried successfully.
      makeQueueTask({ id: "t1", shot_id: "shot-1", state: "failed" }),
    ];
    const page = makeTaskPage(tasks, { active_tasks: 2, awaiting_input_tasks: 1, failed_tasks: 7 });
    expect(summarizeGenerationActivity(page, latestTaskByShot(tasks))).toEqual({
      active: 2,
      awaitingInput: 1,
      failedShots: 1,
    });
  });
});

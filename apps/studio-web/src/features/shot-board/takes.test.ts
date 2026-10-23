import { describe, expect, it } from "vitest";
import {
  buildContinuityFlagRequest,
  downstreamShotLabels,
  isPredecessorOutOfOrder,
  sortTakes,
  takeActionErrorMessage,
} from "@/features/shot-board/takes";
import { makeShot, makeTake } from "@/features/shot-board/test-fixtures";
import { ApiError } from "@/lib/api/client";

describe("buildContinuityFlagRequest", () => {
  const take = makeTake({ model_id: "take-model" });

  it("flags without regeneration: no connection, trimmed notes, take model echoed", () => {
    expect(
      buildContinuityFlagRequest({ take, flagged: true, directorNotes: "  Hair flips.  " }),
    ).toEqual({
      continuity_flagged: true,
      director_notes: "Hair flips.",
      regenerate_now: false,
      model_id: "take-model",
    });
  });

  it("sends the Generation settings connection and model explicitly when regenerating", () => {
    expect(
      buildContinuityFlagRequest({
        take,
        flagged: true,
        directorNotes: "Wrong prop.",
        regenerate: { connectionId: "conn-live", modelId: " muse-video-v1 " },
      }),
    ).toEqual({
      continuity_flagged: true,
      director_notes: "Wrong prop.",
      regenerate_now: true,
      connection_id: "conn-live",
      model_id: "muse-video-v1",
    });
  });

  it("can clear a flag", () => {
    expect(
      buildContinuityFlagRequest({ take, flagged: false, directorNotes: "old" }),
    ).toMatchObject({ continuity_flagged: false, regenerate_now: false });
  });
});

describe("isPredecessorOutOfOrder", () => {
  const shot = makeShot({ sequence_index: 3 });

  it("warns when the previous shot comes at or after the shot", () => {
    expect(isPredecessorOutOfOrder(shot, makeShot({ sequence_index: 5 }))).toBe(true);
    expect(isPredecessorOutOfOrder(shot, makeShot({ sequence_index: 3 }))).toBe(true);
  });

  it("does not warn for an earlier previous shot or no previous shot", () => {
    expect(isPredecessorOutOfOrder(shot, makeShot({ sequence_index: 2 }))).toBe(false);
    expect(isPredecessorOutOfOrder(shot, undefined)).toBe(false);
  });
});

describe("take helpers", () => {
  it("sorts takes by take number", () => {
    const sorted = sortTakes([
      makeTake({ id: "b", take_number: 2 }),
      makeTake({ id: "a", take_number: 1 }),
    ]);
    expect(sorted.map((take) => take.id)).toEqual(["a", "b"]);
  });

  it("labels downstream shots and keeps unknown IDs", () => {
    expect(
      downstreamShotLabels(
        ["shot-2", "shot-x"],
        [makeShot({ id: "shot-2", sequence_index: 2, title: "Chase" })],
      ),
    ).toEqual(["Shot 2 · Chase", "shot-x"]);
  });

  it.each([
    [new ApiError({ status: 401, detail: "no" }), "select", /admin token was rejected/],
    [new ApiError({ status: 403, detail: "no" }), "flag", /does not have permission/],
    [
      new ApiError({ status: 404, detail: "Connection không tồn tại." }),
      "flag",
      /shot, take or connection was not found \(Connection không tồn tại\.\)/,
    ],
    [
      new ApiError({ status: 422, detail: "Shot has lint blockers" }),
      "flag",
      /^The continuity flag was not saved: Shot has lint blockers$/,
    ],
    [
      new ApiError({ status: 409, detail: "Conflict detail" }),
      "select",
      /^The take was not selected: Conflict detail$/,
    ],
    [new ApiError({ status: 200, detail: "too big", category: "too_large" }), "preview", /too big/],
  ] as const)("maps %s for %s", (error, action, expected) => {
    expect(takeActionErrorMessage(error, action)).toMatch(expected);
  });
});

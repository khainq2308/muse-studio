import { describe, expect, it } from "vitest";
import {
  buildContinuityFlagRequest,
  downstreamShotLabels,
  flagErrorFeedback,
  isPredecessorOutOfOrder,
  parseTakeCreatedAt,
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

describe("flagErrorFeedback", () => {
  it.each([
    ["client timeout", new ApiError({ status: 0, detail: "t", category: "timeout" })],
    ["network failure", new TypeError("Failed to fetch")],
    ["gateway error", new ApiError({ status: 502, detail: "Bad gateway" })],
    ["server error", new ApiError({ status: 500, detail: "boom" })],
  ])("reports an unknown outcome for a regenerate request after a %s", (_name, error) => {
    const feedback = flagErrorFeedback(error, true);
    expect(feedback.outcomeUnknown).toBe(true);
    expect(feedback.message).toMatch(/^The outcome is unknown/);
    expect(feedback.message).not.toMatch(/not saved|Try again/);
  });

  it("does not claim a 422 after regenerate left the flag unsaved", () => {
    const feedback = flagErrorFeedback(
      new ApiError({ status: 422, detail: "Shot has blockers." }),
      true,
    );
    expect(feedback).toEqual({
      outcomeUnknown: true,
      message:
        "The regeneration was refused: Shot has blockers. The continuity flag may already be " +
        "saved; refresh the take list to check it before retrying.",
    });
  });

  it("keeps definite messages for requests without regeneration and for pre-save rejections", () => {
    expect(
      flagErrorFeedback(new ApiError({ status: 0, detail: "t", category: "timeout" }), false),
    ).toEqual({
      outcomeUnknown: false,
      message: "The continuity flag was not saved: the request timed out. Try again.",
    });
    expect(
      flagErrorFeedback(new ApiError({ status: 400, detail: "Connection disabled" }), true),
    ).toEqual({
      outcomeUnknown: false,
      message: "The continuity flag was not saved: Connection disabled",
    });
  });
});

describe("isPredecessorOutOfOrder", () => {
  const shot = makeShot({ sequence_index: 3 });

  it("ignores a previous shot in another scene", () => {
    expect(
      isPredecessorOutOfOrder(shot, makeShot({ scene_id: "scene-9", sequence_index: 7 })),
    ).toBe(false);
  });

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

  it("parses valid ISO-8601 created_at timestamps and returns null for invalid values", () => {
    expect(parseTakeCreatedAt("2026-10-23T12:34:56Z")?.toISOString()).toBe(
      "2026-10-23T12:34:56.000Z",
    );
    expect(parseTakeCreatedAt("")).toBeNull();
    expect(parseTakeCreatedAt("not-a-date")).toBeNull();
    expect(parseTakeCreatedAt(null)).toBeNull();
    expect(parseTakeCreatedAt(undefined)).toBeNull();
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

import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { TimelineSequence } from "@/features/timeline-editor/api";
import {
  clipErrorFromApi,
  draftFromClips,
  isDraftDirty,
  layoutClips,
  moveClip,
  removeClip,
  toCustomClips,
  updateClip,
  validateClip,
  validateDraft,
} from "@/features/timeline-editor/clip-draft";

type Clip = TimelineSequence["manifest"]["video_clips"][number];

function clip(index: number, start: number, duration: number, overrides: Partial<Clip> = {}): Clip {
  return {
    order_index: index + 1,
    shot_id: `shot-${index + 1}`,
    take_id: `take-${index + 1}`,
    asset_id: `asset-${index + 1}`,
    start_time_seconds: start,
    source_duration_seconds: duration,
    trim_start_seconds: 0,
    trim_end_seconds: 0,
    effective_duration_seconds: duration,
    transition_type: "hard_cut",
    transition_duration_seconds: 0,
    volume: 1,
    fade_in_seconds: 0,
    fade_out_seconds: 0,
    ...overrides,
  };
}

const THREE = [clip(0, 0, 4), clip(1, 4, 5), clip(2, 9, 4)];

describe("draftFromClips", () => {
  it("orders by order_index and treats back-to-back clips as sequential placement", () => {
    const { clips, normalizedOverlaps } = draftFromClips([THREE[2]!, THREE[0]!, THREE[1]!]);
    expect(clips.map((c) => c.shotId)).toEqual(["shot-1", "shot-2", "shot-3"]);
    expect(clips.map((c) => c.gapBeforeSeconds)).toEqual([0, 0, 0]);
    expect(normalizedOverlaps).toEqual([]);
    expect(toCustomClips(clips).map((c) => c.start_time_seconds)).toEqual([null, null, null]);
  });

  it("recovers an explicit gap and a sequential cross dissolve from absolute starts", () => {
    const { clips } = draftFromClips([
      clip(0, 0, 4),
      // Starts 2 s after clip 1 ends: a black gap.
      clip(1, 6, 5),
      // Starts 1 s before clip 2 ends with a 1 s dissolve: sequential placement.
      clip(2, 10, 4, { transition_type: "cross_dissolve", transition_duration_seconds: 1 }),
    ]);
    expect(clips.map((c) => c.gapBeforeSeconds)).toEqual([0, 2, 0]);
    expect(layoutClips(clips).layouts.map((l) => l.startSeconds)).toEqual([0, 6, 10]);
    expect(toCustomClips(clips).map((c) => c.start_time_seconds)).toEqual([null, 6, null]);
  });

  it("flags a saved overlap that the exporter would refuse", () => {
    const { clips, normalizedOverlaps } = draftFromClips([clip(0, 0, 4), clip(1, 3, 5)]);
    expect(normalizedOverlaps).toEqual([2]);
    expect(layoutClips(clips).layouts[1]!.startSeconds).toBe(4);
  });
});

describe("layout and serialization", () => {
  it("shifts later clips when an earlier clip is trimmed and renumbers order_index", () => {
    const saved = draftFromClips(THREE).clips;
    const edited = updateClip(saved, saved[0]!.key, { trimStartSeconds: 1, trimEndSeconds: 0.5 });
    const { layouts, totalSeconds } = layoutClips(edited);
    expect(layouts.map((l) => l.startSeconds)).toEqual([0, 2.5, 7.5]);
    expect(totalSeconds).toBe(11.5);
    const body = toCustomClips(edited);
    expect(body.map((c) => c.order_index)).toEqual([1, 2, 3]);
    expect(body[0]).toMatchObject({ trim_start_seconds: 1, trim_end_seconds: 0.5 });
    expect(isDraftDirty(saved, edited)).toBe(true);
    expect(isDraftDirty(saved, saved)).toBe(false);
  });

  it("always serializes the first clip as a hard cut with no dissolve", () => {
    const saved = draftFromClips(THREE).clips;
    const withDissolve = updateClip(saved, saved[1]!.key, {
      transitionType: "cross_dissolve",
      transitionDurationSeconds: 1,
    });
    const moved = moveClip(withDissolve, 1, 0);
    expect(toCustomClips(moved)[0]).toMatchObject({
      transition_type: "hard_cut",
      transition_duration_seconds: 0,
    });
    expect(validateDraft(moved)[0]).toEqual({});
  });

  it("drops an inherited gap when a clip moves but keeps a gap the user typed", () => {
    const saved = draftFromClips([clip(0, 0, 4), clip(1, 6, 5), clip(2, 11, 4)]).clips;
    expect(saved[1]!.gapBeforeSeconds).toBe(2);
    expect(moveClip(saved, 1, 2)[2]!.gapBeforeSeconds).toBe(0);

    const typed = updateClip(saved, saved[2]!.key, { gapBeforeSeconds: 1.5 });
    const moved = moveClip(typed, 2, 0);
    expect(moved[0]!.gapBeforeSeconds).toBe(1.5);
    expect(toCustomClips(moved)[0]!.start_time_seconds).toBe(1.5);
  });

  it("removes a clip by key", () => {
    const saved = draftFromClips(THREE).clips;
    expect(removeClip(saved, saved[1]!.key).map((c) => c.shotId)).toEqual(["shot-1", "shot-3"]);
  });
});

describe("validateClip", () => {
  const saved = draftFromClips(THREE).clips;

  it("blocks trims that consume the source duration", () => {
    const edited = updateClip(saved, saved[0]!.key, { trimStartSeconds: 2, trimEndSeconds: 2 });
    expect(validateClip(edited, 0).trims).toMatch(/must leave at least 0.1 s of the 4 s source/);
  });

  it("rejects empty, negative and out-of-range trims", () => {
    const edited = updateClip(saved, saved[0]!.key, {
      trimStartSeconds: Number.NaN,
      trimEndSeconds: -1,
    });
    const errors = validateClip(edited, 0);
    expect(errors.trimStart).toMatch(/Enter a number/);
    expect(errors.trimEnd).toMatch(/between 0 and 600/);
  });

  it("bounds the dissolve by 5 s and by both neighbouring clips", () => {
    const tooLong = updateClip(saved, saved[1]!.key, {
      transitionType: "cross_dissolve",
      transitionDurationSeconds: 4,
    });
    expect(validateClip(tooLong, 1).transition).toMatch(/shorter than this clip and the previous/);
    const overFive = updateClip(saved, saved[1]!.key, {
      transitionType: "cross_dissolve",
      transitionDurationSeconds: 6,
    });
    expect(validateClip(overFive, 1).transition).toMatch(/at most 5 s/);
    const ok = updateClip(saved, saved[1]!.key, {
      transitionType: "cross_dissolve",
      transitionDurationSeconds: 1,
    });
    expect(validateClip(ok, 1)).toEqual({});
  });

  it("rejects a dissolve across a gap and tiny gaps", () => {
    const dissolve = updateClip(saved, saved[1]!.key, {
      transitionType: "cross_dissolve",
      transitionDurationSeconds: 1,
    });
    const withGap = updateClip(dissolve, saved[1]!.key, { gapBeforeSeconds: 1 });
    expect(validateClip(withGap, 1).transition).toMatch(/follow the previous clip directly/);
    const tiny = updateClip(saved, saved[2]!.key, { gapBeforeSeconds: 0.05 });
    expect(validateClip(tiny, 2).gap).toMatch(/at least 0.1 s/);
  });
});

describe("clipErrorFromApi", () => {
  it("maps an ownership 422 string to the clip position", () => {
    const error = new ApiError({
      status: 422,
      detail: "custom_clips[1]: take_id không thuộc shot_id đã chọn.",
    });
    expect(clipErrorFromApi(error)).toEqual({
      index: 1,
      message: "take_id không thuộc shot_id đã chọn.",
    });
  });

  it("maps a Pydantic validation item to the clip position", () => {
    const error = new ApiError({
      status: 422,
      detail: [{ loc: ["body", "custom_clips", 2], msg: "Value error, trims too long" }],
    });
    expect(clipErrorFromApi(error)).toEqual({ index: 2, message: "Value error, trims too long" });
  });

  it("ignores unrelated errors", () => {
    expect(clipErrorFromApi(new ApiError({ status: 422, detail: "Project has no shots" }))).toBe(
      null,
    );
    expect(clipErrorFromApi(new ApiError({ status: 500, detail: "custom_clips[0]: x" }))).toBe(
      null,
    );
  });
});

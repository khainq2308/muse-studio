import { describe, expect, it } from "vitest";
import {
  MIN_TICK_SPACING_PX,
  formatTimecode,
  pixelsPerSecondFor,
  rulerTicks,
} from "@/features/timeline-editor/timecode";

describe("formatTimecode", () => {
  it.each([
    [0, "00:00:00"],
    [5, "00:00:05"],
    [12.9, "00:00:12"],
    [59.999, "00:00:59"],
    [600, "00:10:00"],
    [3599, "00:59:59"],
    [3700, "01:01:40"],
    [36_000 + 61, "10:01:01"],
  ])("formats %s seconds as %s", (seconds, expected) => {
    expect(formatTimecode(seconds)).toBe(expected);
  });

  it("appends a frame field derived from the fps", () => {
    expect(formatTimecode(3700.5, 24)).toBe("01:01:40:12");
    expect(formatTimecode(1.999, 25)).toBe("00:00:01:24");
    expect(formatTimecode(4, 24)).toBe("00:00:04:00");
  });

  it("clamps negative and non-finite input to zero", () => {
    expect(formatTimecode(-3)).toBe("00:00:00");
    expect(formatTimecode(Number.NaN, 24)).toBe("00:00:00:00");
  });
});

describe("rulerTicks", () => {
  it("never extends past the sequence duration", () => {
    const ticks = rulerTicks(13, 40);
    expect(ticks).toEqual([0, 2, 4, 6, 8, 10, 12]);
    expect(ticks.map((tick) => formatTimecode(tick))).not.toContain("00:00:25");
  });

  it("spaces labels at least MIN_TICK_SPACING_PX apart at any scale", () => {
    for (const pixelsPerSecond of [16, 40, 73, 240]) {
      const ticks = rulerTicks(3700, pixelsPerSecond);
      const spacing = (ticks[1]! - ticks[0]!) * pixelsPerSecond;
      expect(spacing).toBeGreaterThanOrEqual(MIN_TICK_SPACING_PX);
      expect(ticks[ticks.length - 1]).toBeLessThanOrEqual(3700);
    }
  });

  it("returns a single zero tick for an empty sequence", () => {
    expect(rulerTicks(0, 40)).toEqual([0]);
  });
});

describe("pixelsPerSecondFor", () => {
  it("scales so the shortest clip stays readable, within bounds", () => {
    expect(pixelsPerSecondFor([4, 5, 4])).toBe(40);
    expect(pixelsPerSecondFor([0.1])).toBe(240);
    expect(pixelsPerSecondFor([600])).toBe(16);
    expect(pixelsPerSecondFor([])).toBe(40);
  });
});

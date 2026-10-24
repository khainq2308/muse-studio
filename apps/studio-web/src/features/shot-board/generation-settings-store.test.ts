import { describe, expect, it } from "vitest";
import {
  EMPTY_GENERATION_SETTINGS,
  withDefaultConnection,
} from "@/features/shot-board/generation-settings-store";
import { makeConnection } from "@/features/shot-board/test-fixtures";

describe("withDefaultConnection", () => {
  it("chooses the only enabled connection and its first discovered model", () => {
    expect(
      withDefaultConnection(EMPTY_GENERATION_SETTINGS, [
        makeConnection(),
        makeConnection({ id: "conn-off", enabled: false }),
      ]),
    ).toEqual({ connectionId: "conn-mock", modelId: "muse-video-mock-v1", taskType: "video" });
  });

  it("does not choose between several enabled connections", () => {
    const two = [makeConnection(), makeConnection({ id: "conn-live" })];
    expect(withDefaultConnection(EMPTY_GENERATION_SETTINGS, two)).toBe(EMPTY_GENERATION_SETTINGS);
  });

  it("never overrides a choice the user made while it is still enabled", () => {
    const chosen = { connectionId: "conn-x", modelId: "m", taskType: "image" as const };
    expect(
      withDefaultConnection(chosen, [makeConnection(), makeConnection({ id: "conn-x" })]),
    ).toBe(chosen);
  });

  it("drops a stored connection that was disabled or deleted, and re-applies the default", () => {
    const stale = { connectionId: "conn-old", modelId: "m", taskType: "video" as const };
    expect(withDefaultConnection(stale, [makeConnection()])).toEqual({
      connectionId: "conn-mock",
      modelId: "m",
      taskType: "video",
    });
    expect(
      withDefaultConnection(stale, [makeConnection(), makeConnection({ id: "conn-live" })]),
    ).toEqual({ ...stale, connectionId: "" });
  });

  it("keeps the stored value while connections are still loading", () => {
    const stale = { connectionId: "conn-old", modelId: "m", taskType: "video" as const };
    expect(withDefaultConnection(stale, undefined)).toBe(stale);
  });
});

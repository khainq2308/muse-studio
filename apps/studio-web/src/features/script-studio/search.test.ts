import { describe, expect, it } from "vitest";
import { defaultScriptStudioView, parseScriptStudioView } from "@/features/script-studio/search";

describe("parseScriptStudioView", () => {
  it("keeps known views and drops anything else", () => {
    expect(parseScriptStudioView("script")).toBe("script");
    expect(parseScriptStudioView("bible")).toBe("bible");
    expect(parseScriptStudioView("Bible")).toBeUndefined();
    expect(parseScriptStudioView(1)).toBeUndefined();
    expect(parseScriptStudioView(undefined)).toBeUndefined();
  });
});

describe("defaultScriptStudioView", () => {
  const none = { hasEntries: false, hasShots: false, bibleFailed: false };

  it("starts an empty project on the script import", () => {
    expect(defaultScriptStudioView(none)).toBe("script");
  });

  it("opens the Story Bible once the project has entries or shots", () => {
    expect(defaultScriptStudioView({ ...none, hasEntries: true })).toBe("bible");
    expect(defaultScriptStudioView({ ...none, hasShots: true })).toBe("bible");
  });

  it("opens the Story Bible when it failed to load, so the error is visible", () => {
    expect(defaultScriptStudioView({ ...none, bibleFailed: true })).toBe("bible");
  });
});

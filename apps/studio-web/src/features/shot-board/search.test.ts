import { describe, expect, it } from "vitest";
import { validateShotBoardSearch } from "@/features/shot-board/search";

describe("validateShotBoardSearch", () => {
  it("keeps a shot ID and a known tab", () => {
    expect(validateShotBoardSearch({ shot: "shot-2", tab: "prompt" })).toEqual({
      shot: "shot-2",
      tab: "prompt",
    });
  });

  it("drops unknown tabs, blank or non-string shots and unrelated keys", () => {
    // Keys are present but undefined so they override the raw values the router merges in.
    const dropped = validateShotBoardSearch({ shot: "  ", tab: "storyboard", page: 2 });
    expect(dropped).toStrictEqual({ shot: undefined, tab: undefined });
    expect(validateShotBoardSearch({ shot: { id: "x" }, tab: 3 })).toEqual({});
  });

  it("restores a numeric-looking shot ID that the router JSON-decoded", () => {
    expect(validateShotBoardSearch({ shot: 42 })).toEqual({ shot: "42", tab: undefined });
  });
});

import { describe, expect, it } from "vitest";
import {
  entriesNeedingReference,
  isBlockingMissingReference,
} from "@/features/script-studio/attention";
import type { StoryBibleEntry } from "@/features/script-studio/state";

function entry(overrides: Partial<StoryBibleEntry>): StoryBibleEntry {
  return {
    id: `bib_${overrides.code_name ?? "x"}`,
    entry_type: "character",
    code_name: "char_x",
    display_name: "X",
    immutable_traits: "",
    mutable_attributes: {},
    reference_asset_ids: [],
    requires_reference_lock: true,
    ...overrides,
  };
}

type Shot = Parameters<typeof entriesNeedingReference>[1][number];
const CHARACTER_REF_BLOCKER = {
  code: "BLOCKED_REFERENCE",
  severity: "blocker" as const,
  message: "Thiếu ref asset 'MISSING_REQUIRED_CHAR_REF'.",
  field: "subject_ids",
};
function shot(subjects: string[], lint: Shot["lint_report"] = [CHARACTER_REF_BLOCKER]): Shot {
  return { subject_codes: subjects, lint_report: lint };
}

describe("isBlockingMissingReference", () => {
  it("only flags characters that require a reference and have none", () => {
    expect(isBlockingMissingReference(entry({}))).toBe(true);
    expect(isBlockingMissingReference(entry({ reference_asset_ids: ["a1"] }))).toBe(false);
    expect(isBlockingMissingReference(entry({ requires_reference_lock: false }))).toBe(false);
    // The compiler does not check references of locations, styles or props.
    for (const entry_type of ["location", "style", "prop"] as const) {
      expect(isBlockingMissingReference(entry({ entry_type }))).toBe(false);
    }
  });
});

describe("entriesNeedingReference", () => {
  it("counts shots with a character-reference blocker using each character, most first", () => {
    const linh = entry({ code_name: "char_linh", display_name: "Linh" });
    const minh = entry({ code_name: "char_minh", display_name: "Minh" });
    const items = entriesNeedingReference(
      [linh, minh, entry({ code_name: "loc_dock", entry_type: "location" })],
      [
        shot(["char_minh"]),
        shot(["char_minh", "char_linh"]),
        // No character-reference blocker (only a shot-level reference blocker): not counted.
        shot(["char_linh"], [{ ...CHARACTER_REF_BLOCKER, field: "reference_asset_ids" }]),
        // Blocked for another reason entirely: not counted.
        shot(["char_linh"], [{ ...CHARACTER_REF_BLOCKER, code: "CAMERA_MOTION_CONFLICT" }]),
      ],
    );
    expect(items.map((item) => [item.entry.code_name, item.blockedShots])).toEqual([
      ["char_minh", 2],
      ["char_linh", 1],
    ]);
  });

  it("reports a character with no blocked shots", () => {
    expect(entriesNeedingReference([entry({ code_name: "char_x" })], [shot(["char_y"])])).toEqual([
      expect.objectContaining({ blockedShots: 0 }),
    ]);
  });
});

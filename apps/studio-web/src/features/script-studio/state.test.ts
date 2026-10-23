import { describe, expect, it } from "vitest";
import {
  bibleEntryFormDefaults,
  buildBibleEntryCreateRequest,
  buildScriptImportRequest,
  filterStoryBibleEntries,
  mapBibleEntryError,
  mapScriptImportError,
  parseListText,
  referenceUploadErrorMessage,
  shotExportErrorMessage,
  sortStoryBibleEntries,
  storyBibleEntryTypeLabel,
  storyBibleEntryTypeTone,
  summarizeMutableAttributes,
  type StoryBibleEntry,
} from "@/features/script-studio/state";
import { ApiError } from "@/lib/api/client";

function makeEntry(overrides: Partial<StoryBibleEntry> = {}): StoryBibleEntry {
  return {
    id: "bib_1",
    entry_type: "character",
    code_name: "char_linh",
    display_name: "Detective Linh",
    immutable_traits: "2D anime detective with sharp silver eyes",
    mutable_attributes: { current_outfit: "charcoal trench coat" },
    reference_asset_ids: ["ast_ref1"],
    requires_reference_lock: true,
    ...overrides,
  };
}

describe("Script Studio state helpers", () => {
  it("sorts and filters Story Bible entries by category and code_name", () => {
    const entries: StoryBibleEntry[] = [
      makeEntry({ id: "bib_3", entry_type: "style", code_name: "style_noir" }),
      makeEntry({ id: "bib_2", entry_type: "character", code_name: "char_minh" }),
      makeEntry({ id: "bib_1", entry_type: "character", code_name: "char_linh" }),
      makeEntry({ id: "bib_4", entry_type: "location", code_name: "loc_alley" }),
      makeEntry({ id: "bib_5", entry_type: "prop", code_name: "prop_key" }),
    ];

    expect(sortStoryBibleEntries(entries).map((entry) => entry.code_name)).toEqual([
      "char_linh",
      "char_minh",
      "loc_alley",
      "style_noir",
      "prop_key",
    ]);

    expect(filterStoryBibleEntries(entries, "character").map((entry) => entry.code_name)).toEqual([
      "char_linh",
      "char_minh",
    ]);
    expect(filterStoryBibleEntries(entries, "style").map((entry) => entry.code_name)).toEqual([
      "style_noir",
    ]);
  });

  it("formats known and unknown entry types safely", () => {
    expect(storyBibleEntryTypeLabel("character")).toBe("Character");
    expect(storyBibleEntryTypeLabel("custom_type")).toBe("Unknown type (custom_type)");
    expect(storyBibleEntryTypeTone("style")).toContain("text-warning");
    expect(storyBibleEntryTypeTone("custom_type")).toContain("text-muted-foreground");
  });

  it("parses comma and newline separated terms without duplicates", () => {
    expect(parseListText("3d cgi, deformed hands\n 3d cgi , photorealistic ")).toEqual([
      "3d cgi",
      "deformed hands",
      "photorealistic",
    ]);
  });

  it("builds a style BibleEntryCreateIn, preserves extra style keys, and explicitly sends empty constraint arrays when cleared", () => {
    const existingStyle = makeEntry({
      entry_type: "style",
      code_name: "style_main",
      display_name: "2D Anime Noir",
      immutable_traits: "hand-drawn cel shading",
      mutable_attributes: {
        color_palette: "teal and amber",
        negative_constraints: ["3d cgi", "lowres"],
        forbidden_terms: ["photorealistic"],
        custom_grain: "fine 35mm",
      },
      reference_asset_ids: [],
      requires_reference_lock: false,
    });

    const defaults = bibleEntryFormDefaults(existingStyle);
    expect(defaults.color_palette).toBe("teal and amber");
    expect(defaults.negative_constraints_text).toBe("3d cgi, lowres");
    expect(defaults.forbidden_terms_text).toBe("photorealistic");

    const request = buildBibleEntryCreateRequest(
      {
        ...defaults,
        current_outfit: "should_not_be_sent_for_style",
        time_of_day: "should_not_be_sent_for_style",
        color_palette: "  deep neon contrast ",
        negative_constraints_text: "   ",
        forbidden_terms_text: "live action",
      },
      existingStyle,
    );

    expect(request).toEqual({
      entry_type: "style",
      code_name: "style_main",
      display_name: "2D Anime Noir",
      immutable_traits: "hand-drawn cel shading",
      mutable_attributes: {
        color_palette: "deep neon contrast",
        negative_constraints: [],
        forbidden_terms: ["live action"],
        custom_grain: "fine 35mm",
      },
      reference_asset_ids: [],
      requires_reference_lock: false,
    });
  });

  it("preserves custom mutable_attributes on non-style entries while updating structured fields", () => {
    const existingCharacter = makeEntry({
      mutable_attributes: {
        current_outfit: "old coat",
        voice_tone: "calm baritone",
      },
    });
    const defaults = bibleEntryFormDefaults(existingCharacter);
    const request = buildBibleEntryCreateRequest(
      {
        ...defaults,
        current_outfit: "rain-soaked cloak",
      },
      existingCharacter,
    );

    expect(request.mutable_attributes).toEqual({
      current_outfit: "rain-soaked cloak",
      voice_tone: "calm baritone",
    });
  });

  it("summarizes mutable attributes for Story Bible cards", () => {
    const style = makeEntry({
      entry_type: "style",
      mutable_attributes: {
        color_palette: "amber noir",
        negative_constraints: ["3d cgi", "blurry"],
      },
    });
    expect(summarizeMutableAttributes(style)).toEqual([
      { label: "Color palette", value: "amber noir" },
      { label: "Negative constraints", value: "3d cgi, blurry" },
    ]);
  });

  it("clears replace_existing when auto_apply_proposal is false in buildScriptImportRequest", () => {
    expect(
      buildScriptImportRequest({
        raw_content: "SCENE 1: Office\nSHOT 1: Desk",
        source_format: "museflow",
        auto_apply_proposal: false,
        replace_existing: true,
      }),
    ).toEqual({
      raw_content: "SCENE 1: Office\nSHOT 1: Desk",
      source_format: "museflow",
      auto_apply_proposal: false,
      replace_existing: false,
    });
  });

  it("maps Story Bible 422 validation and 409 conflict errors to fields without duplicating formError", () => {
    const validationError = new ApiError({
      status: 422,
      detail: [
        {
          loc: ["body", "mutable_attributes", "color_palette"],
          msg: "String should have at most 255 characters",
        },
        {
          loc: ["body", "negative_constraints", 0],
          msg: "String should have at least 1 character",
        },
        {
          loc: ["body", "code_name"],
          msg: "String should have at least 2 characters",
        },
      ],
    });
    expect(mapBibleEntryError(validationError)).toEqual({
      fieldErrors: {
        color_palette: "String should have at most 255 characters",
        negative_constraints_text: "String should have at least 1 character",
        code_name: "String should have at least 2 characters",
      },
      formError: null,
    });

    const conflictError = new ApiError({
      status: 409,
      detail: "code_name 'char_linh' already belongs to a 'character' entry.",
    });
    expect(mapBibleEntryError(conflictError)).toEqual({
      fieldErrors: {
        code_name: "code_name 'char_linh' already belongs to a 'character' entry.",
      },
      formError: null,
    });
  });

  it("maps script import 409 replace_existing confirmation and 422 parse errors without duplicating formError", () => {
    const replaceConflict = new ApiError({
      status: 409,
      detail:
        "Re-applying a script would delete every existing shot together with its generated takes. Retry with replace_existing=true to confirm.",
    });
    expect(mapScriptImportError(replaceConflict)).toEqual({
      fieldErrors: {
        replace_existing:
          "Re-applying a script would delete every existing shot together with its generated takes. Retry with replace_existing=true to confirm.",
      },
      formError: null,
      requiresReplaceConfirmation: true,
    });

    const parseError = new ApiError({
      status: 422,
      detail: "SCENE number '99999' is out of range (1-10000).",
    });
    expect(mapScriptImportError(parseError)).toEqual({
      fieldErrors: {
        raw_content: "SCENE number '99999' is out of range (1-10000).",
      },
      formError: null,
      requiresReplaceConfirmation: false,
    });
  });

  it("formats upload and shot export error messages", () => {
    expect(
      referenceUploadErrorMessage(
        new ApiError({ status: 413, detail: "File vượt giới hạn dung lượng." }),
      ),
    ).toBe("Could not upload the reference image: File vượt giới hạn dung lượng.");

    expect(
      shotExportErrorMessage(new ApiError({ status: 401, detail: "Unauthorized" }), "csv"),
    ).toBe(
      "Could not export shots (CSV): the admin token was rejected. Update the token and try again.",
    );
  });
});

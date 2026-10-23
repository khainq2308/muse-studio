import { ApiError } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type StoryBibleEntry = components["schemas"]["StoryBibleEntryOut"];
export type StoryBibleEntryType = StoryBibleEntry["entry_type"];
export type BibleEntryCreateRequest = components["schemas"]["BibleEntryCreateIn"];
export type StoryBibleUpsertResult = components["schemas"]["StoryBibleUpsertOut"];
export type ScriptImportRequest = components["schemas"]["ScriptImportIn"];
export type ScriptImportResult = components["schemas"]["ScriptImportOut"];
export type UploadedAsset = components["schemas"]["AssetOut"];
export type ShotExportFormat = "json" | "csv";

export const STORY_BIBLE_ENTRY_TYPES = [
  "character",
  "location",
  "style",
  "prop",
] as const satisfies readonly StoryBibleEntryType[];

export type StoryBibleFilterType = "all" | StoryBibleEntryType;

export const STORY_BIBLE_ENTRY_LABELS: Record<StoryBibleEntryType, string> = {
  character: "Character",
  location: "Location",
  style: "Style",
  prop: "Prop",
};

const STORY_BIBLE_ENTRY_TONES: Record<StoryBibleEntryType, string> = {
  character: "border border-primary/30 bg-primary/10 text-primary",
  location: "border border-success/30 bg-success/10 text-success",
  style: "border border-warning/30 bg-warning/10 text-warning",
  prop: "border border-border bg-secondary text-secondary-foreground",
};

const UNKNOWN_TONE = "border border-border bg-muted text-muted-foreground";

/** In-browser preview limit for reference image assets (20 MB). */
export const REFERENCE_PREVIEW_MAX_BYTES = 20 * 1024 * 1024;

/** Backend `BibleEntryCreateIn` and `StyleMutableAttributesIn` validation bounds. */
export const BIBLE_ENTRY_LIMITS = {
  codeNameMinChars: 2,
  codeNameMaxChars: 128,
  displayNameMinChars: 1,
  displayNameMaxChars: 255,
  immutableTraitsMaxChars: 8_000,
  mutableAttributesMaxJsonChars: 32_768,
  mutableStringAttrMaxChars: 512,
  maxReferenceAssets: 32,
  referenceAssetIdMaxChars: 64,
  styleColorPaletteMaxChars: 255,
  styleMaxListItems: 64,
  styleListItemMaxChars: 255,
  allowedReferenceMimeTypes: ["image/png", "image/jpeg", "image/webp"] as const,
} as const;

/** Backend `ScriptImportIn` validation bounds (`MAX_SCRIPT_CHARS` in `script_parser.py`). */
export const SCRIPT_IMPORT_LIMITS = {
  minScriptChars: 5,
  maxScriptChars: 1_000_000,
  sourceFormatMaxChars: 32,
} as const;

export const SCRIPT_SOURCE_FORMATS = ["museflow", "markdown", "plaintext"] as const;
export type ScriptSourceFormat = (typeof SCRIPT_SOURCE_FORMATS)[number];

export const SCRIPT_SOURCE_FORMAT_LABELS: Record<ScriptSourceFormat, string> = {
  museflow: "MuseFlow (SCENE / SHOT blocks)",
  markdown: "Markdown screenplay",
  plaintext: "Plaintext prose",
};

function hasKey<T extends string>(record: Record<T, string>, key: string): key is T {
  return Object.prototype.hasOwnProperty.call(record, key);
}

export function isKnownStoryBibleEntryType(value: string): value is StoryBibleEntryType {
  return hasKey(STORY_BIBLE_ENTRY_LABELS, value);
}

export function storyBibleEntryTypeLabel(entryType: string): string {
  return isKnownStoryBibleEntryType(entryType)
    ? STORY_BIBLE_ENTRY_LABELS[entryType]
    : `Unknown type (${entryType || "empty"})`;
}

export function storyBibleEntryTypeTone(entryType: string): string {
  return isKnownStoryBibleEntryType(entryType) ? STORY_BIBLE_ENTRY_TONES[entryType] : UNKNOWN_TONE;
}

const ENTRY_TYPE_ORDER: Record<StoryBibleEntryType, number> = {
  character: 0,
  location: 1,
  style: 2,
  prop: 3,
};

/**
 * Sort Story Bible entries deterministically by category (`character`, `location`, `style`,
 * `prop`) and then by `code_name`.
 */
export function sortStoryBibleEntries(entries: readonly StoryBibleEntry[]): StoryBibleEntry[] {
  return [...entries].sort((left, right) => {
    const leftOrder = isKnownStoryBibleEntryType(left.entry_type)
      ? ENTRY_TYPE_ORDER[left.entry_type]
      : 99;
    const rightOrder = isKnownStoryBibleEntryType(right.entry_type)
      ? ENTRY_TYPE_ORDER[right.entry_type]
      : 99;
    return (
      leftOrder - rightOrder ||
      left.code_name.localeCompare(right.code_name) ||
      left.id.localeCompare(right.id)
    );
  });
}

export function filterStoryBibleEntries(
  entries: readonly StoryBibleEntry[],
  filter: StoryBibleFilterType,
): StoryBibleEntry[] {
  const sorted = sortStoryBibleEntries(entries);
  if (filter === "all") return sorted;
  return sorted.filter((entry) => entry.entry_type === filter);
}

/** Split comma- or newline-separated terms into unique non-empty strings. */
export function parseListText(text: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of text.split(/[\n,]+/)) {
    const item = raw.trim();
    if (!item || seen.has(item)) continue;
    seen.add(item);
    result.push(item);
  }
  return result;
}

function readStringAttr(
  attrs: StoryBibleEntry["mutable_attributes"] | undefined,
  key: string,
): string {
  const value = attrs?.[key];
  return typeof value === "string" ? value : "";
}

function readStringListAttr(
  attrs: StoryBibleEntry["mutable_attributes"] | undefined,
  key: string,
): string[] {
  const value = attrs?.[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

export interface BibleEntryFormValues {
  entry_type: StoryBibleEntryType;
  code_name: string;
  display_name: string;
  immutable_traits: string;
  requires_reference_lock: boolean;
  reference_asset_ids: string[];
  current_outfit: string;
  time_of_day: string;
  lighting_rule: string;
  color_palette: string;
  negative_constraints_text: string;
  forbidden_terms_text: string;
}

export function bibleEntryFormDefaults(entry?: StoryBibleEntry | null): BibleEntryFormValues {
  const attrs = entry?.mutable_attributes;
  return {
    entry_type:
      entry && isKnownStoryBibleEntryType(entry.entry_type) ? entry.entry_type : "character",
    code_name: entry?.code_name ?? "",
    display_name: entry?.display_name ?? "",
    immutable_traits: entry?.immutable_traits ?? "",
    requires_reference_lock: entry?.requires_reference_lock ?? false,
    reference_asset_ids: [...(entry?.reference_asset_ids ?? [])],
    current_outfit: readStringAttr(attrs, "current_outfit"),
    time_of_day: readStringAttr(attrs, "time_of_day"),
    lighting_rule: readStringAttr(attrs, "lighting_rule"),
    color_palette: readStringAttr(attrs, "color_palette"),
    negative_constraints_text: readStringListAttr(attrs, "negative_constraints").join(", "),
    forbidden_terms_text: readStringListAttr(attrs, "forbidden_terms").join(", "),
  };
}

/**
 * Build a `BibleEntryCreateIn` request body from the form values.
 *
 * Existing extra attributes on an edited entry of the same `entry_type` are preserved alongside
 * the structured fields (`StyleMutableAttributesIn` uses `extra="allow"`). For `style` entries,
 * `negative_constraints` and `forbidden_terms` are always sent explicitly (even as `[]`) so
 * clearing the input removes the constraint instead of falling back to the backend default.
 */
export function buildBibleEntryCreateRequest(
  values: BibleEntryFormValues,
  existingEntry?: StoryBibleEntry | null,
): BibleEntryCreateRequest {
  const mutableAttributes: NonNullable<BibleEntryCreateRequest["mutable_attributes"]> = {};

  if (existingEntry && existingEntry.entry_type === values.entry_type) {
    for (const [key, val] of Object.entries(existingEntry.mutable_attributes ?? {})) {
      mutableAttributes[key] = val;
    }
  }

  if (values.entry_type === "style") {
    const palette = values.color_palette.trim();
    if (palette) mutableAttributes.color_palette = palette;
    else delete mutableAttributes.color_palette;
    mutableAttributes.negative_constraints = parseListText(values.negative_constraints_text);
    mutableAttributes.forbidden_terms = parseListText(values.forbidden_terms_text);
  } else if (values.entry_type === "character") {
    const outfit = values.current_outfit.trim();
    if (outfit) mutableAttributes.current_outfit = outfit;
    else delete mutableAttributes.current_outfit;
  } else if (values.entry_type === "location") {
    const timeOfDay = values.time_of_day.trim();
    if (timeOfDay) mutableAttributes.time_of_day = timeOfDay;
    else delete mutableAttributes.time_of_day;
    const lightingRule = values.lighting_rule.trim();
    if (lightingRule) mutableAttributes.lighting_rule = lightingRule;
    else delete mutableAttributes.lighting_rule;
  }

  return {
    entry_type: values.entry_type,
    code_name: values.code_name.trim(),
    display_name: values.display_name.trim(),
    immutable_traits: values.immutable_traits.trim(),
    mutable_attributes: mutableAttributes,
    reference_asset_ids: values.reference_asset_ids
      .map((id) => id.trim())
      .filter((id) => id.length > 0),
    requires_reference_lock: values.requires_reference_lock,
  };
}

export interface MutableAttributeSummaryItem {
  label: string;
  value: string;
}

const KNOWN_ATTRIBUTE_LABELS: Record<string, string> = {
  current_outfit: "Current outfit",
  time_of_day: "Time of day",
  lighting_rule: "Lighting rule",
  color_palette: "Color palette",
  negative_constraints: "Negative constraints",
  forbidden_terms: "Forbidden terms",
};

/** Format `mutable_attributes` into readable key/value pairs for Story Bible cards. */
export function summarizeMutableAttributes(entry: StoryBibleEntry): MutableAttributeSummaryItem[] {
  const items: MutableAttributeSummaryItem[] = [];
  for (const [key, rawValue] of Object.entries(entry.mutable_attributes ?? {})) {
    if (rawValue === null || rawValue === undefined) continue;
    const label = KNOWN_ATTRIBUTE_LABELS[key] ?? key;
    if (Array.isArray(rawValue)) {
      const text = rawValue
        .map((item) => String(item))
        .filter(Boolean)
        .join(", ");
      if (text) items.push({ label, value: text });
    } else if (typeof rawValue === "object") {
      items.push({ label, value: JSON.stringify(rawValue) });
    } else {
      const text = String(rawValue).trim();
      if (text) items.push({ label, value: text });
    }
  }
  return items;
}

export interface ScriptImportFormValues {
  raw_content: string;
  source_format: string;
  auto_apply_proposal: boolean;
  replace_existing: boolean;
}

export function buildScriptImportRequest(values: ScriptImportFormValues): ScriptImportRequest {
  return {
    raw_content: values.raw_content,
    source_format: values.source_format.trim() || "museflow",
    auto_apply_proposal: values.auto_apply_proposal,
    replace_existing: values.auto_apply_proposal ? values.replace_existing : false,
  };
}

export type BibleEntryField =
  | "entry_type"
  | "code_name"
  | "display_name"
  | "immutable_traits"
  | "requires_reference_lock"
  | "reference_asset_ids"
  | "current_outfit"
  | "time_of_day"
  | "lighting_rule"
  | "color_palette"
  | "negative_constraints_text"
  | "forbidden_terms_text";

const BIBLE_ENTRY_FIELDS: readonly BibleEntryField[] = [
  "entry_type",
  "code_name",
  "display_name",
  "immutable_traits",
  "requires_reference_lock",
  "reference_asset_ids",
  "current_outfit",
  "time_of_day",
  "lighting_rule",
  "color_palette",
  "negative_constraints_text",
  "forbidden_terms_text",
];

function isBibleEntryField(value: unknown): value is BibleEntryField {
  return typeof value === "string" && (BIBLE_ENTRY_FIELDS as readonly string[]).includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export interface BibleEntryErrorMapping {
  fieldErrors: Partial<Record<BibleEntryField, string>>;
  formError: string | null;
}

const BIBLE_FIELD_HINTS: ReadonlyArray<[BibleEntryField, RegExp]> = [
  ["reference_asset_ids", /reference_asset_ids/i],
  ["color_palette", /color_palette/i],
  ["negative_constraints_text", /negative_constraints/i],
  ["forbidden_terms_text", /forbidden_terms/i],
  ["code_name", /code_name/i],
];

/**
 * Map a failed `POST /api/v1/projects/{project_id}/story-bible` to inline field and form errors.
 */
export function mapBibleEntryError(error: unknown): BibleEntryErrorMapping {
  if (!(error instanceof ApiError)) {
    return {
      fieldErrors: {},
      formError:
        error instanceof Error
          ? `Could not save the Story Bible entry: ${error.message}`
          : "Could not save the Story Bible entry.",
    };
  }
  if (error.status === 401 || error.status === 403) {
    return {
      fieldErrors: {},
      formError:
        error.status === 403
          ? "The current session does not have permission to edit the Story Bible."
          : "The admin token was rejected while saving the Story Bible entry. Update the token and try again.",
    };
  }
  if (error.status === 404) {
    return {
      fieldErrors: {},
      formError: "This project no longer exists. Refresh the workspace.",
    };
  }

  const fieldErrors: Partial<Record<BibleEntryField, string>> = {};
  const unmapped: string[] = [];

  if (Array.isArray(error.detail)) {
    for (const item of error.detail) {
      if (!isRecord(item)) continue;
      const message = typeof item.msg === "string" ? item.msg : "Invalid value.";
      const location = Array.isArray(item.loc) ? item.loc : [];
      const rawField = location[0] === "body" ? location[1] : location[0];
      if (isBibleEntryField(rawField)) {
        if (!fieldErrors[rawField]) fieldErrors[rawField] = message;
        continue;
      }
      if (rawField === "negative_constraints" && !fieldErrors.negative_constraints_text) {
        fieldErrors.negative_constraints_text = message;
        continue;
      }
      if (rawField === "forbidden_terms" && !fieldErrors.forbidden_terms_text) {
        fieldErrors.forbidden_terms_text = message;
        continue;
      }
      if (rawField === "mutable_attributes") {
        const subField = location[2];
        if (subField === "color_palette" && !fieldErrors.color_palette) {
          fieldErrors.color_palette = message;
          continue;
        }
        if (subField === "negative_constraints" && !fieldErrors.negative_constraints_text) {
          fieldErrors.negative_constraints_text = message;
          continue;
        }
        if (subField === "forbidden_terms" && !fieldErrors.forbidden_terms_text) {
          fieldErrors.forbidden_terms_text = message;
          continue;
        }
      }
      let matchedHint = false;
      for (const [hintField, pattern] of BIBLE_FIELD_HINTS) {
        if (pattern.test(message)) {
          if (!fieldErrors[hintField]) fieldErrors[hintField] = message;
          matchedHint = true;
          break;
        }
      }
      if (!matchedHint) unmapped.push(message);
    }
    return {
      fieldErrors,
      formError: unmapped.length > 0 ? unmapped.join("; ") : null,
    };
  }

  const message = error.message;
  for (const [field, pattern] of BIBLE_FIELD_HINTS) {
    if (pattern.test(message)) {
      fieldErrors[field] = message;
      break;
    }
  }
  return {
    fieldErrors,
    formError: Object.keys(fieldErrors).length > 0 ? null : message,
  };
}

export type ScriptImportField =
  "raw_content" | "source_format" | "auto_apply_proposal" | "replace_existing";

const SCRIPT_IMPORT_FIELDS: readonly ScriptImportField[] = [
  "raw_content",
  "source_format",
  "auto_apply_proposal",
  "replace_existing",
];

function isScriptImportField(value: unknown): value is ScriptImportField {
  return typeof value === "string" && (SCRIPT_IMPORT_FIELDS as readonly string[]).includes(value);
}

export interface ScriptImportErrorMapping {
  fieldErrors: Partial<Record<ScriptImportField, string>>;
  formError: string | null;
  /** True when the backend refused re-import with 409 until `replace_existing=true` is confirmed. */
  requiresReplaceConfirmation: boolean;
}

/**
 * Map a failed `POST /api/v1/projects/{project_id}/scripts/import` to form/field errors.
 */
export function mapScriptImportError(error: unknown): ScriptImportErrorMapping {
  if (!(error instanceof ApiError)) {
    return {
      fieldErrors: {},
      formError:
        error instanceof Error
          ? `Could not import the script: ${error.message}`
          : "Could not import the script.",
      requiresReplaceConfirmation: false,
    };
  }
  if (error.status === 401 || error.status === 403) {
    return {
      fieldErrors: {},
      formError:
        error.status === 403
          ? "The current session does not have permission to import scripts."
          : "The admin token was rejected while importing the script. Update the token and try again.",
      requiresReplaceConfirmation: false,
    };
  }
  if (error.status === 404) {
    return {
      fieldErrors: {},
      formError: "This project no longer exists. Refresh the workspace.",
      requiresReplaceConfirmation: false,
    };
  }

  const fieldErrors: Partial<Record<ScriptImportField, string>> = {};
  const unmapped: string[] = [];

  if (Array.isArray(error.detail)) {
    for (const item of error.detail) {
      if (!isRecord(item)) continue;
      const message = typeof item.msg === "string" ? item.msg : "Invalid value.";
      const location = Array.isArray(item.loc) ? item.loc : [];
      const field = location[0] === "body" ? location[1] : undefined;
      if (isScriptImportField(field) && !fieldErrors[field]) fieldErrors[field] = message;
      else unmapped.push(message);
    }
    return {
      fieldErrors,
      formError: unmapped.length > 0 ? unmapped.join("; ") : null,
      requiresReplaceConfirmation: false,
    };
  }

  const message = error.message;
  const requiresReplaceConfirmation = error.status === 409 && /replace_existing/i.test(message);
  if (requiresReplaceConfirmation) {
    fieldErrors.replace_existing = message;
  } else if (error.status === 422 && /script|scene|character/i.test(message)) {
    fieldErrors.raw_content = message;
  }
  return {
    fieldErrors,
    formError: Object.keys(fieldErrors).length > 0 ? null : message,
    requiresReplaceConfirmation,
  };
}

export function referenceUploadErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error
      ? `Could not upload the reference image: ${error.message}`
      : "Could not upload the reference image.";
  }
  if (error.status === 401) {
    return "Could not upload the reference image: the admin token was rejected. Update the token and try again.";
  }
  if (error.status === 403) {
    return "Could not upload the reference image: the current session does not have permission.";
  }
  if (error.status === 413 || error.category === "too_large") {
    return `Could not upload the reference image: ${error.message}`;
  }
  return `Could not upload the reference image: ${error.message}`;
}

export function shotExportErrorMessage(error: unknown, format: ShotExportFormat): string {
  const label = format.toUpperCase();
  if (!(error instanceof ApiError)) {
    return error instanceof Error
      ? `Could not export shots (${label}): ${error.message}`
      : `Could not export shots (${label}).`;
  }
  if (error.status === 401) {
    return `Could not export shots (${label}): the admin token was rejected. Update the token and try again.`;
  }
  if (error.status === 403) {
    return `Could not export shots (${label}): the current session does not have permission.`;
  }
  return `Could not export shots (${label}): ${error.message}`;
}

import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, LoaderCircle, Plus, Save, Trash2, Upload, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { ReferenceAssetPreview } from "@/features/script-studio/components/reference-asset-preview";
import {
  BIBLE_ENTRY_LIMITS,
  STORY_BIBLE_ENTRY_LABELS,
  STORY_BIBLE_ENTRY_TYPES,
  bibleEntryFormDefaults,
  buildBibleEntryCreateRequest,
  parseListText,
  type BibleEntryCreateRequest,
  type BibleEntryField,
  type BibleEntryFormValues,
  type StoryBibleEntry,
  type StoryBibleUpsertResult,
  type UploadedAsset,
} from "@/features/script-studio/state";
import { cn } from "@/lib/utils";

function styleListRefinement(label: string) {
  return z
    .string()
    .refine(
      (value) => parseListText(value).length <= BIBLE_ENTRY_LIMITS.styleMaxListItems,
      `${label} may contain at most ${BIBLE_ENTRY_LIMITS.styleMaxListItems} items.`,
    )
    .refine(
      (value) =>
        parseListText(value).every(
          (item) => item.length <= BIBLE_ENTRY_LIMITS.styleListItemMaxChars,
        ),
      `Each item in ${label.toLowerCase()} must be at most ${BIBLE_ENTRY_LIMITS.styleListItemMaxChars} characters.`,
    );
}

function buildBibleEntryFormSchema(existingEntry: StoryBibleEntry | null) {
  return z
    .object({
      entry_type: z.enum(STORY_BIBLE_ENTRY_TYPES),
      code_name: z
        .string()
        .trim()
        .min(
          BIBLE_ENTRY_LIMITS.codeNameMinChars,
          `Code name must be at least ${BIBLE_ENTRY_LIMITS.codeNameMinChars} characters.`,
        )
        .max(
          BIBLE_ENTRY_LIMITS.codeNameMaxChars,
          `Code name must be at most ${BIBLE_ENTRY_LIMITS.codeNameMaxChars} characters.`,
        ),
      display_name: z
        .string()
        .trim()
        .min(BIBLE_ENTRY_LIMITS.displayNameMinChars, "Display name is required.")
        .max(
          BIBLE_ENTRY_LIMITS.displayNameMaxChars,
          `Display name must be at most ${BIBLE_ENTRY_LIMITS.displayNameMaxChars} characters.`,
        ),
      immutable_traits: z
        .string()
        .max(
          BIBLE_ENTRY_LIMITS.immutableTraitsMaxChars,
          `Immutable traits must be at most ${BIBLE_ENTRY_LIMITS.immutableTraitsMaxChars} characters.`,
        ),
      requires_reference_lock: z.boolean(),
      reference_asset_ids: z
        .array(
          z
            .string()
            .trim()
            .min(1, "Asset ID must not be empty.")
            .max(
              BIBLE_ENTRY_LIMITS.referenceAssetIdMaxChars,
              `Asset ID must be at most ${BIBLE_ENTRY_LIMITS.referenceAssetIdMaxChars} characters.`,
            ),
        )
        .max(
          BIBLE_ENTRY_LIMITS.maxReferenceAssets,
          `At most ${BIBLE_ENTRY_LIMITS.maxReferenceAssets} reference assets may be attached.`,
        ),
      current_outfit: z
        .string()
        .max(
          BIBLE_ENTRY_LIMITS.mutableStringAttrMaxChars,
          `Current outfit must be at most ${BIBLE_ENTRY_LIMITS.mutableStringAttrMaxChars} characters.`,
        ),
      time_of_day: z
        .string()
        .max(
          BIBLE_ENTRY_LIMITS.mutableStringAttrMaxChars,
          `Time of day must be at most ${BIBLE_ENTRY_LIMITS.mutableStringAttrMaxChars} characters.`,
        ),
      lighting_rule: z
        .string()
        .max(
          BIBLE_ENTRY_LIMITS.mutableStringAttrMaxChars,
          `Lighting rule must be at most ${BIBLE_ENTRY_LIMITS.mutableStringAttrMaxChars} characters.`,
        ),
      color_palette: z
        .string()
        .max(
          BIBLE_ENTRY_LIMITS.styleColorPaletteMaxChars,
          `Color palette must be at most ${BIBLE_ENTRY_LIMITS.styleColorPaletteMaxChars} characters.`,
        ),
      negative_constraints_text: styleListRefinement("Negative constraints"),
      forbidden_terms_text: styleListRefinement("Forbidden terms"),
    })
    .superRefine((values, ctx) => {
      const request = buildBibleEntryCreateRequest(values, existingEntry);
      const serialized = JSON.stringify(request.mutable_attributes ?? {});
      if (serialized.length > BIBLE_ENTRY_LIMITS.mutableAttributesMaxJsonChars) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["immutable_traits"],
          message: `Mutable attributes exceed ${BIBLE_ENTRY_LIMITS.mutableAttributesMaxJsonChars.toLocaleString()} JSON characters.`,
        });
      }
    }) satisfies z.ZodType<BibleEntryFormValues>;
}

const inputClass = cn(
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "aria-[invalid=true]:border-destructive disabled:cursor-not-allowed disabled:opacity-60",
);

interface StoryBibleEditorProps {
  /** Existing entry when editing; null when creating a new entry. */
  entry: StoryBibleEntry | null;
  isSaving: boolean;
  fieldErrors: Partial<Record<BibleEntryField, string>>;
  formError: string | null;
  upsertResult: StoryBibleUpsertResult | null;
  isUploadingAsset: boolean;
  uploadError: string | null;
  uploadedAsset: UploadedAsset | null;
  onUploadAsset: (file: File) => void;
  onSave: (request: BibleEntryCreateRequest) => void;
  onClose: () => void;
}

export function StoryBibleEditor({
  entry,
  isSaving,
  fieldErrors,
  formError,
  upsertResult,
  isUploadingAsset,
  uploadError,
  uploadedAsset,
  onUploadAsset,
  onSave,
  onClose,
}: StoryBibleEditorProps) {
  const headingId = useId();
  const idPrefix = useId();
  const initialEntryRef = useRef(entry);
  const schema = useMemo(() => buildBibleEntryFormSchema(entry), [entry]);
  const defaults = useMemo(() => bibleEntryFormDefaults(initialEntryRef.current), []);

  const form = useForm<BibleEntryFormValues>({
    resolver: zodResolver(schema),
    defaultValues: defaults,
    mode: "onSubmit",
  });
  const {
    register,
    handleSubmit,
    setError,
    clearErrors,
    setValue,
    setFocus,
    getValues,
    watch,
    reset,
    formState: { errors },
  } = form;

  const [manualAssetId, setManualAssetId] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [clientUploadError, setClientUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const lastAttachedUploadId = useRef<string | null>(null);
  const lastHandledUpsertResult = useRef<StoryBibleUpsertResult | null>(null);

  useEffect(() => {
    setFocus(initialEntryRef.current ? "display_name" : "code_name");
  }, [setFocus]);

  useEffect(() => {
    clearErrors();
    for (const [field, message] of Object.entries(fieldErrors) as Array<
      [BibleEntryField, string]
    >) {
      setError(field, { type: "server", message });
    }
  }, [clearErrors, fieldErrors, setError]);

  // Reset the new-entry form after a successful creation so creating another entry starts clean.
  useEffect(() => {
    if (entry !== null || !upsertResult || lastHandledUpsertResult.current === upsertResult) return;
    lastHandledUpsertResult.current = upsertResult;
    reset(bibleEntryFormDefaults(null));
    setManualAssetId("");
    setSelectedFile(null);
    setClientUploadError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [entry, reset, upsertResult]);

  // Automatically attach a newly uploaded asset to the entry's reference_asset_ids.
  useEffect(() => {
    if (!uploadedAsset || lastAttachedUploadId.current === uploadedAsset.id) return;
    lastAttachedUploadId.current = uploadedAsset.id;
    const current = getValues("reference_asset_ids");
    if (
      !current.includes(uploadedAsset.id) &&
      current.length < BIBLE_ENTRY_LIMITS.maxReferenceAssets
    ) {
      setValue("reference_asset_ids", [...current, uploadedAsset.id], { shouldDirty: true });
      clearErrors("reference_asset_ids");
    }
    setSelectedFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [clearErrors, getValues, setValue, uploadedAsset]);

  const entryType = watch("entry_type");
  const referenceAssetIds = watch("reference_asset_ids");
  const busy = isSaving || isUploadingAsset;

  const fieldId = (name: string) => `${idPrefix}-${name}`;
  const errorId = (name: string) => `${idPrefix}-${name}-error`;
  const fieldProps = (field: BibleEntryField) => ({
    id: fieldId(field),
    "aria-invalid": errors[field] ? true : undefined,
    "aria-describedby": errors[field] ? errorId(field) : undefined,
    disabled: busy,
  });
  const renderFieldError = (field: BibleEntryField) =>
    errors[field]?.message ? (
      <p id={errorId(field)} className="text-xs text-destructive">
        {errors[field]?.message}
      </p>
    ) : null;

  const addAssetId = () => {
    const trimmed = manualAssetId.trim();
    if (!trimmed) return;
    if (trimmed.length > BIBLE_ENTRY_LIMITS.referenceAssetIdMaxChars) {
      setError("reference_asset_ids", {
        type: "manual",
        message: `Asset ID must be at most ${BIBLE_ENTRY_LIMITS.referenceAssetIdMaxChars} characters.`,
      });
      return;
    }
    if (referenceAssetIds.includes(trimmed)) {
      setManualAssetId("");
      return;
    }
    if (referenceAssetIds.length >= BIBLE_ENTRY_LIMITS.maxReferenceAssets) {
      setError("reference_asset_ids", {
        type: "manual",
        message: `At most ${BIBLE_ENTRY_LIMITS.maxReferenceAssets} reference assets may be attached.`,
      });
      return;
    }
    clearErrors("reference_asset_ids");
    setValue("reference_asset_ids", [...referenceAssetIds, trimmed], { shouldDirty: true });
    setManualAssetId("");
  };

  const removeAssetId = (assetId: string) => {
    clearErrors("reference_asset_ids");
    setValue(
      "reference_asset_ids",
      referenceAssetIds.filter((id) => id !== assetId),
      { shouldDirty: true },
    );
  };

  const handleFileChange = (file: File | null) => {
    if (!file) {
      setSelectedFile(null);
      setClientUploadError(null);
      return;
    }
    const allowedMimeTypes: readonly string[] = BIBLE_ENTRY_LIMITS.allowedReferenceMimeTypes;
    if (!allowedMimeTypes.includes(file.type)) {
      setSelectedFile(null);
      setClientUploadError("Only PNG, JPEG and WebP reference images are supported.");
      return;
    }
    setClientUploadError(null);
    setSelectedFile(file);
  };

  const handleUploadClick = () => {
    if (!selectedFile || isUploadingAsset) return;
    const allowedMimeTypes: readonly string[] = BIBLE_ENTRY_LIMITS.allowedReferenceMimeTypes;
    if (!allowedMimeTypes.includes(selectedFile.type)) {
      setClientUploadError("Only PNG, JPEG and WebP reference images are supported.");
      return;
    }
    setClientUploadError(null);
    onUploadAsset(selectedFile);
  };

  const submit = handleSubmit((values) => {
    onSave(buildBibleEntryCreateRequest(values, entry));
  });

  return (
    <section
      aria-labelledby={headingId}
      data-testid={entry ? `story-bible-editor-${entry.code_name}` : "story-bible-editor-new"}
      className="mt-4 rounded-lg border border-primary/40 bg-background/60 p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 id={headingId} className="font-semibold">
            {entry ? `Edit ${entry.display_name} (${entry.code_name})` : "New Story Bible entry"}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            Saving upserts the entry by code name and recompiles every shot prompt in the project.
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={onClose}
          aria-label={
            entry ? `Close editor for ${entry.display_name}` : "Close new Story Bible entry editor"
          }
        >
          <X aria-hidden="true" />
          Close
        </Button>
      </div>

      {formError && (
        <p
          role="alert"
          className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {formError}
        </p>
      )}

      {upsertResult && (
        <p
          role="status"
          data-testid="story-bible-upsert-result"
          className="mt-3 flex items-center gap-2 rounded-md border border-success/30 bg-success/10 p-3 text-sm text-success"
        >
          <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
          Saved {upsertResult.code_name} ({upsertResult.id}). Downstream project shots were
          recompiled.
        </p>
      )}

      <form className="mt-4 grid gap-4 md:grid-cols-2" onSubmit={submit} noValidate>
        <div className="space-y-1">
          <label htmlFor={fieldId("entry_type")} className="text-sm font-medium">
            Entry type
          </label>
          <select
            className={inputClass}
            {...fieldProps("entry_type")}
            disabled={busy || Boolean(entry)}
            {...register("entry_type")}
          >
            {STORY_BIBLE_ENTRY_TYPES.map((type) => (
              <option key={type} value={type}>
                {STORY_BIBLE_ENTRY_LABELS[type]}
              </option>
            ))}
          </select>
          {renderFieldError("entry_type")}
        </div>

        <div className="space-y-1">
          <label htmlFor={fieldId("code_name")} className="text-sm font-medium">
            Code name
          </label>
          <input
            type="text"
            placeholder="e.g. char_detective_linh"
            maxLength={BIBLE_ENTRY_LIMITS.codeNameMaxChars}
            className={cn(inputClass, "font-mono")}
            {...fieldProps("code_name")}
            disabled={busy || Boolean(entry)}
            {...register("code_name")}
          />
          {renderFieldError("code_name")}
        </div>

        <div className="space-y-1 md:col-span-2">
          <label htmlFor={fieldId("display_name")} className="text-sm font-medium">
            Display name
          </label>
          <input
            type="text"
            placeholder="e.g. Detective Linh"
            maxLength={BIBLE_ENTRY_LIMITS.displayNameMaxChars}
            className={inputClass}
            {...fieldProps("display_name")}
            {...register("display_name")}
          />
          {renderFieldError("display_name")}
        </div>

        <div className="space-y-1 md:col-span-2">
          <label htmlFor={fieldId("immutable_traits")} className="text-sm font-medium">
            Immutable traits
          </label>
          <textarea
            rows={3}
            placeholder="Fixed visual traits locked into every compiled shot prompt..."
            maxLength={BIBLE_ENTRY_LIMITS.immutableTraitsMaxChars}
            className={inputClass}
            {...fieldProps("immutable_traits")}
            {...register("immutable_traits")}
          />
          {renderFieldError("immutable_traits")}
        </div>

        {entryType === "character" && (
          <div className="space-y-1 md:col-span-2">
            <label htmlFor={fieldId("current_outfit")} className="text-sm font-medium">
              Current outfit
            </label>
            <input
              type="text"
              placeholder="e.g. charcoal trench coat"
              maxLength={BIBLE_ENTRY_LIMITS.mutableStringAttrMaxChars}
              className={inputClass}
              {...fieldProps("current_outfit")}
              {...register("current_outfit")}
            />
            {renderFieldError("current_outfit")}
          </div>
        )}

        {entryType === "location" && (
          <>
            <div className="space-y-1">
              <label htmlFor={fieldId("time_of_day")} className="text-sm font-medium">
                Time of day
              </label>
              <input
                type="text"
                placeholder="e.g. rainy night"
                maxLength={BIBLE_ENTRY_LIMITS.mutableStringAttrMaxChars}
                className={inputClass}
                {...fieldProps("time_of_day")}
                {...register("time_of_day")}
              />
              {renderFieldError("time_of_day")}
            </div>
            <div className="space-y-1">
              <label htmlFor={fieldId("lighting_rule")} className="text-sm font-medium">
                Lighting rule
              </label>
              <input
                type="text"
                placeholder="e.g. neon rim lighting"
                maxLength={BIBLE_ENTRY_LIMITS.mutableStringAttrMaxChars}
                className={inputClass}
                {...fieldProps("lighting_rule")}
                {...register("lighting_rule")}
              />
              {renderFieldError("lighting_rule")}
            </div>
          </>
        )}

        {entryType === "style" && (
          <>
            <div className="space-y-1 md:col-span-2">
              <label htmlFor={fieldId("color_palette")} className="text-sm font-medium">
                Color palette
              </label>
              <input
                type="text"
                placeholder="e.g. teal and amber noir contrast"
                maxLength={BIBLE_ENTRY_LIMITS.styleColorPaletteMaxChars}
                className={inputClass}
                {...fieldProps("color_palette")}
                {...register("color_palette")}
              />
              {renderFieldError("color_palette")}
            </div>
            <div className="space-y-1">
              <label htmlFor={fieldId("negative_constraints_text")} className="text-sm font-medium">
                Negative constraints (comma-separated)
              </label>
              <input
                type="text"
                placeholder="e.g. 3d cgi, deformed hands"
                className={inputClass}
                {...fieldProps("negative_constraints_text")}
                {...register("negative_constraints_text")}
              />
              {renderFieldError("negative_constraints_text")}
            </div>
            <div className="space-y-1">
              <label htmlFor={fieldId("forbidden_terms_text")} className="text-sm font-medium">
                Forbidden terms (comma-separated)
              </label>
              <input
                type="text"
                placeholder="e.g. photorealistic, live action"
                className={inputClass}
                {...fieldProps("forbidden_terms_text")}
                {...register("forbidden_terms_text")}
              />
              {renderFieldError("forbidden_terms_text")}
            </div>
          </>
        )}

        {entryType === "character" && (
          <div className="md:col-span-2">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                disabled={busy}
                className="size-4 rounded border-input"
                {...register("requires_reference_lock")}
              />
              Require reference lock for downstream shots
            </label>
            <p className="mt-1 text-xs text-muted-foreground">
              When enabled on a character, any shot featuring this character is blocked from
              generation until at least one reference asset is attached.
            </p>
          </div>
        )}

        <div className="space-y-3 rounded-md border border-border bg-muted/20 p-3 md:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm font-medium">
                Reference assets ({referenceAssetIds.length}/{BIBLE_ENTRY_LIMITS.maxReferenceAssets}
                )
              </p>
              <p className="text-xs text-muted-foreground">
                Upload a PNG, JPEG or WebP reference image, or attach an existing project asset ID.
              </p>
            </div>
          </div>

          {referenceAssetIds.length > 0 && (
            <ul className="flex flex-wrap gap-3" data-testid="editor-reference-assets">
              {referenceAssetIds.map((assetId) => (
                <li
                  key={assetId}
                  className="flex flex-col items-start gap-1.5 rounded-md border border-border bg-background p-2"
                >
                  <ReferenceAssetPreview assetId={assetId} label={`Reference asset ${assetId}`} />
                  <div className="flex w-28 items-center justify-between gap-1">
                    <span className="truncate font-mono text-[11px] text-muted-foreground">
                      {assetId}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => removeAssetId(assetId)}
                      aria-label={`Remove reference asset ${assetId}`}
                    >
                      <Trash2 className="size-3 text-destructive" aria-hidden="true" />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label htmlFor={fieldId("upload-file")} className="text-xs font-medium">
                Upload reference image
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  ref={fileInputRef}
                  id={fieldId("upload-file")}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  disabled={busy}
                  aria-invalid={errors.reference_asset_ids ? true : undefined}
                  aria-describedby={
                    errors.reference_asset_ids ? errorId("reference_asset_ids") : undefined
                  }
                  onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
                  className="block w-full text-xs text-muted-foreground file:mr-2 file:rounded-md file:border file:border-border file:bg-background file:px-2.5 file:py-1 file:text-xs file:font-medium"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || !selectedFile}
                  onClick={handleUploadClick}
                >
                  {isUploadingAsset ? (
                    <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                  ) : (
                    <Upload className="size-3.5" aria-hidden="true" />
                  )}
                  Upload &amp; attach
                </Button>
              </div>
              {(clientUploadError ?? uploadError) && (
                <p role="alert" className="text-xs text-destructive">
                  {clientUploadError ?? uploadError}
                </p>
              )}
              {uploadedAsset && (
                <p role="status" className="text-xs text-success">
                  Attached uploaded asset <code className="font-mono">{uploadedAsset.id}</code> (
                  {uploadedAsset.filename}).
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <label htmlFor={fieldId("manual-asset-id")} className="text-xs font-medium">
                Attach existing asset ID
              </label>
              <div className="flex items-center gap-2">
                <input
                  id={fieldId("manual-asset-id")}
                  type="text"
                  value={manualAssetId}
                  onChange={(event) => setManualAssetId(event.target.value)}
                  placeholder="ast_..."
                  maxLength={BIBLE_ENTRY_LIMITS.referenceAssetIdMaxChars}
                  disabled={busy}
                  aria-invalid={errors.reference_asset_ids ? true : undefined}
                  aria-describedby={
                    errors.reference_asset_ids ? errorId("reference_asset_ids") : undefined
                  }
                  className={cn(inputClass, "font-mono")}
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || !manualAssetId.trim()}
                  onClick={addAssetId}
                >
                  <Plus className="size-3.5" aria-hidden="true" />
                  Add ID
                </Button>
              </div>
            </div>
          </div>

          {renderFieldError("reference_asset_ids")}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 md:col-span-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            {isSaving ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="size-4" aria-hidden="true" />
            )}
            {entry ? "Save entry" : "Create entry"}
          </Button>
        </div>
      </form>
    </section>
  );
}

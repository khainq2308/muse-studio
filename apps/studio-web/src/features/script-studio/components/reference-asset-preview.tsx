import { FileQuestion, ImageOff, LoaderCircle, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useReferenceAssetMedia } from "@/features/script-studio/api";
import { REFERENCE_PREVIEW_MAX_BYTES } from "@/features/script-studio/state";
import { ApiError } from "@/lib/api/client";
import { useInView } from "@/lib/media/use-in-view";
import { useObjectUrl } from "@/lib/media/use-object-url";
import { cn } from "@/lib/utils";

interface ReferenceAssetPreviewProps {
  assetId: string;
  label: string;
  variant?: "thumb" | "cover";
  /**
   * `compact` is for very small thumbnails (e.g. 64px): status text is moved to an
   * accessible label/tooltip and the retry action becomes an icon-only button so
   * nothing is clipped or unclickable.
   */
  size?: "default" | "compact";
  className?: string;
}

/**
 * Lazy-loaded authenticated thumbnail for a Story Bible reference asset.
 * Fetches bytes via `apiGetBlob` only when near the viewport and revokes the object URL on unmount.
 */
export function ReferenceAssetPreview({
  assetId,
  label,
  variant = "thumb",
  size = "default",
  className,
}: ReferenceAssetPreviewProps) {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const inView = useInView(element);
  return (
    <div
      ref={setElement}
      data-asset-id={assetId}
      className={cn(variant === "cover" ? "h-full w-full" : "w-28 shrink-0", className)}
    >
      <ReferenceAssetPreviewBody
        assetId={assetId}
        label={label}
        variant={variant}
        size={size}
        enabled={inView}
      />
    </div>
  );
}

function ReferenceAssetPreviewBody({
  assetId,
  label,
  variant = "thumb",
  size = "default",
  enabled,
}: ReferenceAssetPreviewProps & { enabled: boolean }) {
  const compact = size === "compact" && variant !== "cover";
  const query = useReferenceAssetMedia(assetId, REFERENCE_PREVIEW_MAX_BYTES, {
    enabled,
    gcTime: 30_000,
  });
  const url = useObjectUrl(query.data);
  const boxClass = cn(
    "flex w-full items-center justify-center overflow-hidden bg-muted/40 text-xs text-muted-foreground",
    variant === "cover" ? "h-full" : "aspect-square rounded-md border border-border",
  );

  if (query.isError) {
    const tooLarge = query.error instanceof ApiError && query.error.category === "too_large";
    const missing = query.error instanceof ApiError && query.error.status === 404;
    const final = tooLarge || missing;
    const message = tooLarge ? "Too large" : missing ? "Asset missing" : "Preview unavailable";
    if (compact) {
      return (
        <div
          className={cn(boxClass, "flex-col gap-1 p-1 text-center")}
          role="alert"
          title={`${message}: ${label}`}
          data-testid={`reference-asset-error-${assetId}`}
        >
          <ImageOff className="size-3.5 shrink-0" aria-hidden="true" />
          <span className="sr-only">{message}</span>
          {!final && (
            <Button
              type="button"
              size="icon"
              variant="outline"
              onClick={() => void query.refetch()}
              aria-label={`Retry loading ${label}`}
              title="Retry"
              className="size-7 [&_svg]:size-3"
            >
              <RefreshCw aria-hidden="true" />
            </Button>
          )}
        </div>
      );
    }
    return (
      <div className={cn(boxClass, "flex-col gap-1.5 p-2 text-center")} role="alert">
        <ImageOff className="size-4" aria-hidden="true" />
        <span>{message}</span>
        {!final && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void query.refetch()}
            aria-label={`Retry loading ${label}`}
          >
            <RefreshCw className="size-3" aria-hidden="true" />
            Retry
          </Button>
        )}
      </div>
    );
  }

  if (!query.data || !url) {
    return (
      <div className={boxClass} role="status" aria-label={`Loading ${label}`}>
        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
      </div>
    );
  }

  if (query.data.type.startsWith("image/")) {
    return (
      <img
        src={url}
        alt={label}
        data-testid={`reference-asset-image-${assetId}`}
        className={cn(
          "w-full bg-black object-cover",
          variant === "cover" ? "h-full" : "aspect-square rounded-md border border-border",
        )}
      />
    );
  }

  const nonImageText = `Non-image (${query.data.type || "unknown"})`;
  if (compact) {
    return (
      <div className={boxClass} role="note" title={nonImageText}>
        <FileQuestion className="size-3.5" aria-hidden="true" />
        <span className="sr-only">{nonImageText}</span>
      </div>
    );
  }

  return (
    <div className={boxClass} role="note">
      <span className="p-2 text-center">{nonImageText}</span>
    </div>
  );
}

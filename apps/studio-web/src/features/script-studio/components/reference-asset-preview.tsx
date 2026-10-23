import { ImageOff, LoaderCircle, RefreshCw } from "lucide-react";
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
}

/**
 * Lazy-loaded authenticated thumbnail for a Story Bible reference asset.
 * Fetches bytes via `apiGetBlob` only when near the viewport and revokes the object URL on unmount.
 */
export function ReferenceAssetPreview({ assetId, label }: ReferenceAssetPreviewProps) {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const inView = useInView(element);
  return (
    <div ref={setElement} data-asset-id={assetId} className="w-28 shrink-0">
      <ReferenceAssetPreviewBody assetId={assetId} label={label} enabled={inView} />
    </div>
  );
}

function ReferenceAssetPreviewBody({
  assetId,
  label,
  enabled,
}: ReferenceAssetPreviewProps & { enabled: boolean }) {
  const query = useReferenceAssetMedia(assetId, REFERENCE_PREVIEW_MAX_BYTES, {
    enabled,
    gcTime: 30_000,
  });
  const url = useObjectUrl(query.data);
  const boxClass =
    "flex aspect-square w-full items-center justify-center overflow-hidden rounded-md border border-border bg-muted/40 text-xs text-muted-foreground";

  if (query.isError) {
    const tooLarge = query.error instanceof ApiError && query.error.category === "too_large";
    const missing = query.error instanceof ApiError && query.error.status === 404;
    const final = tooLarge || missing;
    return (
      <div className={cn(boxClass, "flex-col gap-1.5 p-2 text-center")} role="alert">
        <ImageOff className="size-4" aria-hidden="true" />
        <span>{tooLarge ? "Too large" : missing ? "Asset missing" : "Preview unavailable"}</span>
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
        className="aspect-square w-full rounded-md border border-border bg-black object-cover"
      />
    );
  }

  return (
    <div className={boxClass} role="note">
      <span className="p-2 text-center">Non-image ({query.data.type || "unknown"})</span>
    </div>
  );
}

import { ImageOff, LoaderCircle, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useAssetMedia } from "@/features/shot-board/api";
import { takeActionErrorMessage } from "@/features/shot-board/takes";
import { ApiError } from "@/lib/api/client";
import { useInView } from "@/lib/media/use-in-view";
import { useObjectUrl } from "@/lib/media/use-object-url";
import { cn } from "@/lib/utils";

interface TakeMediaProps {
  /** Asset to preview; null renders the "no asset" placeholder without a request. */
  assetId: string | null;
  /** Accessible name of the media, e.g. "Take 2 media" or "Take 2 first frame". */
  label: string;
  maxBytes: number;
  /** `frame`: compact thumbnail; `preview`: full-width player. */
  variant: "frame" | "preview";
  /** Placeholder text when there is no asset ID. */
  emptyText?: string;
}

/**
 * Authenticated media preview. The bytes are fetched through the shared API client (the
 * admin token is a header, so `<img src>`/`<video src>` cannot point at the API directly) and
 * shown through an object URL that is revoked on unmount. Nothing is downloaded until the
 * element is near the viewport. The served media type decides between image and video.
 */
export function TakeMedia(props: TakeMediaProps) {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const inView = useInView(element);
  return (
    <div
      ref={setElement}
      data-asset-id={props.assetId ?? undefined}
      className={props.variant === "frame" ? "w-32" : "w-full"}
    >
      <TakeMediaBody {...props} enabled={inView} />
    </div>
  );
}

function TakeMediaBody({
  assetId,
  label,
  maxBytes,
  variant,
  emptyText = "No asset",
  enabled,
}: TakeMediaProps & { enabled: boolean }) {
  const frame = variant === "frame";
  // A closed video preview releases its Blob at once; small frames stay cached briefly.
  const query = useAssetMedia(assetId, maxBytes, { enabled, gcTime: frame ? 30_000 : 0 });
  const url = useObjectUrl(query.data);
  const boxClass =
    "flex aspect-video w-full items-center justify-center overflow-hidden rounded-md border border-border bg-muted/40 text-xs text-muted-foreground";

  if (!assetId) {
    return (
      <div
        className={boxClass}
        data-testid="take-media-empty"
        role="img"
        aria-label={`${label}: ${emptyText}`}
      >
        <span className="flex flex-col items-center gap-1 p-2 text-center" aria-hidden="true">
          <ImageOff className="size-4" />
          {emptyText}
        </span>
      </div>
    );
  }

  if (query.isError) {
    const tooLarge = query.error instanceof ApiError && query.error.category === "too_large";
    // Over-limit and missing assets are final: retrying cannot change the outcome.
    const final = tooLarge || (query.error instanceof ApiError && query.error.status === 404);
    return (
      <div className={cn(boxClass, "flex-col gap-2 p-2 text-center")} role="alert">
        <span>
          {frame
            ? tooLarge
              ? "Frame too large to preview"
              : query.error instanceof ApiError && query.error.status === 404
                ? "Frame asset missing"
                : "Frame unavailable"
            : takeActionErrorMessage(query.error, "preview")}
        </span>
        {!final && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void query.refetch()}
            aria-label={`Retry loading ${label}`}
          >
            <RefreshCw aria-hidden="true" />
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
        {!frame && <span className="ml-2">Loading preview...</span>}
      </div>
    );
  }

  const type = query.data.type;
  if (type.startsWith("image/")) {
    return (
      <img
        src={url}
        alt={label}
        data-testid="take-media-image"
        className="aspect-video w-full rounded-md border border-border bg-black object-contain"
      />
    );
  }
  if (type.startsWith("video/") && !frame) {
    return (
      <video
        src={url}
        controls
        preload="metadata"
        aria-label={label}
        data-testid="take-media-video"
        className="aspect-video w-full rounded-md border border-border bg-black"
      />
    );
  }
  return (
    <div className={boxClass} role="note">
      <span className="p-2 text-center">
        Preview not available for this media type ({type || "unknown"}).
      </span>
    </div>
  );
}

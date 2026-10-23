import { useEffect, useState } from "react";

/**
 * Expose a Blob as a temporary `blob:` URL for `<img>`/`<video>` and revoke it when the Blob
 * changes or the component unmounts. The URL is created in an effect (not during render) so
 * StrictMode's mount/unmount/mount cycle never leaves an element pointing at a revoked URL.
 */
export function useObjectUrl(blob: Blob | null | undefined): string | null {
  const [entry, setEntry] = useState<{ blob: Blob; url: string } | null>(null);
  useEffect(() => {
    if (!blob) {
      setEntry(null);
      return;
    }
    const url = URL.createObjectURL(blob);
    setEntry({ blob, url });
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [blob]);
  // Never hand out a URL created for a previous Blob (it is revoked by the time we render).
  return entry && entry.blob === blob ? entry.url : null;
}

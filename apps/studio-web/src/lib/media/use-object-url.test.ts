import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useObjectUrl } from "@/lib/media/use-object-url";

function stubObjectUrls() {
  let next = 0;
  const create = vi.fn<(object: Blob | MediaSource) => string>(() => `blob:test-${++next}`);
  const revoke = vi.fn<(url: string) => void>();
  const NativeURL = globalThis.URL;
  vi.stubGlobal(
    "URL",
    class extends NativeURL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
  return { create, revoke };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useObjectUrl", () => {
  it("returns null without a Blob and creates no URL", () => {
    const { create } = stubObjectUrls();
    const { result } = renderHook(() => useObjectUrl(null));
    expect(result.current).toBeNull();
    expect(create).not.toHaveBeenCalled();
  });

  it("creates one URL per Blob, revokes the old one on change and the last on unmount", () => {
    const { create, revoke } = stubObjectUrls();
    const first = new Blob(["a"], { type: "image/png" });
    const second = new Blob(["b"], { type: "image/png" });
    const { result, rerender, unmount } = renderHook(({ blob }) => useObjectUrl(blob), {
      initialProps: { blob: first as Blob | null },
    });

    expect(result.current).toBe("blob:test-1");
    expect(create).toHaveBeenLastCalledWith(first);

    rerender({ blob: second });
    expect(revoke).toHaveBeenCalledWith("blob:test-1");
    expect(result.current).toBe("blob:test-2");

    rerender({ blob: null });
    expect(revoke).toHaveBeenCalledWith("blob:test-2");
    expect(result.current).toBeNull();

    rerender({ blob: first });
    expect(result.current).toBe("blob:test-3");
    unmount();
    expect(revoke).toHaveBeenCalledWith("blob:test-3");
    expect(create).toHaveBeenCalledTimes(3);
    expect(revoke).toHaveBeenCalledTimes(3);
  });
});

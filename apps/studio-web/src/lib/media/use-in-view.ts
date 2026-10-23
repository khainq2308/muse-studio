import { useEffect, useState } from "react";

/**
 * Whether an element has come near the viewport (it stays true once seen). Used to defer
 * media downloads until an item is about to be shown. Environments without
 * IntersectionObserver report the element as visible immediately.
 */
export function useInView<T extends Element>(element: T | null, rootMargin = "200px"): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen || !element) return;
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [element, rootMargin, seen]);
  return seen;
}

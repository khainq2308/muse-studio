import { describe, expect, it } from "vitest";
import { resources } from "./index";

function getNormalizedKeyPaths(obj: Record<string, unknown>, prefix = ""): Set<string> {
  const keys = new Set<string>();

  for (const [key, value] of Object.entries(obj)) {
    // Strip plural suffix (_one, _other, etc.) to get base semantic key
    const baseKey = key.replace(/_(one|other|zero|two|few|many)$/, "");
    const fullPath = prefix ? `${prefix}.${baseKey}` : baseKey;

    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      const nested = getNormalizedKeyPaths(value as Record<string, unknown>, fullPath);
      for (const k of nested) keys.add(k);
    } else {
      keys.add(fullPath);
    }
  }

  return keys;
}

describe("i18n key parity between vi and en", () => {
  const viNamespaces = resources.vi;
  const enNamespaces = resources.en;

  const allNamespaces = Object.keys(viNamespaces) as (keyof typeof viNamespaces)[];

  it("has identical namespaces in vi and en", () => {
    expect(Object.keys(viNamespaces).sort()).toEqual(Object.keys(enNamespaces).sort());
  });

  allNamespaces.forEach((ns) => {
    it(`has 100% normalized key parity in namespace: ${ns}`, () => {
      const viKeys = getNormalizedKeyPaths(viNamespaces[ns] as Record<string, unknown>);
      const enKeys = getNormalizedKeyPaths(enNamespaces[ns] as Record<string, unknown>);

      const missingInEn = [...viKeys].filter((k) => !enKeys.has(k));
      const missingInVi = [...enKeys].filter((k) => !viKeys.has(k));

      expect(missingInEn, `Keys missing in en/${ns}.json`).toEqual([]);
      expect(missingInVi, `Keys missing in vi/${ns}.json`).toEqual([]);
    });
  });
});

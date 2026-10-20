import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const srcRoot = path.join(appRoot, "src");
const failures = [];

function collectFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return collectFiles(fullPath);
    return /\.(?:ts|tsx)$/.test(entry.name) ? [fullPath] : [];
  });
}

function resolveImport(sourceFile, specifier) {
  if (specifier.startsWith("@/")) return path.resolve(srcRoot, specifier.slice(2));
  if (specifier.startsWith(".")) return path.resolve(path.dirname(sourceFile), specifier);
  return null;
}

for (const file of collectFiles(srcRoot)) {
  const relative = path.relative(srcRoot, file).split(path.sep).join("/");
  const source = readFileSync(file, "utf8");

  if (
    file.endsWith(".tsx") &&
    /<pre\b[^>]*>\s*\{\s*JSON\.stringify\s*\(/s.test(source)
  ) {
    failures.push(`${relative}: render domain data instead of dumping JSON into <pre>.`);
  }

  if (relative !== "lib/api/client.ts" && /\b(?:window\.)?fetch\s*\(/.test(source)) {
    failures.push(`${relative}: use the shared authenticated API client instead of raw fetch.`);
  }

  if (
    !relative.endsWith(".test.ts") &&
    !relative.endsWith(".test.tsx") &&
    /\b(?:window\.)?(?:localStorage|sessionStorage)\b/.test(source)
  ) {
    failures.push(
      `${relative}: browser storage is forbidden for admin credentials; keep tokens memory-only.`,
    );
  }

  const sourceMatch = relative.match(/^(entities|features)\/([^/]+)\//);
  if (!sourceMatch) continue;
  const [, sourceRootName, sourceFeature] = sourceMatch;
  const importPattern = /\b(?:import|export)\s+(?:type\s+)?(?:[^"']*?\s+from\s+)?["']([^"']+)["']/g;

  for (const match of source.matchAll(importPattern)) {
    const targetPath = resolveImport(file, match[1]);
    if (!targetPath) continue;
    const targetRelative = path.relative(srcRoot, targetPath).split(path.sep).join("/");
    const targetMatch = targetRelative.match(/^(entities|features)\/([^/]+)(?:\/|$)/);
    if (
      targetMatch &&
      targetMatch[1] === sourceRootName &&
      targetMatch[2] !== sourceFeature
    ) {
      failures.push(
        `${relative}: cross-feature import to "${match[1]}" is forbidden; use lib/ for shared contracts.`,
      );
    }
  }
}

if (failures.length > 0) {
  console.error("Frontend boundary check failed:");
  for (const failure of failures) console.error(` - ${failure}`);
  process.exitCode = 1;
} else {
  console.log("Frontend boundary check passed.");
}

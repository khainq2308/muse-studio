import ts from "typescript";
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

function resolveLocalImport(sourceFile, specifier) {
  if (specifier.startsWith("@/")) return path.resolve(srcRoot, specifier.slice(2));
  if (specifier.startsWith(".")) return path.resolve(path.dirname(sourceFile), specifier);
  return null;
}

function collectImportSpecifiers(file, source) {
  const scriptKind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, scriptKind);
  const specifiers = [];

  function addStringLiteral(node) {
    if (node && ts.isStringLiteralLike(node)) specifiers.push(node.text);
  }

  function visit(node) {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      addStringLiteral(node.moduleSpecifier);
    } else if (
      ts.isCallExpression(node) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) && node.expression.text === "require"))
    ) {
      addStringLiteral(node.arguments[0]);
    } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) {
      addStringLiteral(node.argument.literal);
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return specifiers;
}

function isOutsideSrc(relativePath) {
  return relativePath === ".." || relativePath.startsWith("../") || path.isAbsolute(relativePath);
}

for (const file of collectFiles(srcRoot)) {
  const relative = path.relative(srcRoot, file).split(path.sep).join("/");
  const source = readFileSync(file, "utf8");

  if (file.endsWith(".tsx") && /<pre\b[^>]*>\s*\{\s*JSON\.stringify\s*\(/s.test(source)) {
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

  const sourceEntity = relative.match(/^entities\/([^/]+)\//);
  const sourceFeature = relative.match(/^features\/([^/]+)\//);
  const isUiPrimitive = relative.startsWith("components/ui/");

  for (const specifier of collectImportSpecifiers(file, source)) {
    const targetPath = resolveLocalImport(file, specifier);
    if (!targetPath) continue;

    const targetRelative = path.relative(srcRoot, targetPath).split(path.sep).join("/");
    if (isOutsideSrc(targetRelative)) {
      failures.push(
        `${relative}: import "${specifier}" resolves outside src/; keep imports inside apps/studio-web.`,
      );
      continue;
    }

    const targetEntity = targetRelative.match(/^entities\/([^/]+)(?:\/|$)/);
    const targetFeature = targetRelative.match(/^features\/([^/]+)(?:\/|$)/);

    if (sourceEntity && targetFeature) {
      failures.push(`${relative}: entities/ must not import features/ ("${specifier}").`);
    }

    if (sourceEntity && targetEntity && targetEntity[1] !== sourceEntity[1]) {
      failures.push(
        `${relative}: cross-entity import "${specifier}" is forbidden; move shared contracts into lib/.`,
      );
    }

    if (sourceFeature && targetFeature && targetFeature[1] !== sourceFeature[1]) {
      failures.push(
        `${relative}: features must not import another feature ("${specifier}"); compose in app/.`,
      );
    }

    if (
      isUiPrimitive &&
      (targetFeature ||
        targetEntity ||
        targetRelative === "lib/api" ||
        targetRelative.startsWith("lib/api/"))
    ) {
      failures.push(
        `${relative}: components/ui must not import features, entities or lib/api ("${specifier}").`,
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

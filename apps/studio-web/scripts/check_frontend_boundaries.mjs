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
  if (specifier === "src" || specifier.startsWith("src/")) {
    return path.resolve(appRoot, specifier);
  }
  if (specifier.startsWith(".")) return path.resolve(path.dirname(sourceFile), specifier);
  return null;
}

function collectImportSpecifiers(sourceFile) {
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

function rootIdentifier(node) {
  let current = node;
  while (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {
    current = current.expression;
  }
  return ts.isIdentifier(current) ? current.text : null;
}

function isGlobalBrowserProperty(node, names) {
  if (ts.isPropertyAccessExpression(node)) {
    return (
      names.includes(node.name.text) &&
      ["window", "globalThis", "self"].includes(rootIdentifier(node.expression))
    );
  }
  if (
    ts.isElementAccessExpression(node) &&
    node.argumentExpression &&
    ts.isStringLiteralLike(node.argumentExpression)
  ) {
    return (
      names.includes(node.argumentExpression.text) &&
      ["window", "globalThis", "self"].includes(rootIdentifier(node.expression))
    );
  }
  return false;
}

function isIdentifierDeclarationName(node) {
  const parent = node.parent;
  return (
    (ts.isVariableDeclaration(parent) && parent.name === node) ||
    (ts.isBindingElement(parent) && parent.name === node) ||
    (ts.isFunctionDeclaration(parent) && parent.name === node) ||
    (ts.isClassDeclaration(parent) && parent.name === node) ||
    (ts.isParameter(parent) && parent.name === node) ||
    ((ts.isMethodDeclaration(parent) ||
      ts.isPropertyDeclaration(parent) ||
      ts.isPropertySignature(parent) ||
      ts.isMethodSignature(parent)) &&
      parent.name === node) ||
    ((ts.isImportSpecifier(parent) || ts.isImportClause(parent) || ts.isNamespaceImport(parent)) &&
      parent.name === node) ||
    ((ts.isPropertyAssignment(parent) || ts.isMethodDeclaration(parent)) && parent.name === node) ||
    (ts.isPropertyAccessExpression(parent) && parent.name === node)
  );
}

function containsJsonStringifyCall(node) {
  let found = false;
  function visit(child) {
    if (
      ts.isCallExpression(child) &&
      ts.isPropertyAccessExpression(child.expression) &&
      ts.isIdentifier(child.expression.expression) &&
      child.expression.expression.text === "JSON" &&
      child.expression.name.text === "stringify"
    ) {
      found = true;
      return;
    }
    if (!found) ts.forEachChild(child, visit);
  }
  visit(node);
  return found;
}

function containsRawFetchAccess(sourceFile) {
  let found = false;
  function visit(node) {
    if (
      (ts.isCallExpression(node) &&
        ts.isIdentifier(node.expression) &&
        node.expression.text === "fetch") ||
      isGlobalBrowserProperty(node, ["fetch"])
    ) {
      found = true;
      return;
    }

    // Also block aliases such as `const send = fetch`, not just direct calls.
    if (
      ts.isVariableDeclaration(node) &&
      node.initializer &&
      ts.isIdentifier(node.initializer) &&
      node.initializer.text === "fetch"
    ) {
      found = true;
      return;
    }

    // Destructuring a browser global is another way to alias the restricted APIs.
    if (
      ts.isVariableDeclaration(node) &&
      node.initializer &&
      ["window", "globalThis", "self"].some(
        (name) => ts.isIdentifier(node.initializer) && node.initializer.text === name,
      ) &&
      node.name &&
      ts.isObjectBindingPattern(node.name) &&
      node.name.elements.some((element) => {
        const property = element.propertyName ?? element.name;
        return ts.isIdentifier(property) && property.text === "fetch";
      })
    ) {
      found = true;
      return;
    }

    if (ts.isIdentifier(node) && node.text === "fetch" && !isIdentifierDeclarationName(node)) {
      found = true;
      return;
    }

    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return found;
}

function containsBrowserStorageAccess(sourceFile) {
  let found = false;
  const storageNames = ["localStorage", "sessionStorage"];

  function visit(node) {
    if (isGlobalBrowserProperty(node, storageNames)) {
      found = true;
      return;
    }
    if (
      ts.isIdentifier(node) &&
      storageNames.includes(node.text) &&
      !isIdentifierDeclarationName(node)
    ) {
      found = true;
      return;
    }
    if (
      ts.isVariableDeclaration(node) &&
      node.initializer &&
      ["window", "globalThis", "self"].some(
        (name) => ts.isIdentifier(node.initializer) && node.initializer.text === name,
      ) &&
      node.name &&
      ts.isObjectBindingPattern(node.name) &&
      node.name.elements.some((element) => {
        const property = element.propertyName ?? element.name;
        return ts.isIdentifier(property) && storageNames.includes(property.text);
      })
    ) {
      found = true;
      return;
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return found;
}

for (const file of collectFiles(srcRoot)) {
  const relative = path.relative(srcRoot, file).split(path.sep).join("/");
  const source = readFileSync(file, "utf8");
  const scriptKind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, scriptKind);

  function inspectJsx(node) {
    if (
      ts.isJsxElement(node) &&
      ts.isIdentifier(node.openingElement.tagName) &&
      node.openingElement.tagName.text.toLowerCase() === "pre" &&
      node.children.some(containsJsonStringifyCall)
    ) {
      failures.push(`${relative}: render domain data instead of dumping JSON into <pre>.`);
    }
    ts.forEachChild(node, inspectJsx);
  }
  inspectJsx(sourceFile);

  const isTestFile = relative.endsWith(".test.ts") || relative.endsWith(".test.tsx");
  if (!isTestFile && relative !== "lib/api/client.ts" && containsRawFetchAccess(sourceFile)) {
    failures.push(
      `${relative}: use the shared authenticated API client instead of raw fetch or aliases.`,
    );
  }

  if (
    !relative.endsWith(".test.ts") &&
    !relative.endsWith(".test.tsx") &&
    !relative.startsWith("lib/i18n/") &&
    containsBrowserStorageAccess(sourceFile)
  ) {
    failures.push(
      `${relative}: browser storage is forbidden for admin credentials; keep tokens memory-only.`,
    );
  }

  const sourceEntity = relative.match(/^entities\/([^/]+)\//);
  const sourceFeature = relative.match(/^features\/([^/]+)\//);
  const isUiPrimitive = relative.startsWith("components/ui/");

  for (const specifier of collectImportSpecifiers(sourceFile)) {
    if (specifier === "@muse-studio/contracts" || specifier.startsWith("@muse-studio/contracts/")) {
      failures.push(
        `${relative}: import "${specifier}" targets the retired contracts package; use generated OpenAPI types from lib/api/schema.d.ts.`,
      );
      continue;
    }

    const targetPath = resolveLocalImport(file, specifier);
    if (!targetPath) continue;

    const targetRelative = path
      .relative(srcRoot, path.resolve(targetPath))
      .split(path.sep)
      .join("/");
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

// The dev and preview servers proxy the (possibly unauthenticated) development API, so they
// must default to loopback. Only docker-compose.yml may pass `--host 0.0.0.0` explicitly,
// because it publishes the container port on the host's 127.0.0.1 only.
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

function checkDevServerExposure() {
  const packageJson = JSON.parse(readFileSync(path.join(appRoot, "package.json"), "utf8"));
  for (const name of ["dev", "preview"]) {
    const script = packageJson.scripts?.[name] ?? "";
    const hostFlag = /--host(?:[=\s]+(\S+))?/.exec(script);
    if (hostFlag && !LOOPBACK_HOSTS.has(hostFlag[1] ?? "")) {
      failures.push(
        `package.json: "${name}" must not expose Vite beyond loopback ("${script}"); ` +
          "pass --host only from the Compose dev command.",
      );
    }
  }

  const configPath = path.join(appRoot, "vite.config.ts");
  const config = ts.createSourceFile(
    configPath,
    readFileSync(configPath, "utf8"),
    ts.ScriptTarget.Latest,
    true,
  );
  const constants = new Map();
  const hosts = new Map();
  function literalValue(node) {
    if (ts.isStringLiteralLike(node)) return node.text;
    if (node.kind === ts.SyntaxKind.TrueKeyword) return true;
    if (ts.isIdentifier(node) && constants.has(node.text)) return constants.get(node.text);
    return undefined;
  }
  function visit(node) {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      const value = literalValue(node.initializer);
      if (value !== undefined) constants.set(node.name.text, value);
    }
    if (ts.isPropertyAssignment(node) && ts.isIdentifier(node.name)) {
      const parent = node.parent?.parent;
      const section =
        parent && ts.isPropertyAssignment(parent) && ts.isIdentifier(parent.name)
          ? parent.name.text
          : null;
      if (node.name.text === "host" && (section === "server" || section === "preview")) {
        hosts.set(section, literalValue(node.initializer));
      }
      if (node.name.text === "changeOrigin" && literalValue(node.initializer) === true) {
        failures.push(
          "vite.config.ts: proxy changeOrigin must stay disabled so the API's Host checks see " +
            "the browser-facing host.",
        );
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(config);
  for (const section of ["server", "preview"]) {
    if (!LOOPBACK_HOSTS.has(hosts.get(section))) {
      failures.push(
        `vite.config.ts: ${section}.host must be an explicit loopback literal ` +
          `(found ${JSON.stringify(hosts.get(section) ?? null)}).`,
      );
    }
  }
}

checkDevServerExposure();

if (failures.length > 0) {
  console.error("Frontend boundary check failed:");
  for (const failure of failures) console.error(` - ${failure}`);
  process.exitCode = 1;
} else {
  console.log("Frontend boundary check passed.");
}

#!/usr/bin/env node

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const frontendRoot = join(repoRoot, "frontend");
const sourceRoot = join(frontendRoot, "src");

const rootApiCompatibilityConsumers = new Set(["src/api.test.ts"]);

const featurePatterns = [
  ["chat", /^src\/(?:features\/chat|chat)\//],
  ["settings", /^src\/(?:features\/settings|settings)\//],
  ["characters", /^src\/(?:features\/characters|characters)\//],
  ["destiny", /^(?:src\/features\/destiny\/|src\/DestinyCanvas(?:\.test)?\.tsx$)/],
  ["scenes", /^(?:src\/features\/scenes\/|src\/SceneExperience(?:\.test)?\.tsx$)/],
];

function toModulePath(filePath) {
  return relative(frontendRoot, filePath).replaceAll("\\", "/");
}

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(entryPath);
    return /\.(?:ts|tsx)$/.test(entry.name) ? [entryPath] : [];
  });
}

function isIdentifierStart(character) {
  return /[A-Za-z_$]/.test(character);
}

function isIdentifierPart(character) {
  return /[A-Za-z0-9_$]/.test(character);
}

function regexCanStartAfter(previous) {
  if (!previous) return true;
  if (previous.kind === "word") {
    return /^(?:await|case|delete|do|else|in|instanceof|new|of|return|throw|typeof|void|yield)$/.test(previous.value);
  }
  return previous.kind === "punct" && /^[([{,:;=!?&|+*%^~<>-]$/.test(previous.value);
}

// This lexer only retains tokens needed to recognize module declarations. It
// deliberately skips comments and regular expressions so words inside them
// cannot be mistaken for imports.
function tokenize(source) {
  const tokens = [];
  let index = 0;
  let line = 1;

  const push = (kind, value, tokenLine = line, extra = {}) => {
    tokens.push({ kind, value, line: tokenLine, ...extra });
  };

  while (index < source.length) {
    const character = source[index];
    const next = source[index + 1];

    if (/\s/.test(character)) {
      if (character === "\n") line += 1;
      index += 1;
      continue;
    }

    if (character === "/" && next === "/") {
      index += 2;
      while (index < source.length && source[index] !== "\n") index += 1;
      continue;
    }

    if (character === "/" && next === "*") {
      index += 2;
      while (index < source.length && !(source[index] === "*" && source[index + 1] === "/")) {
        if (source[index] === "\n") line += 1;
        index += 1;
      }
      index = Math.min(index + 2, source.length);
      continue;
    }

    if (character === "'" || character === '"') {
      const quote = character;
      const tokenLine = line;
      let value = "";
      index += 1;
      while (index < source.length && source[index] !== quote) {
        if (source[index] === "\\" && index + 1 < source.length) {
          value += source[index + 1];
          index += 2;
          continue;
        }
        if (source[index] === "\n") line += 1;
        value += source[index];
        index += 1;
      }
      index = Math.min(index + 1, source.length);
      push("string", value, tokenLine);
      continue;
    }

    if (character === "`") {
      const tokenLine = line;
      let value = "";
      let interpolated = false;
      index += 1;
      while (index < source.length && source[index] !== "`") {
        if (source[index] === "\\" && index + 1 < source.length) {
          value += source[index + 1];
          index += 2;
          continue;
        }
        if (source[index] === "$" && source[index + 1] === "{") interpolated = true;
        if (source[index] === "\n") line += 1;
        value += source[index];
        index += 1;
      }
      index = Math.min(index + 1, source.length);
      push("template", value, tokenLine, { interpolated });
      continue;
    }

    if (character === "/" && regexCanStartAfter(tokens.at(-1))) {
      index += 1;
      let inCharacterClass = false;
      while (index < source.length) {
        if (source[index] === "\\") {
          index += 2;
          continue;
        }
        if (source[index] === "[") inCharacterClass = true;
        if (source[index] === "]") inCharacterClass = false;
        if (source[index] === "/" && !inCharacterClass) {
          index += 1;
          while (/[A-Za-z]/.test(source[index] ?? "")) index += 1;
          break;
        }
        if (source[index] === "\n") break;
        index += 1;
      }
      push("regex", "");
      continue;
    }

    if (isIdentifierStart(character)) {
      const tokenLine = line;
      let value = character;
      index += 1;
      while (index < source.length && isIdentifierPart(source[index])) {
        value += source[index];
        index += 1;
      }
      push("word", value, tokenLine);
      continue;
    }

    push("punct", character);
    index += 1;
  }

  return tokens;
}

function scanDependencies(filePath) {
  const tokens = tokenize(readFileSync(filePath, "utf8"));
  const dependencies = [];
  const scanErrors = [];

  const addDependency = (token, kind) => {
    dependencies.push({ specifier: token.value, kind, line: token.line });
  };

  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token.kind !== "word" || (token.value !== "import" && token.value !== "export")) continue;

    if (token.value === "import" && tokens[index + 1]?.value === ".") continue;

    if (token.value === "import" && tokens[index + 1]?.value === "(") {
      const argument = tokens[index + 2];
      if (argument?.kind === "string" || (argument?.kind === "template" && !argument.interpolated)) {
        addDependency(argument, "dynamic-import");
      } else {
        scanErrors.push({
          rule: "dynamic-import-must-be-literal",
          line: token.line,
          message: "Dynamic imports must use one string literal so boundary checks cannot be bypassed.",
        });
      }
      continue;
    }

    if (token.value === "import" && tokens[index + 1]?.kind === "string") {
      addDependency(tokens[index + 1], "import");
      continue;
    }

    for (let cursor = index + 1; cursor < Math.min(tokens.length, index + 80); cursor += 1) {
      const candidate = tokens[cursor];
      if (candidate.value === ";") break;
      if (cursor > index + 1 && candidate.kind === "word" && /^(?:import|export)$/.test(candidate.value)) break;
      if (candidate.kind === "word" && candidate.value === "from" && tokens[cursor + 1]?.kind === "string") {
        addDependency(tokens[cursor + 1], token.value === "import" ? "import" : "export-from");
        break;
      }
    }
  }

  return { dependencies, scanErrors };
}

function resolveLocalDependency(importer, specifier) {
  if (!specifier.startsWith(".")) return null;
  const base = resolve(dirname(importer), specifier);
  const candidates = extname(base)
    ? [base]
    : [`${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx"), base];
  const resolved = candidates.find((candidate) => existsSync(candidate)) ?? base;
  return toModulePath(resolved);
}

function featureOf(modulePath) {
  const publicFeature = /^src\/features\/([^/]+)\//.exec(modulePath)?.[1];
  if (publicFeature) return publicFeature;
  return featurePatterns.find(([, pattern]) => pattern.test(modulePath))?.[0] ?? null;
}

function isFoundation(modulePath) {
  return /^(?:src\/(?:api|chat-contract|types)\.ts|src\/ui\/|src\/shared\/)/.test(modulePath);
}

function isAppEntry(modulePath) {
  return /^(?:src\/(?:App|main)\.tsx|src\/app\/)/.test(modulePath);
}

function isStructuredAppDependency(modulePath) {
  return /^(?:src\/app\/|src\/shared\/)/.test(modulePath)
    || /^src\/features\/[^/]+\/index\.(?:ts|tsx)$/.test(modulePath);
}

function boundaryViolations(from, to) {
  const violations = [];
  const fromFeature = featureOf(from);
  const toFeature = featureOf(to);

  if (fromFeature && toFeature && fromFeature !== toFeature) {
    violations.push({
      rule: "features-no-cross-import",
      message: `Feature '${fromFeature}' must not import feature '${toFeature}' directly.`,
    });
  }

  if (isFoundation(from) && (toFeature || isAppEntry(to))) {
    violations.push({
      rule: "foundation-no-upward-import",
      message: "Shared/root foundation code must not depend on app or feature code.",
    });
  }

  if (fromFeature && isAppEntry(to)) {
    violations.push({
      rule: "features-no-entrypoint-import",
      message: "Feature code must not import App, main, or app-shell modules.",
    });
  }

  if (to === "src/api.ts" && !rootApiCompatibilityConsumers.has(from)) {
    violations.push({
      rule: "no-new-root-api-consumer",
      message: "This file is not an approved legacy consumer of src/api.ts.",
    });
  }

  if (
    from === "src/App.tsx"
    && to.startsWith("src/")
    && !isStructuredAppDependency(to)
  ) {
    violations.push({
      rule: "app-no-new-internal-dependency",
      message: "App.tsx may import only app/shared modules and feature public indexes.",
    });
  }

  return violations;
}

function main() {
  const unsupportedArguments = process.argv.slice(2).filter((argument) => argument !== "--help");
  if (process.argv.includes("--help")) {
    console.log("Usage: node scripts/verify-frontend-boundaries.mjs");
    console.log("Checks static imports, export-from declarations, and literal dynamic imports under frontend/src.");
    return;
  }
  if (unsupportedArguments.length > 0) {
    throw new Error(`Unknown argument(s): ${unsupportedArguments.join(", ")}`);
  }

  const files = sourceFiles(sourceRoot).sort();
  const violations = [];
  let localDependencyCount = 0;

  for (const filePath of files) {
    const from = toModulePath(filePath);
    const { dependencies, scanErrors } = scanDependencies(filePath);

    for (const error of scanErrors) {
      violations.push({ ...error, from, kind: "dynamic-import", specifier: "<non-literal>", to: null });
    }

    for (const dependency of dependencies) {
      const to = resolveLocalDependency(filePath, dependency.specifier);
      if (!to) continue;
      localDependencyCount += 1;
      for (const violation of boundaryViolations(from, to)) {
        violations.push({ ...violation, from, to, ...dependency });
      }
    }
  }

  violations.sort((left, right) =>
    left.from.localeCompare(right.from) || left.line - right.line || left.rule.localeCompare(right.rule),
  );

  if (violations.length === 0) {
    console.log(`Frontend boundaries OK: ${files.length} source files and ${localDependencyCount} local dependencies checked.`);
    return;
  }

  console.error(`Frontend boundary violations (${violations.length}):`);
  for (const violation of violations) {
    const target = violation.to ? ` -> ${violation.to}` : "";
    console.error(`- [${violation.rule}] ${violation.from}:${violation.line}${target}`);
    console.error(`  ${violation.kind}: ${violation.specifier}`);
    console.error(`  ${violation.message}`);
  }
  process.exitCode = 1;
}

try {
  main();
} catch (error) {
  console.error(`Frontend boundary check failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 2;
}

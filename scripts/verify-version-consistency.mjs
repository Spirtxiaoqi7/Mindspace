import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = process.env.MINDSPACE_VERSION_ROOT
  ? path.resolve(process.env.MINDSPACE_VERSION_ROOT)
  : path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SUPPORTED_ALLOWLIST_SCHEMA = "1.0.0";
const failures = [];
const readText = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const readJson = (relative) => JSON.parse(readText(relative));
const contract = readJson("config/version.json");
const productVersion = contract.product_version;

function fail(message) {
  failures.push(message);
}

function captured(relative, pattern, label) {
  const match = readText(relative).match(pattern);
  if (!match) {
    fail(`${relative}: cannot independently read ${label}`);
    return "";
  }
  return match[1];
}

function inspectRelativePath(raw, label, seen) {
  if (typeof raw !== "string" || raw.length === 0 || raw.trim() !== raw) {
    fail(`${label} must be a non-empty string without surrounding whitespace`);
    return "";
  }
  const portable = raw.replaceAll("\\", "/");
  const segments = portable.split("/");
  if (/^\d+$/.test(portable)) fail(`${label} must not be a numeric array index: ${raw}`);
  if (portable.startsWith("/") || /^[A-Za-z]:\//.test(portable) || portable.startsWith("//")) fail(`${label} must be repository-relative: ${raw}`);
  if (segments.some((segment) => !segment || segment === "." || segment === "..")) fail(`${label} contains an empty, dot or parent segment: ${raw}`);
  if (/[\u0000-\u001f]/.test(raw)) fail(`${label} contains control characters`);
  const key = portable.toLowerCase();
  if (seen.has(key)) fail(`${label} duplicates ${seen.get(key)}: ${raw}`);
  else seen.set(key, label);
  return raw;
}

function independentlyExpectedReleaseTargets() {
  const allowlist = readJson("config/core-release-allowlist.json");
  if (!allowlist || typeof allowlist !== "object" || Array.isArray(allowlist)) {
    fail("core release allowlist must be an object");
    return [];
  }
  if (allowlist.schema_version !== SUPPORTED_ALLOWLIST_SCHEMA) fail(`unsupported core release allowlist schema: ${allowlist.schema_version ?? "missing"}`);
  if (!Array.isArray(allowlist.source_trees)) {
    fail("core release allowlist source_trees must be an array");
    return [];
  }
  if (!Array.isArray(allowlist.runtime_files)) {
    fail("core release allowlist runtime_files must be an array");
    return [];
  }
  const expected = [];
  const seen = new Map();
  allowlist.source_trees.forEach((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      fail(`source_trees[${index}] must be an object`);
      return;
    }
    if (!Array.isArray(item.extensions) || item.extensions.length === 0 || item.extensions.some((extension) => typeof extension !== "string" || !/^\.[A-Za-z0-9]+$/.test(extension))) {
      fail(`source_trees[${index}].extensions must be a non-empty extension array`);
    }
    const value = inspectRelativePath(item.path, `source_trees[${index}].path`, seen);
    if (value) expected.push(value);
  });
  allowlist.runtime_files.forEach((item, index) => {
    const value = inspectRelativePath(item, `runtime_files[${index}]`, seen);
    if (value) expected.push(value);
  });
  return expected;
}

if (!/^\d+\.\d+\.\d+$/.test(productVersion)) fail(`config/version.json has invalid product_version: ${productVersion}`);
if (captured("pyproject.toml", /^version = "([^"]+)"/m, "project version") !== productVersion) fail("pyproject.toml version differs from product_version");
if (captured("uv.lock", /\[\[package\]\]\r?\nname = "mindspace-langgraph"\r?\nversion = "([^"]+)"/, "locked project version") !== productVersion) fail("uv.lock project version differs from product_version");
if (captured("src/mindspace_graph/version.py", /^APP_VERSION = "([^"]+)"/m, "APP_VERSION") !== productVersion) fail("Python APP_VERSION differs from product_version");

for (const folder of ["frontend", "desktop"]) {
  const pkg = readJson(`${folder}/package.json`);
  const lock = readJson(`${folder}/package-lock.json`);
  const lockedRoot = lock.packages?.[""];
  if (pkg.version !== productVersion) fail(`${folder}/package.json version differs from product_version`);
  if (lockedRoot?.version !== productVersion) fail(`${folder}/package-lock.json root version differs from product_version`);
  if (JSON.stringify(lockedRoot?.dependencies ?? {}) !== JSON.stringify(pkg.dependencies ?? {})) fail(`${folder}/package-lock.json dependencies differ from package.json`);
  if (JSON.stringify(lockedRoot?.devDependencies ?? {}) !== JSON.stringify(pkg.devDependencies ?? {})) fail(`${folder}/package-lock.json devDependencies differ from package.json`);
}

const expectedTargets = independentlyExpectedReleaseTargets();
const payload = readJson("payload.json");
if (payload.schema_version !== SUPPORTED_ALLOWLIST_SCHEMA) fail(`payload.json schema_version must be ${SUPPORTED_ALLOWLIST_SCHEMA}`);
if (payload.version !== productVersion) fail("payload.json version differs from product_version");
if (!Array.isArray(payload.targets)) {
  fail("payload.json targets must be an array");
} else {
  const payloadSeen = new Map();
  payload.targets.forEach((item, index) => inspectRelativePath(item, `payload.targets[${index}]`, payloadSeen));
  if (JSON.stringify(payload.targets) !== JSON.stringify(expectedTargets)) fail("payload.json targets must exactly equal source_trees[].path followed by runtime_files");
}

const history = readJson("docs/release-history.json");
const expectedHistory = {
  version: productVersion,
  published_at: contract.release_date,
  status: contract.release_status,
  title: contract.release_title,
  summary: contract.release_summary,
};
if (!Array.isArray(history) || JSON.stringify(history[0]) !== JSON.stringify(expectedHistory)) fail("docs/release-history.json first entry differs from the version contract");

const runtime = readJson("desktop/assets/runtime-manifest.json");
if (runtime.runtime_version !== contract.runtime_bundle.manifest_version) fail("runtime manifest version differs from the version contract");
const coreEnvironment = runtime.components?.find((item) => item.id === "core-venv");
const pythonRuntime = runtime.components?.find((item) => item.id === "python");
if (coreEnvironment?.version !== contract.runtime_bundle.core_environment_version) fail("runtime manifest core-venv version differs from the version contract");
if (pythonRuntime?.version !== contract.runtime_bundle.python_version) fail("runtime manifest Python version differs from the version contract");

const bootstrapPath = path.join(root, "desktop/bootstrap/manifest.json");
if (fs.existsSync(bootstrapPath) && readJson("desktop/bootstrap/manifest.json").version !== productVersion) fail("generated bootstrap manifest differs from product_version");

if (failures.length) {
  console.error(`Independent version consistency failed:\n- ${[...new Set(failures)].join("\n- ")}`);
  process.exit(1);
}
console.log(`Independent version consistency verified: ${productVersion}; release targets=${expectedTargets.length}`);

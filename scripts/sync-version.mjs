import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = process.env.MINDSPACE_VERSION_ROOT
  ? path.resolve(process.env.MINDSPACE_VERSION_ROOT)
  : path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const checkOnly = process.argv.includes("--check");
const readText = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const readJson = (relative) => JSON.parse(readText(relative));
const writeJson = (relative, value) => {
  fs.writeFileSync(path.join(root, relative), `${JSON.stringify(value, null, 2)}\n`, "utf8");
};
const contract = readJson("config/version.json");
const version = contract.product_version;
const failures = [];
const RELEASE_ALLOWLIST_SCHEMA = "1.0.0";

if (!/^\d+\.\d+\.\d+$/.test(version)) {
  throw new Error(`Invalid product_version in config/version.json: ${version}`);
}

function replaceRequired(relative, pattern, replacement, label) {
  const before = readText(relative);
  if (!pattern.test(before)) throw new Error(`Cannot find ${label} in ${relative}`);
  const after = before.replace(pattern, replacement);
  if (checkOnly) {
    if (after !== before) failures.push(`${relative}: ${label} is not ${version}`);
  } else if (after !== before) {
    fs.writeFileSync(path.join(root, relative), after, "utf8");
  }
}

function syncPackage(relative) {
  const pkg = readJson(relative);
  if (checkOnly) {
    if (pkg.version !== version) failures.push(`${relative}: version=${pkg.version}`);
    return;
  }
  pkg.version = version;
  writeJson(relative, pkg);
}

function syncLock(relative, packageRelative) {
  const lock = readJson(relative);
  const pkg = readJson(packageRelative);
  const rootPackage = lock.packages?.[""];
  if (!rootPackage) throw new Error(`${relative} has no packages[\"\"] root record`);
  if (checkOnly) {
    if (rootPackage.version !== version) failures.push(`${relative}: root version=${rootPackage.version}`);
    if (JSON.stringify(rootPackage.dependencies ?? {}) !== JSON.stringify(pkg.dependencies ?? {})) failures.push(`${relative}: root dependencies differ from ${packageRelative}`);
    if (JSON.stringify(rootPackage.devDependencies ?? {}) !== JSON.stringify(pkg.devDependencies ?? {})) failures.push(`${relative}: root devDependencies differ from ${packageRelative}`);
    return;
  }
  lock.version = version;
  rootPackage.version = version;
  rootPackage.dependencies = pkg.dependencies ?? {};
  rootPackage.devDependencies = pkg.devDependencies ?? {};
  writeJson(relative, lock);
}

function validatedReleaseTargets(allowlist) {
  if (!allowlist || typeof allowlist !== "object" || Array.isArray(allowlist)) throw new Error("core release allowlist must be an object");
  if (allowlist.schema_version !== RELEASE_ALLOWLIST_SCHEMA) throw new Error(`Unsupported core release allowlist schema: ${allowlist.schema_version ?? "missing"}`);
  if (!Array.isArray(allowlist.source_trees)) throw new Error("core release allowlist source_trees must be an object array");
  if (!Array.isArray(allowlist.runtime_files)) throw new Error("core release allowlist runtime_files must be a string array");
  const targets = [];
  const seen = new Map();
  const addPath = (raw, label) => {
    if (typeof raw !== "string" || raw.length === 0 || raw.trim() !== raw) throw new Error(`${label} must be a non-empty path without surrounding whitespace`);
    const portable = raw.replaceAll("\\", "/");
    const segments = portable.split("/");
    if (/^\d+$/.test(portable) || portable.startsWith("/") || /^[A-Za-z]:\//.test(portable) || portable.startsWith("//") || segments.some((segment) => !segment || segment === "." || segment === "..") || /[\u0000-\u001f]/.test(raw)) {
      throw new Error(`${label} is not a safe repository-relative path: ${raw}`);
    }
    const key = portable.toLowerCase();
    if (seen.has(key)) throw new Error(`${label} duplicates ${seen.get(key)}: ${raw}`);
    seen.set(key, label);
    targets.push(raw);
  };
  allowlist.source_trees.forEach((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error(`source_trees[${index}] must be an object`);
    if (!Array.isArray(item.extensions) || item.extensions.length === 0 || item.extensions.some((extension) => typeof extension !== "string" || !/^\.[A-Za-z0-9]+$/.test(extension))) {
      throw new Error(`source_trees[${index}].extensions must be a non-empty extension array`);
    }
    addPath(item.path, `source_trees[${index}].path`);
  });
  allowlist.runtime_files.forEach((item, index) => addPath(item, `runtime_files[${index}]`));
  return targets;
}

replaceRequired("pyproject.toml", /^version = "[^"]+"/m, `version = "${version}"`, "project version");
replaceRequired(
  "uv.lock",
  /(\[\[package\]\]\r?\nname = "mindspace-langgraph"\r?\nversion = ")[^"]+("\r?\n)/,
  `$1${version}$2`,
  "locked project version",
);
replaceRequired(
  "src/mindspace_graph/version.py",
  /^APP_VERSION = "[^"]+"/m,
  `APP_VERSION = "${version}"`,
  "Python APP_VERSION",
);

for (const relative of ["frontend/package.json", "desktop/package.json"]) syncPackage(relative);
syncLock("frontend/package-lock.json", "frontend/package.json");
syncLock("desktop/package-lock.json", "desktop/package.json");

const allowlist = readJson("config/core-release-allowlist.json");
const releaseTargets = validatedReleaseTargets(allowlist);
const expectedPayload = {
  targets: releaseTargets,
  schema_version: allowlist.schema_version,
  requires_dependency_sync: false,
  version,
};
if (checkOnly) {
  const actual = readJson("payload.json");
  if (JSON.stringify(actual) !== JSON.stringify(expectedPayload)) failures.push("payload.json is not generated from the release allowlist and version contract");
} else {
  writeJson("payload.json", expectedPayload);
}

const historyPath = "docs/release-history.json";
const history = readJson(historyPath);
const entry = {
  version,
  published_at: contract.release_date,
  status: contract.release_status,
  title: contract.release_title,
  summary: contract.release_summary,
};
if (!Array.isArray(history)) throw new Error(`${historyPath} must be a release array`);
if (checkOnly) {
  if (JSON.stringify(history[0]) !== JSON.stringify(entry)) failures.push(`${historyPath}: first release is not the canonical ${version} entry`);
} else {
  writeJson(historyPath, [entry, ...history.filter((item) => item.version !== version)]);
}

const runtime = readJson("desktop/assets/runtime-manifest.json");
if (runtime.runtime_version !== contract.runtime_bundle.manifest_version) failures.push("runtime-manifest runtime_version differs from config/version.json");
const coreEnvironment = runtime.components?.find((item) => item.id === "core-venv");
if (coreEnvironment?.version !== contract.runtime_bundle.core_environment_version) failures.push("runtime-manifest core-venv version differs from config/version.json");
const pythonRuntime = runtime.components?.find((item) => item.id === "python");
if (pythonRuntime?.version !== contract.runtime_bundle.python_version) failures.push("runtime-manifest Python version differs from config/version.json");

const bootstrapPath = path.join(root, "desktop/bootstrap/manifest.json");
if (fs.existsSync(bootstrapPath)) {
  const bootstrap = JSON.parse(fs.readFileSync(bootstrapPath, "utf8"));
  if (bootstrap.version !== version) failures.push("generated bootstrap manifest does not match product_version");
}

if (failures.length) {
  throw new Error(`Version consistency failed:\n- ${failures.join("\n- ")}`);
}
console.log(checkOnly ? `Version contract verified: ${version}` : `Version consumers synchronized: ${version}`);

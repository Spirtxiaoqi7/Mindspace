import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const failures = [];
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const json = (relative) => JSON.parse(read(relative));
const productVersion = json("config/version.json").product_version;
const normalize = (value) => value.replaceAll("\\", "/").toLowerCase();
const excludedDirectories = new Set([".git", "node_modules", ".venv", "dist", "build", "reports", "vendor", "artifacts", "runtime", "desktop/bootstrap"]);

function isExcludedDirectory(name, relative) {
  const normalized = normalize(relative);
  return excludedDirectories.has(name)
    || excludedDirectories.has(normalized)
    || name.startsWith(".real-api-")
    || name.startsWith(".runtime-")
    || name.startsWith(".test-tmp")
    || name.startsWith(".tmp")
    || name.startsWith(".venv-")
    || name.startsWith(".deploy-")
    || name.startsWith("dist-launcher");
}

function walk(directory, output = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory() && isExcludedDirectory(entry.name, path.relative(root, full))) continue;
    if (entry.isDirectory()) walk(full, output);
    else output.push(full);
  }
  return output;
}

for (const relative of ["frontend/package.json", "desktop/package.json"]) {
  const pkg = json(relative);
  for (const group of ["dependencies", "devDependencies"]) {
    for (const [name, spec] of Object.entries(pkg[group] ?? {})) {
      if (spec === "latest" || /^[~^*]/.test(spec)) failures.push(`${relative}: ${group}.${name} is not exactly pinned (${spec})`);
    }
  }
}

const dockerfile = read("Dockerfile");
if (!dockerfile.includes("FROM python:3.11.15-slim")) failures.push("Dockerfile must use the supported Python 3.11.15 runtime");
if (!dockerfile.includes("uv sync --frozen --no-dev")) failures.push("Dockerfile must install from uv.lock with --frozen");
if (/pip install(?:\s+--[^\s]+)*\s+\./.test(dockerfile)) failures.push("Dockerfile must not bypass uv.lock with pip install .");

const ignore = read(".gitignore");
for (const required of ["reports/", ".real-api-*", ".runtime-*", ".test-tmp", ".tmp", "desktop/bootstrap/"]) {
  if (!ignore.includes(required)) failures.push(`.gitignore is missing ${required}`);
}
if (/^payload\.json$/m.test(ignore)) failures.push("payload.json is a governed version artifact and must not be ignored");

for (const relative of ["scripts/run_082_real_api_regression.py", "scripts/run_082_two_card_tool_benchmark.py"]) {
  const source = read(relative);
  if (/read_(?:text|bytes)|json\.load|open\s*\(/.test(source)) failures.push(`${relative}: deprecated tombstone must not read files`);
  if (/api_key|llm_api_key|sk-[a-z0-9]/i.test(source)) failures.push(`${relative}: deprecated tombstone must not contain credential handling`);
  if (!source.includes("raise SystemExit")) failures.push(`${relative}: deprecated entry point must fail closed`);
}

const allowlist = json("config/core-release-allowlist.json");
if (allowlist.schema_version !== "1.0.0") failures.push(`unsupported core release allowlist schema: ${allowlist.schema_version ?? "missing"}`);
if (!Array.isArray(allowlist.source_trees)) failures.push("core release allowlist source_trees must be an object array");
if (!Array.isArray(allowlist.runtime_files)) failures.push("core release allowlist runtime_files must be a string array");
const policySeenTargets = new Map();
const policyTarget = (raw, label) => {
  if (typeof raw !== "string" || raw.length === 0 || raw.trim() !== raw) {
    failures.push(`${label} must be a non-empty path without surrounding whitespace`);
    return "";
  }
  const portable = raw.replaceAll("\\", "/");
  const segments = portable.split("/");
  if (/^\d+$/.test(portable) || portable.startsWith("/") || /^[A-Za-z]:\//.test(portable) || portable.startsWith("//") || segments.some((segment) => !segment || segment === "." || segment === "..")) {
    failures.push(`${label} is not a safe repository-relative path: ${raw}`);
  }
  const key = portable.toLowerCase();
  if (policySeenTargets.has(key)) failures.push(`${label} duplicates ${policySeenTargets.get(key)}: ${raw}`);
  else policySeenTargets.set(key, label);
  return raw;
};
const sourceTreeTargets = Array.isArray(allowlist.source_trees)
  ? allowlist.source_trees.map((item, index) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        failures.push(`source_trees[${index}] must be an object`);
        return "";
      }
      if (!Array.isArray(item.extensions) || item.extensions.length === 0) failures.push(`source_trees[${index}].extensions must be a non-empty array`);
      return policyTarget(item.path, `source_trees[${index}].path`);
    }).filter(Boolean)
  : [];
const runtimeTargets = Array.isArray(allowlist.runtime_files)
  ? allowlist.runtime_files.map((item, index) => policyTarget(item, `runtime_files[${index}]`)).filter(Boolean)
  : [];
const releaseTargets = [...sourceTreeTargets, ...runtimeTargets];
for (const target of releaseTargets) {
  const value = normalize(target);
  if (value.includes("reports") || value.includes(".real-api") || value.includes(".runtime-") || value.endsWith(".map")) {
    failures.push(`release allowlist contains forbidden local/generated target: ${target}`);
  }
}
const payload = json("payload.json");
if (payload.schema_version !== allowlist.schema_version) failures.push("payload.json schema_version differs from the core release allowlist");
if (!Array.isArray(payload.targets)) failures.push("payload.json targets must be an array");
const payloadSeenTargets = new Map();
for (const [index, target] of (Array.isArray(payload.targets) ? payload.targets : []).entries()) {
  if (typeof target !== "string" || /^\d+$/.test(target.replaceAll("\\", "/"))) failures.push(`payload.targets[${index}] is a numeric index or non-string path`);
  if (typeof target === "string") {
    const portable = target.replaceAll("\\", "/");
    const segments = portable.split("/");
    if (portable.startsWith("/") || /^[A-Za-z]:\//.test(portable) || portable.startsWith("//") || segments.some((segment) => !segment || segment === "." || segment === "..")) failures.push(`payload.targets[${index}] is not repository-relative: ${target}`);
    const key = portable.toLowerCase();
    if (payloadSeenTargets.has(key)) failures.push(`payload.targets[${index}] duplicates ${payloadSeenTargets.get(key)}: ${target}`);
    else payloadSeenTargets.set(key, `payload.targets[${index}]`);
  }
}
if (JSON.stringify(payload.targets ?? []) !== JSON.stringify(releaseTargets)) {
  failures.push("payload.json targets do not exactly match source_trees[].path + runtime_files from the release allowlist");
}

const currentDocs = [
  "README.md",
  "SECURITY.md",
  "docs/README.md",
  "docs/architecture/overview.md",
  "docs/architecture/frontend.md",
  "docs/architecture/backend.md",
  "docs/architecture/storage-memory.md",
  "docs/architecture/prompts-tools.md",
  "docs/architecture/desktop-runtime.md",
  "docs/development/workflow.md",
  "docs/development/testing.md",
  "docs/development/deprecations.md",
  "docs/operations/runtime.md",
  "docs/operations/packaging.md",
  "docs/operations/release.md",
  "docs/product/overview.md",
  "docs/product/characters-destiny.md",
  "docs/product/memory-context.md",
  "docs/product/voice.md",
  "docs/adr/0001-runtime-home.md",
  "docs/adr/0002-modular-boundaries.md",
  "docs/adr/0003-character-card-v2.md",
  "docs/adr/0004-single-tool-protocol.md",
  "docs/readme/ASSETS.md",
];
const documentIndex = read("docs/README.md");
for (const relative of currentDocs.filter((name) => name.startsWith("docs/") && name !== "docs/README.md")) {
  if (!documentIndex.includes(path.basename(relative))) failures.push(`docs/README.md does not link ${relative}`);
  const source = read(relative);
  if (/(?:A:\\Mindscape|A:\\RAG\\langgarph-rag)/i.test(source) && !/^> 文档状态：(historical|prototype|report)/m.test(source)) {
    failures.push(`${relative}: current documentation contains an obsolete path`);
  }
}
for (const relative of currentDocs) {
  if (!fs.existsSync(path.join(root, relative))) {
    failures.push(`current authority is missing: ${relative}`);
    continue;
  }
  const source = normalize(read(relative));
  if (source.includes("a:/mindscape") || source.includes("a:/rag/langgarph-rag")) failures.push(`${relative}: current documentation contains an obsolete executable path`);
}

for (const file of walk(root)) {
  const relative = normalize(path.relative(root, file));
  if (relative.endsWith(".map")) failures.push(`source map is forbidden in repository/release tree: ${relative}`);
  if (!/\.(?:md|txt|json|ya?ml|toml|ini|js|mjs|cjs|ts|tsx|py|ps1)$/i.test(relative)) continue;
  const source = fs.readFileSync(file, "utf8");
  const secretPrefix = ["s", "k", "-"].join("");
  const secretPattern = new RegExp(`${secretPrefix}[A-Za-z0-9_-]{20,}`);
  if (secretPattern.test(source) || /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/.test(source)) {
    failures.push(`possible committed secret/private key: ${relative}`);
  }
}

const bootstrapSource = read("desktop/prepare-bootstrap.cjs");
if (!bootstrapSource.includes("bootstrap/manifest.json") && !bootstrapSource.includes("manifest.json")) failures.push("desktop/prepare-bootstrap.cjs must own bootstrap manifest generation");
if (fs.existsSync(path.join(root, "desktop/bootstrap/manifest.json"))) failures.push("generated desktop/bootstrap/manifest.json must not be committed or precreated outside formal packaging");

const workflow = read(".github/workflows/ci.yml");
for (const command of ["uv run pytest -q", "test_api_route_contract.py", "npm run check", "npm test", "npm run build", "verify-version-consistency.mjs", "generate-codebase-index.mjs", "verify-current-doc-paths.mjs", "verify-repository-policy.mjs", "verify-cjs-syntax.mjs", "verify-powershell-syntax.ps1", `build-update.ps1 -Version ${productVersion} -SkipBuild -DryRun`]) {
  if (!workflow.includes(command)) failures.push(`CI workflow is missing required gate: ${command}`);
}

const currentDocPathCheck = spawnSync(process.execPath, [path.join(root, "scripts/verify-current-doc-paths.mjs")], {
  cwd: root,
  encoding: "utf8",
});
if (currentDocPathCheck.status !== 0) failures.push(`current documentation path check failed: ${(currentDocPathCheck.stderr || currentDocPathCheck.stdout).trim()}`);

if (failures.length) {
  console.error(`Repository policy failed:\n- ${[...new Set(failures)].join("\n- ")}`);
  process.exit(1);
}
console.log("Repository policy verified");

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const productVersion = JSON.parse(fs.readFileSync(path.join(root, "config/version.json"), "utf8")).product_version;
const currentDocs = [
  "README.md", "SECURITY.md", "docs/INDEX.md", "docs/APPLICATION_FULL_CHAIN.md", "docs/CODE_READING_GUIDE.md",
  "docs/MINDSPACE_FUNCTION_MAP.md", "docs/ONLINE_UPDATE_RELEASE.md", "docs/PACKAGING.md",
  "docs/READ_ONLY_CAPABILITIES.md", "docs/RUNTIME_RUNBOOK.md", "docs/VERIFICATION.md",
  `docs/DEVELOPMENT_WORKFLOW_${productVersion}.md`, "docs/LOCAL_REPORT_POLICY.md", "docs/VERSIONING_AND_GENERATED_ASSETS.md",
];
const rootFiles = new Set(["Dockerfile", "README.md", "SECURITY.md", "payload.json", "pyproject.toml", "uv.lock"]);
const relativePrefix = /^(?:src|frontend|desktop|scripts|config|tests|docs|\.github)\//;
const intentionallyAbsentGenerated = new Set(["desktop/bootstrap/manifest.json"]);
const nonPathExamples = new Set(["docs/deprecation-register"]);
const failures = [];

for (const document of currentDocs) {
  const source = fs.readFileSync(path.join(root, document), "utf8");
  for (const match of source.matchAll(/`([^`\r\n]+)`/g)) {
    let candidate = match[1].trim().replaceAll("\\", "/");
    if (!relativePrefix.test(candidate) && !rootFiles.has(candidate)) continue;
    if (/\s|[<>|]/.test(candidate)) continue;
    candidate = candidate.replace(/[),.;:]+$/, "");
    if (intentionallyAbsentGenerated.has(candidate) || nonPathExamples.has(candidate)) continue;
    const wildcard = candidate.search(/[?*{[]/);
    if (wildcard >= 0) {
      const prefix = candidate.slice(0, wildcard);
      const directory = path.dirname(prefix);
      const basenamePrefix = path.basename(prefix);
      const directoryPath = path.join(root, directory);
      if (!fs.existsSync(directoryPath) || !fs.readdirSync(directoryPath).some((name) => name.startsWith(basenamePrefix))) failures.push(`${document}: ${match[1]}`);
      continue;
    }
    candidate = candidate.replace(/\/$/, "");
    if (!candidate || !fs.existsSync(path.join(root, candidate))) failures.push(`${document}: ${match[1]}`);
  }
}

if (failures.length) {
  console.error(`Current documentation references missing paths:\n- ${[...new Set(failures)].join("\n- ")}`);
  process.exit(1);
}
console.log(`Current documentation paths verified: ${currentDocs.length} documents`);

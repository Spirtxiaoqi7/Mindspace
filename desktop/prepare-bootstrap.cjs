const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const pyproject = fs.readFileSync(path.join(projectRoot, "pyproject.toml"), "utf8");
const match = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
if (!match) throw new Error("pyproject.toml does not contain project.version");

const version = match[1];
const source = path.join(
  projectRoot,
  "runtime",
  "update-feed",
  `mindspace-core-${version}.zip`,
);
if (!fs.existsSync(source)) {
  throw new Error(`缺少 ${source}，请先运行 scripts/build-update.ps1 -Version ${version}`);
}

const targetRoot = path.join(__dirname, "bootstrap");
const target = path.join(targetRoot, "mindspace-core.zip");
fs.mkdirSync(targetRoot, { recursive: true });
fs.copyFileSync(source, target);
const payload = fs.readFileSync(target);
const manifest = {
  version,
  bytes: payload.length,
  sha256: crypto.createHash("sha256").update(payload).digest("hex"),
};
fs.writeFileSync(
  path.join(targetRoot, "manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
);
process.stdout.write(`${JSON.stringify(manifest)}\n`);

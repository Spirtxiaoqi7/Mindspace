import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

function argument(name, fallback = "") {
  const prefix = `--${name}=`;
  return process.argv.find((item) => item.startsWith(prefix))?.slice(prefix.length) ?? fallback;
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

const coreManifestPath = path.resolve(argument("core-manifest"));
const outputPath = path.resolve(argument("output"));
const privateKeyPath = path.resolve(argument("private-key"));
const channel = argument("channel", "stable");
const sequence = Number(argument("sequence"));
const rollout = Number(argument("rollout", "100"));
const historyFile = argument("history-file");
if (!fs.existsSync(coreManifestPath) || !fs.existsSync(privateKeyPath) || !outputPath) throw new Error("core-manifest, private-key and output are required");
if (!Number.isSafeInteger(sequence) || sequence < 1) throw new Error("sequence must be a positive integer");
if (!Number.isFinite(rollout) || rollout < 0 || rollout > 100) throw new Error("rollout must be between 0 and 100");

const coreManifest = JSON.parse(fs.readFileSync(coreManifestPath, "utf8"));
const summary = argument("notes", coreManifest.release_notes || `Mindspace ${coreManifest.version}`)
  .split("|").map((value) => value.trim()).filter(Boolean);
let releaseHistory = [];
if (historyFile) {
  const historyPath = path.resolve(historyFile);
  const parsed = JSON.parse(fs.readFileSync(historyPath, "utf8"));
  if (!Array.isArray(parsed)) throw new Error("release history must be an array");
  releaseHistory = parsed.map((entry) => {
    if (!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(String(entry?.version || ""))) throw new Error("release history contains an invalid version");
    if (!entry.title || !Array.isArray(entry.summary) || !entry.summary.length) throw new Error(`release history ${entry.version} is incomplete`);
    return {
      version: String(entry.version),
      published_at: String(entry.published_at || ""),
      title: String(entry.title),
      summary: entry.summary.map(String).filter(Boolean),
    };
  });
}
const unsigned = {
  schema_version: "2.0.0",
  channel,
  release_id: argument("release-id", `${new Date().toISOString().slice(0, 10)}-${coreManifest.version}`),
  sequence,
  published_at: new Date().toISOString(),
  rollout: { percentage: rollout, salt: argument("rollout-salt", `mindspace-${sequence}`) },
  core: {
    version: coreManifest.version,
    minimum_launcher: argument("minimum-launcher", "0.4.0"),
    mandatory: argument("mandatory-core", "false") === "true",
    release_notes: summary.join("\n"),
    package: coreManifest.package,
  },
  release_notes: { title: argument("title", `Mindspace ${coreManifest.version}`), summary },
  release_history: releaseHistory,
};
const launcherVersion = argument("launcher-version");
const launcherFeed = argument("launcher-feed");
if (launcherVersion && launcherFeed) {
  unsigned.launcher = {
    version: launcherVersion,
    feed_url: launcherFeed.replace(/\/?$/, "/"),
    mandatory: argument("mandatory-launcher", "false") === "true",
  };
}
const privateKey = fs.readFileSync(privateKeyPath, "utf8");
const signature = crypto.sign(null, Buffer.from(canonical(unsigned)), privateKey).toString("base64");
const catalog = { ...unsigned, signature: { algorithm: "ed25519", key_id: argument("key-id", "mindspace-release-1"), value: signature } };
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(catalog, null, 2)}\n`);
process.stdout.write(`${JSON.stringify({ output: outputPath, release_id: catalog.release_id, sequence, rollout })}\n`);

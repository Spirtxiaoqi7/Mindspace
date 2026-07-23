import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

function argument(name, fallback = "") {
  const prefix = `--${name}=`;
  return process.argv.find((item) => item.startsWith(prefix))?.slice(prefix.length) || fallback;
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

const packagePath = path.resolve(argument("package"));
const privateKeyPath = path.resolve(argument("private-key"));
const outputPath = path.resolve(argument("output"));
const version = argument("version");
const baseUrl = argument("base-url").replace(/\/+$/, "");
if (!version || !baseUrl || !fs.existsSync(packagePath) || !fs.existsSync(privateKeyPath)) {
  throw new Error("version, base-url, package and private-key are required");
}

const packageBytes = fs.readFileSync(packagePath);
const unsigned = {
  schema_version: "1.0.0",
  channel: argument("channel", "stable"),
  version,
  minimum_launcher: argument("minimum-launcher", "0.3.0"),
  mandatory: argument("mandatory", "false") === "true",
  published_at: new Date().toISOString(),
  release_notes: argument("notes", `Mindspace ${version}`),
  package: {
    url: `${baseUrl}/${path.basename(packagePath)}`,
    sha256: crypto.createHash("sha256").update(packageBytes).digest("hex"),
    size: packageBytes.length,
    format: "zip",
  },
};
const privateKey = fs.readFileSync(privateKeyPath, "utf8");
const signature = crypto.sign(null, Buffer.from(canonical(unsigned)), privateKey).toString("base64");
const manifest = { ...unsigned, signature: { algorithm: "ed25519", value: signature } };
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
process.stdout.write(`${outputPath}\n`);


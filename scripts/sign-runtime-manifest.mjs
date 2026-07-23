import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

const root = path.resolve(import.meta.dirname, "..");
const manifestPath = path.resolve(process.argv[2] || path.join(root, "desktop/assets/runtime-manifest.json"));
const privateKeyPath = path.resolve(process.argv[3] || path.join(root, "runtime/update-keys/private.pem"));
if (!fs.existsSync(privateKeyPath)) throw new Error("运行时清单签名私钥不存在");
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
delete manifest.signature;
const privateKey = fs.readFileSync(privateKeyPath, "utf8");
const value = crypto.sign(null, Buffer.from(canonical(manifest)), privateKey).toString("base64");
const signed = { ...manifest, signature: { algorithm: "ed25519", value } };
fs.writeFileSync(manifestPath, `${JSON.stringify(signed, null, 2)}\n`);
process.stdout.write(`${manifestPath}\n`);

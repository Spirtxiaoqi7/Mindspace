import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

const root = path.resolve(import.meta.dirname, "..");
const catalogUrl = process.argv.find((value) => value.startsWith("--url="))?.slice(6)
  || "https://douyinqijun.cn/downloads/mindspace/catalog/stable/windows-x64.json";
const full = process.argv.includes("--full");
const response = await fetch(catalogUrl, { cache: "no-store", headers: { "Cache-Control": "no-cache" } });
if (!response.ok) throw new Error(`Catalog HTTP ${response.status}`);
const contentType = response.headers.get("content-type") || "";
if (!/application\/(?:json|octet-stream)/i.test(contentType)) throw new Error(`Catalog MIME 错误：${contentType}；服务器可能返回了官网 HTML`);
const catalog = await response.json();
const unsigned = { ...catalog };
delete unsigned.signature;
const publicKey = fs.readFileSync(path.join(root, "desktop", "assets", "update-public-key.pem"), "utf8");
const valid = catalog.signature?.algorithm === "ed25519" && crypto.verify(
  null,
  Buffer.from(canonical(unsigned)),
  publicKey,
  Buffer.from(catalog.signature?.value || "", "base64"),
);
if (!valid) throw new Error("Catalog Ed25519 签名验证失败");
const packageUrl = new URL(catalog.core.package.url, catalogUrl).toString();
const range = await fetch(packageUrl, { headers: { Range: "bytes=0-0" } });
if (![200, 206].includes(range.status)) throw new Error(`Core Range 请求失败：HTTP ${range.status}`);
if (range.status === 206 && !/^bytes 0-0\//i.test(range.headers.get("content-range") || "")) throw new Error("Core Content-Range 无效");
let packageHash = "";
if (full) {
  const packageResponse = await fetch(packageUrl, { cache: "no-store" });
  if (!packageResponse.ok) throw new Error(`Core 下载失败：HTTP ${packageResponse.status}`);
  const bytes = Buffer.from(await packageResponse.arrayBuffer());
  if (bytes.length !== catalog.core.package.size) throw new Error(`Core 大小错误：${bytes.length} != ${catalog.core.package.size}`);
  packageHash = crypto.createHash("sha256").update(bytes).digest("hex");
  if (packageHash !== catalog.core.package.sha256.toLowerCase()) throw new Error("Core SHA-256 验证失败");
}
process.stdout.write(`${JSON.stringify({
  ok: true,
  catalog: catalogUrl,
  release_id: catalog.release_id,
  sequence: catalog.sequence,
  core_version: catalog.core.version,
  core_bytes: catalog.core.package.size,
  range_status: range.status,
  full_hash_verified: full,
  sha256: packageHash,
})}\n`);

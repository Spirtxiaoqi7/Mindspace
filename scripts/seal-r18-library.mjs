import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  throw new Error("Usage: node scripts/seal-r18-library.mjs <input.docx> <output.bin>");
}

const magic = Buffer.from("MSR18\x01", "binary");
const key = crypto.createHash("sha256")
  .update(Buffer.concat([Buffer.from("Mindspace"), Buffer.from([82, 49, 56, 45, 108, 105, 98]), Buffer.from("local-readonly-v1")]))
  .digest();
const nonce = crypto.randomBytes(16);
const payload = fs.readFileSync(path.resolve(input));
const blocks = [];
for (let counter = 0; Buffer.concat(blocks).length < payload.length; counter += 1) {
  const value = Buffer.alloc(4);
  value.writeUInt32BE(counter);
  blocks.push(crypto.createHmac("sha256", key).update(Buffer.concat([nonce, value])).digest());
}
const stream = Buffer.concat(blocks).subarray(0, payload.length);
const ciphertext = Buffer.alloc(payload.length);
for (let index = 0; index < payload.length; index += 1) ciphertext[index] = payload[index] ^ stream[index];
const body = Buffer.concat([magic, nonce, ciphertext]);
const tag = crypto.createHmac("sha256", key).update(body).digest();
fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true });
fs.writeFileSync(path.resolve(output), Buffer.concat([body, tag]), { mode: 0o600 });
process.stdout.write(`${JSON.stringify({ output: path.resolve(output), bytes: payload.length, sha256: crypto.createHash("sha256").update(payload).digest("hex") })}\n`);

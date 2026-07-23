import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const privatePath = path.resolve(process.argv[2] || "runtime/update-keys/private.pem");
const publicPath = path.resolve(process.argv[3] || "desktop/assets/update-public-key.pem");
if (fs.existsSync(privatePath)) throw new Error(`refusing to overwrite existing private key: ${privatePath}`);
const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
fs.mkdirSync(path.dirname(privatePath), { recursive: true });
fs.mkdirSync(path.dirname(publicPath), { recursive: true });
fs.writeFileSync(privatePath, privateKey.export({ type: "pkcs8", format: "pem" }));
fs.writeFileSync(publicPath, publicKey.export({ type: "spki", format: "pem" }));
process.stdout.write(`${publicPath}\n`);


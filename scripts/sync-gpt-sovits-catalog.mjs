import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "config/gpt-sovits-voices.json");
const generated = path.join(root, "desktop/assets/gpt-sovits-voices.json");
const checkOnly = process.argv.includes("--check");
const sourceBytes = fs.readFileSync(source);
const generatedBytes = fs.existsSync(generated) ? fs.readFileSync(generated) : null;

if (generatedBytes?.equals(sourceBytes)) {
  console.log("GPT-SoVITS catalog mirror is current");
} else if (checkOnly) {
  throw new Error("desktop/assets/gpt-sovits-voices.json is not the generated mirror of config/gpt-sovits-voices.json");
} else {
  fs.mkdirSync(path.dirname(generated), { recursive: true });
  fs.copyFileSync(source, generated);
  console.log("GPT-SoVITS catalog mirror synchronized");
}

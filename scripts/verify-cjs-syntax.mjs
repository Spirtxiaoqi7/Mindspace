import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const files = fs.readdirSync(path.join(root, "desktop"), { withFileTypes: true })
  .filter((entry) => entry.isFile() && entry.name.endsWith(".cjs") && !entry.name.endsWith(".test.cjs"))
  .map((entry) => path.join(root, "desktop", entry.name))
  .sort();
const failures = [];
for (const file of files) {
  const result = spawnSync(process.execPath, ["--check", file], { encoding: "utf8" });
  if (result.status !== 0) failures.push(`${path.relative(root, file)}: ${(result.stderr || result.stdout).trim()}`);
}
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(`Production CJS syntax verified: ${files.length} files`);

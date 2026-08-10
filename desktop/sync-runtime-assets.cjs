const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const pairs = [
  [path.join(projectRoot, "config", "gpt-sovits-voices.json"), path.join(__dirname, "assets", "gpt-sovits-voices.json")],
];
for (const [source, destination] of pairs) {
  if (!fs.existsSync(source)) throw new Error(`Runtime asset source is missing: ${source}`);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
}

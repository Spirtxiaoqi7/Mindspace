const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("packaged GPT-SoVITS catalog is generated from the Core authority", () => {
  const source = fs.readFileSync(path.resolve(__dirname, "..", "config", "gpt-sovits-voices.json"));
  const packaged = fs.readFileSync(path.join(__dirname, "assets", "gpt-sovits-voices.json"));
  assert.deepEqual(packaged, source);
});

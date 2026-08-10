const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("production web and Core release policy reject source maps and internal scripts", () => {
  const root = path.resolve(__dirname, "..");
  const vite = fs.readFileSync(path.join(root, "frontend", "vite.config.ts"), "utf8");
  const allowlist = JSON.parse(fs.readFileSync(path.join(root, "config", "core-release-allowlist.json"), "utf8"));
  const runtimeFiles = allowlist.runtime_files.join("\n");
  assert.match(vite, /mode !== "production"/);
  assert.match(vite, /MINDSPACE_DEV_SOURCEMAP/);
  assert.doesNotMatch(runtimeFiles, /(benchmark|acceptance|real_api|r18|gemma|deepseek|history|report)/i);
  assert.ok(allowlist.targets.includes("config\\service-ports.json"));
});

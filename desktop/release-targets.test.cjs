const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const projectRoot = path.resolve(__dirname, "..");

test("Core builder and updater consume one positive target allowlist", () => {
  const allowlistPath = path.join(projectRoot, "config", "core-release-allowlist.json");
  const allowlist = JSON.parse(fs.readFileSync(allowlistPath, "utf8"));
  const build = fs.readFileSync(path.join(projectRoot, "scripts", "build-update.ps1"), "utf8");
  const apply = fs.readFileSync(path.join(projectRoot, "scripts", "apply-update.ps1"), "utf8");
  assert.equal(allowlist.schema_version, "1.0.0");
  assert.ok(allowlist.targets.length > 0);
  assert.equal(new Set(allowlist.targets).size, allowlist.targets.length);
  assert.match(build, /config\\core-release-allowlist\.json/);
  assert.match(apply, /config\\core-release-allowlist\.json/);
  assert.match(build, /\$Targets\s*=\s*@\(\$Allowlist\.targets/);
  assert.match(apply, /\$AllowedTargets\s*=\s*@\(\$ReleaseAllowlist\.targets/);
});

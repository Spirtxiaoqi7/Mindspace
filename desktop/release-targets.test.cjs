const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const projectRoot = path.resolve(__dirname, "..");

function readPowerShellArray(scriptName, variableName) {
  const source = fs.readFileSync(
    path.join(projectRoot, "scripts", scriptName),
    "utf8",
  );
  const match = source.match(
    new RegExp(`\\$${variableName}\\s*=\\s*@\\((?<body>[\\s\\S]*?)\\r?\\n\\)`),
  );
  assert.ok(match?.groups?.body, `${scriptName} 缺少 $${variableName}`);
  return new Set(
    [...match.groups.body.matchAll(/^\s*'([^']+)'\s*,?\s*$/gm)].map(
      (entry) => entry[1],
    ),
  );
}

test("core package targets stay accepted by the updater", () => {
  const packaged = readPowerShellArray("build-update.ps1", "Targets");
  const accepted = readPowerShellArray("apply-update.ps1", "AllowedTargets");
  assert.deepEqual(
    [...packaged].filter((target) => !accepted.has(target)),
    [],
    "build-update.ps1 新增目标时必须同步 apply-update.ps1 白名单",
  );
});

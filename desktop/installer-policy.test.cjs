const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("application upgrades never remove the private environment or user data", () => {
  const source = fs.readFileSync(path.join(__dirname, "build", "installer.nsh"), "utf8");
  const upgradeGuard = source.indexOf("${ifNot} ${isUpdated}");
  const environmentRemoval = source.indexOf('RMDir /r "$LOCALAPPDATA\\Mindspace\\environment"');
  const dataRemoval = source.indexOf('RMDir /r "$LOCALAPPDATA\\Mindspace\\data"');
  assert.notEqual(upgradeGuard, -1);
  assert.equal(environmentRemoval > upgradeGuard, true);
  assert.equal(dataRemoval > upgradeGuard, true);
  assert.match(source, /environment\.upgrade-preserve/);
  assert.match(source, /!macro customCheckAppRunning/);
  assert.match(source, /taskkill\.exe.*\/F \/T \/IM/);
  assert.match(source, /nsProcess::FindProcess/);
  assert.doesNotMatch(source, /DeleteRegValue HKCU "\$\{UNINSTALL_REGISTRY_KEY\}" "UninstallString"/);
  assert.match(source, /!macro customInstall/);
  assert.match(source, /IfFileExists "\$LOCALAPPDATA\\Mindspace\\environment\\\*" restoreEnvironmentDone/);
  assert.doesNotMatch(source, /RMDir \/r "\$LOCALAPPDATA\\Mindspace\\environment\.upgrade-preserve"/);
  assert.match(source, /Rename "\$LOCALAPPDATA\\Mindspace\\environment\.upgrade-preserve" "\$LOCALAPPDATA\\Mindspace\\environment"/);
});

test("all runtime policy modules are included in the packaged application", () => {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8"));
  assert.equal(config.build.files.includes("service-policy.cjs"), true);
});

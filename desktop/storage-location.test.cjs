const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { appPaths } = require("./app-paths.cjs");
const {
  assertStorageTarget, cleanupMigratedSource, migrateStorage, readHomeLocation,
} = require("./storage-location.cjs");

test("custom storage location persists outside LocalAppData", async (context) => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-storage-"));
  context.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const userData = path.join(fixture, "user-data");
  const source = path.join(fixture, "local", "Mindspace");
  const target = path.join(fixture, "other-drive", "Mindspace");
  const app = { getPath: () => userData };
  fs.mkdirSync(path.join(source, "application", "core"), { recursive: true });
  fs.mkdirSync(path.join(source, "environment", "venvs", "asr-cuda"), { recursive: true });
  fs.mkdirSync(path.join(source, "environment", "state", "components"), { recursive: true });
  fs.mkdirSync(path.join(source, "data"), { recursive: true });
  fs.writeFileSync(path.join(source, "application", "core", "pyproject.toml"), 'version = "0.4.6"');
  fs.writeFileSync(path.join(source, "environment", "venvs", "asr-cuda", "pyvenv.cfg"), `home = ${source}\\environment\\python\n`);
  fs.writeFileSync(path.join(source, "environment", "state", "components", "asr.json"), JSON.stringify({ executable: `${source}\\environment\\venvs\\asr-cuda\\Scripts\\python.exe` }));
  fs.writeFileSync(path.join(source, "data", "launcher.json"), "{}");

  const result = await migrateStorage({ app, sourceHome: source, targetHome: target });
  assert.equal(result.restartRequired, true);
  assert.equal(readHomeLocation(app, {}), target);
  assert.equal(appPaths(app, {}).home, target);
  assert.match(fs.readFileSync(path.join(target, "environment", "venvs", "asr-cuda", "pyvenv.cfg"), "utf8"), new RegExp(target.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"));
  const marker = JSON.parse(fs.readFileSync(path.join(target, "environment", "state", "components", "asr.json"), "utf8"));
  assert.match(marker.executable, new RegExp(`^${target.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "i"));

  const cleanup = await cleanupMigratedSource(appPaths(app, {}));
  assert.equal(cleanup.cleaned, true);
  assert.equal(fs.existsSync(path.join(source, "environment")), false);
  assert.equal(fs.existsSync(path.join(source, "application", "core")), false);
  assert.equal(fs.existsSync(path.join(target, "application", "core", "pyproject.toml")), true);
});

test("storage migration rejects disk roots, nested paths and occupied targets", (context) => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-storage-safe-"));
  context.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const source = path.join(fixture, "source");
  fs.mkdirSync(source, { recursive: true });
  assert.throws(() => assertStorageTarget(source, path.join(source, "nested")), /不能互相包含/);
  const occupied = path.join(fixture, "occupied");
  fs.mkdirSync(occupied, { recursive: true });
  fs.writeFileSync(path.join(occupied, "keep.txt"), "keep");
  assert.throws(() => assertStorageTarget(source, occupied), /必须为空/);
});

test("installer exposes a directory chooser and no longer forces LocalAppData", () => {
  const packageData = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8"));
  const installer = fs.readFileSync(path.join(__dirname, "build", "installer.nsh"), "utf8");
  assert.equal(packageData.build.nsis.allowToChangeInstallationDirectory, true);
  assert.doesNotMatch(installer, /WriteRegExpandStr[^\r\n]+InstallLocation[^\r\n]+LOCALAPPDATA/i);
});

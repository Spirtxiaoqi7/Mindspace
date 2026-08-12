const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { appPaths } = require("./app-paths.cjs");
const {
  assertStorageTarget, cleanupMigratedSource, inspectStorageAlignment,
  installAlignedHome, migrateStorage, readHomeLocation, writeHomeLocation,
} = require("./storage-location.cjs");

test("fresh packaged installs keep large data beside the selected install directory", (context) => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-install-aligned-"));
  context.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const install = path.join(fixture, "Apps", "Mindspace");
  const userData = path.join(fixture, "user-data");
  fs.mkdirSync(install, { recursive: true });
  const app = {
    isPackaged: true,
    getPath: (name) => name === "exe" ? path.join(install, "Mindspace.exe") : userData,
  };
  const expected = install;
  assert.equal(installAlignedHome(app), expected);
  assert.equal(readHomeLocation(app, { LOCALAPPDATA: path.join(fixture, "local") }), expected);
  assert.equal(expected, install);
  assert.equal(inspectStorageAlignment(app, {}, expected).aligned, true);
});

test("stale LocalAppData payload never overrides a writable packaged install", (context) => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-install-legacy-"));
  context.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const local = path.join(fixture, "local");
  const legacy = path.join(local, "Mindspace");
  const install = path.join(fixture, "Apps", "Mindspace");
  const userData = path.join(fixture, "user-data");
  fs.mkdirSync(path.join(legacy, "data"), { recursive: true });
  fs.mkdirSync(install, { recursive: true });
  fs.writeFileSync(path.join(legacy, "data", "launcher.json"), "{}");
  const app = {
    isPackaged: true,
    getPath: (name) => name === "exe" ? path.join(install, "Mindspace.exe") : userData,
  };
  const expected = install;
  assert.equal(readHomeLocation(app, { LOCALAPPDATA: local }), expected);
  const report = inspectStorageAlignment(app, { LOCALAPPDATA: local }, expected);
  assert.equal(report.aligned, true);
  assert.equal(report.migrationRecommended, false);

  const custom = path.join(fixture, "custom", "Mindspace");
  writeHomeLocation(app, custom);
  assert.equal(inspectStorageAlignment(app, { LOCALAPPDATA: local }, custom).userSelected, true);
  assert.equal(inspectStorageAlignment(app, { LOCALAPPDATA: local }, custom).migrationRecommended, false);
});

test("packaged defaults remain the installation directory when legacy data exists", (context) => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-installed-home-"));
  context.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const local = path.join(fixture, "local");
  const legacy = path.join(local, "Mindspace");
  const install = path.join(fixture, "Apps", "Mindspace");
  const userData = path.join(fixture, "user-data");
  fs.mkdirSync(path.join(legacy, "data"), { recursive: true });
  fs.mkdirSync(path.join(install, "application", "core"), { recursive: true });
  fs.writeFileSync(path.join(legacy, "data", "old.json"), "{}");
  fs.writeFileSync(path.join(install, "application", "core", "pyproject.toml"), "[project]\nname='mindspace'\n");
  const app = {
    isPackaged: true,
    getPath: (name) => name === "exe" ? path.join(install, "Mindspace.exe") : userData,
  };

  assert.equal(readHomeLocation(app, { LOCALAPPDATA: local }), install);
  const report = inspectStorageAlignment(app, { LOCALAPPDATA: local }, install);
  assert.equal(report.current, install);
  assert.equal(report.migrationRecommended, false);
});

test("legacy storage is imported once only into an otherwise empty installation Home", (context) => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-legacy-import-"));
  context.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const install = path.join(fixture, "Apps", "Mindspace");
  const sibling = path.join(fixture, "Apps", "MindspaceData");
  const preserved = `${install}.mindspace-preserve`;
  const local = path.join(fixture, "local");
  const localHome = path.join(local, "Mindspace");
  const userData = path.join(fixture, "user-data");
  fs.mkdirSync(path.join(install, "application", "core"), { recursive: true });
  fs.mkdirSync(path.join(install, "data"), { recursive: true });
  fs.mkdirSync(path.join(sibling, "data"), { recursive: true });
  fs.mkdirSync(path.join(preserved, "logs"), { recursive: true });
  fs.mkdirSync(path.join(localHome, "downloads"), { recursive: true });
  fs.writeFileSync(path.join(install, "application", "core", "version.txt"), "new");
  fs.writeFileSync(path.join(sibling, "data", "settings.json"), "old-settings");
  fs.writeFileSync(path.join(sibling, "data", "history.json"), "sibling-history");
  fs.writeFileSync(path.join(preserved, "logs", "main.log"), "preserved-log");
  fs.writeFileSync(path.join(localHome, "downloads", "resume.json"), "local-download");
  const app = {
    isPackaged: true,
    getPath: (name) => name === "exe" ? path.join(install, "Mindspace.exe") : userData,
  };

  const paths = appPaths(app, { LOCALAPPDATA: local });
  require("./app-paths.cjs").ensureAppPaths(paths);
  const marker = JSON.parse(fs.readFileSync(path.join(install, "environment", "state", "legacy-storage-import-v1.json"), "utf8"));
  assert.equal(paths.home, install);
  assert.equal(fs.readFileSync(path.join(install, "data", "settings.json"), "utf8"), "old-settings");
  assert.equal(fs.readFileSync(path.join(install, "data", "history.json"), "utf8"), "sibling-history");
  assert.equal(fs.readFileSync(path.join(install, "downloads", "resume.json"), "utf8"), "local-download");
  assert.equal(fs.readFileSync(path.join(install, "logs", "main.log"), "utf8"), "preserved-log");
  assert.equal(fs.readFileSync(path.join(sibling, "data", "history.json"), "utf8"), "sibling-history");
  assert.equal(marker.conflicts.length, 0);
});

test("legacy storage is reported but never overlaid onto an existing installation Home", (context) => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-legacy-protect-"));
  context.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const install = path.join(fixture, "Apps", "Mindspace");
  const sibling = path.join(fixture, "Apps", "MindspaceData");
  const userData = path.join(fixture, "user-data");
  fs.mkdirSync(path.join(install, "data"), { recursive: true });
  fs.mkdirSync(path.join(sibling, "data"), { recursive: true });
  fs.writeFileSync(path.join(install, "data", "session.json"), "current");
  fs.writeFileSync(path.join(sibling, "data", "session.json"), "old");
  fs.writeFileSync(path.join(sibling, "data", "legacy.json"), "old-only");
  const app = { isPackaged: true, getPath: (name) => name === "exe" ? path.join(install, "Mindspace.exe") : userData };
  const paths = appPaths(app, {});
  require("./app-paths.cjs").ensureAppPaths(paths);
  const marker = JSON.parse(fs.readFileSync(path.join(paths.state, "legacy-storage-import-v1.json"), "utf8"));
  assert.equal(marker.skipped, true);
  assert.equal(marker.reason, "target-not-empty");
  assert.equal(fs.readFileSync(path.join(install, "data", "session.json"), "utf8"), "current");
  assert.equal(fs.existsSync(path.join(install, "data", "legacy.json")), false);
});

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

  fs.mkdirSync(path.join(target, "application", "core"), { recursive: true });
  fs.writeFileSync(path.join(target, "application", "core", "pyproject.toml"), 'version = "0.9.1"');
  const cleanup = await cleanupMigratedSource(appPaths(app, {}));
  assert.equal(cleanup.cleaned, true);
  assert.equal(fs.existsSync(path.join(source, "environment")), false);
  assert.equal(fs.existsSync(path.join(source, "application", "core", "pyproject.toml")), true);
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
  assert.doesNotMatch(installer, /\$LOCALAPPDATA\\Mindspace/i);
});

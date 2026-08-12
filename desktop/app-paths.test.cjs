const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  appPaths, ensureAppPaths, migrateLegacyLayout, mindspaceHome, reconcileLegacyModelPaths,
} = require("./app-paths.cjs");

test("Mindspace uses one LocalAppData application root", (context) => {
  const local = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-local-"));
  context.after(() => fs.rmSync(local, { recursive: true, force: true }));
  const app = { getPath: () => path.join(local, "roaming") };
  assert.equal(mindspaceHome(app, { LOCALAPPDATA: local }), path.join(local, "Mindspace"));
  const paths = ensureAppPaths(appPaths(app, { LOCALAPPDATA: local }));
  assert.equal(paths.environment, path.join(local, "Mindspace", "environment"));
  assert.equal(fs.existsSync(paths.logs), true);
});

test("a saved custom Home is retained instead of importing legacy packaged storage", (context) => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-custom-home-"));
  context.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const install = path.join(fixture, "Apps", "Mindspace");
  const userData = path.join(fixture, "user-data");
  const custom = path.join(fixture, "custom", "Mindspace");
  const sibling = path.join(fixture, "Apps", "MindspaceData");
  fs.mkdirSync(path.join(sibling, "data"), { recursive: true });
  fs.writeFileSync(path.join(sibling, "data", "legacy.json"), "legacy");
  fs.mkdirSync(userData, { recursive: true });
  fs.writeFileSync(path.join(userData, "storage-location.json"), JSON.stringify({ home: custom }));
  const app = {
    isPackaged: true,
    getPath: (name) => name === "exe" ? path.join(install, "Mindspace.exe") : userData,
  };

  const paths = ensureAppPaths(appPaths(app, {}));
  assert.equal(paths.home, custom);
  assert.equal(fs.existsSync(path.join(custom, "data", "legacy.json")), false);
});

test("0.3.4 data and models migrate without copying virtual environments", (context) => {
  const local = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-migration-"));
  context.after(() => fs.rmSync(local, { recursive: true, force: true }));
  const legacy = path.join(local, "legacy");
  fs.mkdirSync(path.join(legacy, "runtime", "config"), { recursive: true });
  fs.mkdirSync(path.join(legacy, "runtime", "data"), { recursive: true });
  fs.mkdirSync(path.join(legacy, "assets", "models", "embedding"), { recursive: true });
  fs.mkdirSync(path.join(legacy, ".venv", "Scripts"), { recursive: true });
  fs.writeFileSync(path.join(legacy, "runtime", "config", "settings.json"), "{}");
  fs.writeFileSync(path.join(legacy, "runtime", "data", "session.json"), "keep");
  fs.writeFileSync(path.join(legacy, "assets", "models", "embedding", "model.bin"), "model");
  fs.writeFileSync(path.join(legacy, ".venv", "Scripts", "python.exe"), "never-copy");
  const paths = ensureAppPaths(appPaths({ getPath: () => local }, { LOCALAPPDATA: local }));
  const report = migrateLegacyLayout({ paths, legacyRoots: [legacy], version: "0.4.0-test" });
  assert.equal(report.migrated.length, 1);
  assert.equal(fs.existsSync(path.join(paths.data, "session.json")), true);
  assert.equal(fs.existsSync(path.join(paths.data, "data")), false);
  assert.equal(fs.existsSync(path.join(paths.home, "config", "settings.json")), true);
  assert.equal(fs.existsSync(path.join(paths.models, "embedding", "model.bin")), true);
  assert.equal(fs.existsSync(path.join(paths.environment, ".venv")), false);
});

test("misrouted ASR final-pass models are adopted without another download", (context) => {
  const local = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-model-path-repair-"));
  context.after(() => fs.rmSync(local, { recursive: true, force: true }));
  const paths = ensureAppPaths(appPaths({ getPath: () => local }, { LOCALAPPDATA: local }));
  const misplaced = path.join(paths.home, "assets", "models", "asr", "Fun-ASR-Nano-2512");
  fs.mkdirSync(misplaced, { recursive: true });
  fs.writeFileSync(path.join(misplaced, "model.pt"), "existing-model");
  const report = reconcileLegacyModelPaths(paths);
  assert.equal(report.moved[0].id, "asr-final");
  assert.equal(fs.existsSync(path.join(paths.models, "asr", "Fun-ASR-Nano-2512", "model.pt")), true);
  assert.equal(fs.existsSync(misplaced), false);
});

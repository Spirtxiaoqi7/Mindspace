const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { appPaths, ensureAppPaths, migrateLegacyLayout, mindspaceHome } = require("./app-paths.cjs");

test("Mindspace uses one LocalAppData application root", (context) => {
  const local = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-local-"));
  context.after(() => fs.rmSync(local, { recursive: true, force: true }));
  const app = { getPath: () => path.join(local, "roaming") };
  assert.equal(mindspaceHome(app, { LOCALAPPDATA: local }), path.join(local, "Mindspace"));
  const paths = ensureAppPaths(appPaths(app, { LOCALAPPDATA: local }));
  assert.equal(paths.environment, path.join(local, "Mindspace", "environment"));
  assert.equal(fs.existsSync(paths.logs), true);
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
  assert.equal(fs.existsSync(path.join(paths.data, "data", "session.json")), true);
  assert.equal(fs.existsSync(path.join(paths.models, "embedding", "model.bin")), true);
  assert.equal(fs.existsSync(path.join(paths.environment, ".venv")), false);
});

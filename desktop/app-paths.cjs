const fs = require("node:fs");
const path = require("node:path");
const { readHomeLocation } = require("./storage-location.cjs");

function mindspaceHome(app, environment = process.env) {
  return readHomeLocation(app, environment);
}

function appPaths(app, environment = process.env) {
  const home = mindspaceHome(app, environment);
  return {
    home,
    application: path.join(home, "application"),
    core: path.join(home, "application", "core"),
    environment: path.join(home, "environment"),
    tools: path.join(home, "environment", "tools"),
    python: path.join(home, "environment", "python"),
    venvs: path.join(home, "environment", "venvs"),
    cache: path.join(home, "environment", "cache"),
    state: path.join(home, "environment", "state"),
    models: path.join(home, "models"),
    data: path.join(home, "data"),
    downloads: path.join(home, "downloads"),
    logs: path.join(home, "logs"),
    backups: path.join(home, "backups"),
  };
}

function ensureAppPaths(paths) {
  for (const key of [
    "home", "application", "environment", "tools", "python", "venvs", "cache",
    "state", "models", "data", "downloads", "logs", "backups",
  ]) fs.mkdirSync(paths[key], { recursive: true });
  return paths;
}

function copyMissing(source, destination) {
  if (!source || !fs.existsSync(source)) return false;
  fs.mkdirSync(destination, { recursive: true });
  fs.cpSync(source, destination, {
    recursive: true,
    force: false,
    errorOnExist: false,
  });
  return true;
}

function directoryHasEntries(directory) {
  try { return fs.readdirSync(directory).length > 0; } catch { return false; }
}

function migrateLegacyLayout({ paths, legacyRoots = [], version = "0.4.0" }) {
  ensureAppPaths(paths);
  const marker = path.join(paths.state, `migration-${version}.json`);
  if (fs.existsSync(marker)) return JSON.parse(fs.readFileSync(marker, "utf8"));

  const sources = [...new Set(legacyRoots.filter(Boolean).map((root) => path.resolve(root)))]
    .filter((root) => root !== paths.core && fs.existsSync(root));
  const backup = path.join(paths.backups, `before-${version}-${Date.now()}`);
  let backedUp = false;
  if (directoryHasEntries(paths.data)) {
    fs.mkdirSync(backup, { recursive: true });
    fs.cpSync(paths.data, path.join(backup, "data"), { recursive: true, force: true });
    backedUp = true;
  }

  const migrated = [];
  for (const root of sources) {
    const runtime = path.join(root, "runtime");
    let copiedData = false;
    for (const folder of ["config", "data"]) {
      copiedData = copyMissing(path.join(runtime, folder), path.join(paths.data, folder)) || copiedData;
    }
    const copiedModels = copyMissing(path.join(root, "assets", "models"), paths.models);
    if (copiedData || copiedModels) migrated.push({ root, data: copiedData, models: copiedModels });
  }
  const report = {
    version,
    completed_at: new Date().toISOString(),
    migrated,
    backup: backedUp ? backup : "",
    ignored_environments: [".venv", ".venv-asr", ".venv-tts"],
  };
  fs.writeFileSync(marker, `${JSON.stringify(report, null, 2)}\n`);
  return report;
}

module.exports = { appPaths, copyMissing, ensureAppPaths, migrateLegacyLayout, mindspaceHome };

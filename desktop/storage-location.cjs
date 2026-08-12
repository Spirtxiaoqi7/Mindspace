const fs = require("node:fs");
const path = require("node:path");

const MOVABLE_PATHS = [
  ["environment"],
  ["models"],
  ["data"],
  ["downloads"],
  ["logs"],
  ["backups"],
];

// Only these roots are user-owned and may ever cross an installation boundary.
// The Core itself is immutable application code and is always replaced through
// the signed/bootstrap package path, never copied from an old installation.
const LEGACY_LAYOUT_PATHS = [
  ["runtime", "data"],
  ["runtime", "config"],
  ["assets", "models"],
];

function locationFile(app) {
  return path.join(app.getPath("userData"), "storage-location.json");
}

function legacyLocalHome(app, environment = process.env) {
  const local = environment.LOCALAPPDATA || app.getPath("userData");
  return path.resolve(local, "Mindspace");
}

function installationDirectory(app) {
  if (!app?.isPackaged) return "";
  let executable = "";
  try { executable = app.getPath("exe"); } catch {}
  if (!executable || !path.isAbsolute(executable)) return "";
  return path.dirname(path.resolve(executable));
}

function legacySiblingHome(app) {
  const installDirectory = installationDirectory(app);
  if (!installDirectory) return "";
  return path.resolve(path.dirname(installDirectory), "MindspaceData");
}

function preservedInstallHome(app) {
  const installDirectory = installationDirectory(app);
  return installDirectory ? `${installDirectory}.mindspace-preserve` : "";
}

function installAlignedHome(app) {
  // A portable Mindspace installation owns its complete Home. Keeping this
  // derived exclusively from the executable makes upgrades and reinstalls
  // resolve to the same directory on every drive.
  return installationDirectory(app);
}

function packagedInstallHome(app) {
  return installationDirectory(app);
}

function homeHasUserPayload(home) {
  const roots = [
    path.join(home, "application", "core"),
    path.join(home, "environment"),
    path.join(home, "models"),
    path.join(home, "data"),
    path.join(home, "downloads"),
    path.join(home, "logs"),
    path.join(home, "backups"),
  ];
  const stack = roots.filter((item) => fs.existsSync(item));
  let inspected = 0;
  while (stack.length && inspected < 512) {
    const current = stack.pop();
    let entries = [];
    try { entries = fs.readdirSync(current, { withFileTypes: true }); } catch { continue; }
    for (const entry of entries) {
      inspected += 1;
      if (entry.isFile()) return true;
      if (entry.isDirectory()) stack.push(path.join(current, entry.name));
      if (inspected >= 512) break;
    }
  }
  return false;
}

function defaultHome(app, environment = process.env) {
  // Packaged installations always keep both application files and mutable
  // user content below the selected installation directory. Legacy locations
  // are imported later without changing this authoritative destination.
  const packaged = packagedInstallHome(app);
  if (packaged) return packaged;
  return legacyLocalHome(app, environment);
}

function treeHasFiles(root, { skip = () => false } = {}) {
  if (!fs.existsSync(root)) return false;
  const pending = [root];
  let inspected = 0;
  while (pending.length && inspected < 1024) {
    const current = pending.pop();
    let entries = [];
    try { entries = fs.readdirSync(current, { withFileTypes: true }); } catch { continue; }
    for (const entry of entries) {
      inspected += 1;
      const target = path.join(current, entry.name);
      if (skip(target, entry)) continue;
      if (entry.isFile()) return true;
      if (entry.isDirectory()) pending.push(target);
      if (inspected >= 1024) break;
    }
  }
  return false;
}

function homeHasMutablePayload(home) {
  for (const parts of MOVABLE_PATHS) {
    const root = path.join(home, ...parts);
    if (parts[0] === "environment") {
      if (treeHasFiles(root, { skip: (target) => path.relative(root, target).split(path.sep)[0] === "state" })) return true;
    } else if (treeHasFiles(root)) {
      return true;
    }
  }
  return false;
}

function legacyHomeHasImportablePayload(home) {
  if (homeHasMutablePayload(home)) return true;
  return LEGACY_LAYOUT_PATHS.some((parts) => treeHasFiles(path.join(home, ...parts)));
}

function readHomeLocation(app, environment = process.env) {
  if (environment.MINDSPACE_HOME) return path.resolve(environment.MINDSPACE_HOME);
  try {
    const stored = JSON.parse(fs.readFileSync(locationFile(app), "utf8"));
    if (stored?.home && path.isAbsolute(stored.home)) return path.resolve(stored.home);
  } catch {}
  return defaultHome(app, environment);
}

function writeHomeLocation(app, home) {
  const target = locationFile(app);
  const temporary = `${target}.${process.pid}.tmp`;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(temporary, `${JSON.stringify({
    schema_version: "1.0.0",
    home: path.resolve(home),
    updated_at: new Date().toISOString(),
  }, null, 2)}\n`);
  fs.renameSync(temporary, target);
  return target;
}

function storedHomeLocation(app) {
  try {
    const stored = JSON.parse(fs.readFileSync(locationFile(app), "utf8"));
    return stored?.home && path.isAbsolute(stored.home) ? path.resolve(stored.home) : "";
  } catch {
    return "";
  }
}

function legacyStorageHomes(app, environment = process.env, destination = "") {
  const installHome = packagedInstallHome(app);
  const stored = storedHomeLocation(app);
  const target = path.resolve(destination || installHome || ".");
  if (!installHome || environment.MINDSPACE_HOME || stored || target.toLowerCase() !== installHome.toLowerCase()) {
    return [];
  }
  const seen = new Set();
  let legacyWorkspace = "";
  try { legacyWorkspace = path.join(app.getPath("userData"), "app"); } catch {}
  return [legacySiblingHome(app), preservedInstallHome(app), legacyLocalHome(app, environment), legacyWorkspace]
    .filter(Boolean)
    .map((item) => path.resolve(item))
    .filter((item) => item.toLowerCase() !== target.toLowerCase())
    .filter((item) => {
      const normalized = item.toLowerCase();
      if (seen.has(normalized) || !legacyHomeHasImportablePayload(item)) return false;
      seen.add(normalized);
      return true;
    });
}

function inspectStorageAlignment(app, environment = process.env, currentHome = readHomeLocation(app, environment)) {
  const current = path.resolve(currentHome);
  const recommended = installAlignedHome(app);
  const explicitEnvironment = Boolean(environment.MINDSPACE_HOME);
  const stored = Boolean(storedHomeLocation(app));
  const aligned = Boolean(recommended && current.toLowerCase() === recommended.toLowerCase());
  const userSelected = explicitEnvironment || stored;
  return {
    mode: explicitEnvironment ? "environment" : stored ? "user-selected" : aligned ? "install-aligned" : "legacy-localappdata",
    current,
    recommended,
    aligned,
    userSelected,
    migrationRecommended: Boolean(recommended && !aligned && !userSelected),
    message: aligned
      ? "大型模型、私有环境与用户数据已跟随安装盘"
      : userSelected
        ? "正在使用用户指定的统一存储目录"
        : recommended
        ? `检测到旧版存储目录；可安全迁移到 ${recommended}`
          : "正在使用本机用户数据目录",
  };
}

function assertStorageTarget(sourceHome, targetHome) {
  const source = path.resolve(sourceHome);
  const target = path.resolve(targetHome);
  const sourcePrefix = `${source}${path.sep}`.toLowerCase();
  const targetPrefix = `${target}${path.sep}`.toLowerCase();
  if (source.toLowerCase() === target.toLowerCase()) throw new Error("新存储位置与当前位置相同");
  if (targetPrefix.startsWith(sourcePrefix) || sourcePrefix.startsWith(targetPrefix)) {
    throw new Error("新旧存储目录不能互相包含");
  }
  if (path.parse(target).root.toLowerCase() === target.toLowerCase()) {
    throw new Error("不能直接使用磁盘根目录，请选择或创建 Mindspace 文件夹");
  }
  fs.mkdirSync(target, { recursive: true });
  const entries = fs.readdirSync(target).filter((entry) => !entry.startsWith(".mindspace-migrating-"));
  if (entries.length) throw new Error("目标 Mindspace 文件夹必须为空，防止覆盖现有文件");
  const probe = path.join(target, `.write-${process.pid}`);
  fs.writeFileSync(probe, "ok");
  fs.rmSync(probe, { force: true });
  fs.rmdirSync(target);
  return { source, target };
}

function replacePrefix(value, source, target) {
  if (typeof value === "string" && value.toLowerCase().startsWith(source.toLowerCase())) {
    return `${target}${value.slice(source.length)}`;
  }
  if (Array.isArray(value)) return value.map((item) => replacePrefix(item, source, target));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, replacePrefix(item, source, target)]));
  }
  return value;
}

async function walk(root, visit) {
  if (!fs.existsSync(root)) return;
  const entries = await fs.promises.readdir(root, { withFileTypes: true });
  for (const entry of entries) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) await walk(target, visit);
    else if (entry.isFile()) await visit(target);
  }
}

async function rewriteMovedPaths(root, source, target) {
  const extensions = new Set([".json", ".cfg", ".pth", ".txt", ".ps1", ".bat", ".cmd"]);
  const candidates = [path.join(root, "environment"), path.join(root, "data")];
  for (const candidate of candidates) {
    await walk(candidate, async (file) => {
      if (!extensions.has(path.extname(file).toLowerCase())) return;
      const stat = await fs.promises.stat(file);
      if (stat.size > 8 * 1024 * 1024) return;
      let content;
      try { content = await fs.promises.readFile(file, "utf8"); } catch { return; }
      let next = content;
      if (path.extname(file).toLowerCase() === ".json") {
        try {
          const parsed = JSON.parse(content);
          next = `${JSON.stringify(replacePrefix(parsed, source, target), null, 2)}\n`;
        }
        catch { next = content.split(source).join(target); }
      } else {
        if (!content.toLowerCase().includes(source.toLowerCase())) return;
        next = content.split(source).join(target);
      }
      if (next !== content) await fs.promises.writeFile(file, next, "utf8");
    });
  }
}

async function migrateStorage({ app, sourceHome, targetHome, onProgress = () => {} }) {
  const { source, target } = assertStorageTarget(sourceHome, targetHome);
  const staging = path.join(path.dirname(target), `.mindspace-migrating-${path.basename(target)}-${Date.now()}`);
  let promoted = false;
  await fs.promises.rm(staging, { recursive: true, force: true });
  await fs.promises.mkdir(staging, { recursive: true });
  try {
    for (let index = 0; index < MOVABLE_PATHS.length; index += 1) {
      const parts = MOVABLE_PATHS[index];
      const from = path.join(source, ...parts);
      const to = path.join(staging, ...parts);
      onProgress(Math.round(index / MOVABLE_PATHS.length * 80), parts.join("\\"));
      if (fs.existsSync(from)) {
        await fs.promises.mkdir(path.dirname(to), { recursive: true });
        await fs.promises.cp(from, to, { recursive: true, force: false, errorOnExist: true });
      }
    }
    onProgress(84, "正在改写私有环境路径");
    await rewriteMovedPaths(staging, source, target);
    await fs.promises.rename(staging, target);
    promoted = true;
    const marker = path.join(target, "environment", "state", "storage-migration.json");
    await fs.promises.mkdir(path.dirname(marker), { recursive: true });
    await fs.promises.writeFile(marker, `${JSON.stringify({
      schema_version: "1.0.0", source, target, cleanup_pending: true,
      migrated_at: new Date().toISOString(),
    }, null, 2)}\n`);
    writeHomeLocation(app, target);
    onProgress(100, "迁移完成，正在重启验证");
    return { ok: true, source, target, restartRequired: true };
  } catch (error) {
    await fs.promises.rm(staging, { recursive: true, force: true });
    if (promoted) await fs.promises.rm(target, { recursive: true, force: true });
    throw error;
  }
}

function copyLegacyTree(source, target, report) {
  if (!fs.existsSync(source)) return;
  const pending = [[source, target]];
  while (pending.length) {
    const [from, to] = pending.pop();
    let entries;
    try { entries = fs.readdirSync(from, { withFileTypes: true }); } catch (error) {
      report.errors.push({ source: from, error: String(error.message || error) });
      continue;
    }
    for (const entry of entries) {
      const fromEntry = path.join(from, entry.name);
      const toEntry = path.join(to, entry.name);
      if (entry.isDirectory()) {
        if (fs.existsSync(toEntry) && !fs.statSync(toEntry).isDirectory()) {
          report.conflicts.push({ source: fromEntry, target: toEntry, reason: "target-is-file" });
        } else {
          fs.mkdirSync(toEntry, { recursive: true });
          pending.push([fromEntry, toEntry]);
        }
        continue;
      }
      if (!entry.isFile()) {
        report.skipped.push({ source: fromEntry, reason: "unsupported-entry" });
        continue;
      }
      if (fs.existsSync(toEntry)) {
        report.conflicts.push({ source: fromEntry, target: toEntry, reason: "target-exists" });
        continue;
      }
      try {
        fs.mkdirSync(path.dirname(toEntry), { recursive: true });
        fs.copyFileSync(fromEntry, toEntry, fs.constants.COPYFILE_EXCL);
        report.copied += 1;
      } catch (error) {
        report.errors.push({ source: fromEntry, target: toEntry, error: String(error.message || error) });
      }
    }
  }
}

function migrateLegacyStorage(paths) {
  const marker = path.join(paths.state, "legacy-storage-import-v1.json");
  if (fs.existsSync(marker)) return JSON.parse(fs.readFileSync(marker, "utf8"));
  const report = {
    schema_version: "1.0.0",
    completed_at: new Date().toISOString(),
    sources: [],
    copied: 0,
    conflicts: [],
    skipped: [],
    errors: [],
  };
  // The first-launch migration is intentionally all-or-nothing. An existing
  // installation may contain a newer session, model marker or private venv;
  // merging old AppData into it makes the authoritative data root ambiguous.
  if (homeHasMutablePayload(paths.home)) {
    report.skipped = true;
    report.reason = "target-not-empty";
    report.sources = [...(paths.legacyStorageHomes || [])].map((source) => path.resolve(source));
    fs.writeFileSync(marker, `${JSON.stringify(report, null, 2)}\n`);
    return report;
  }
  for (const sourceHome of paths.legacyStorageHomes || []) {
    const source = path.resolve(sourceHome);
    if (!fs.existsSync(source)) continue;
    report.sources.push(source);
    for (const parts of MOVABLE_PATHS) {
      const from = path.join(source, ...parts);
      if (fs.existsSync(from)) copyLegacyTree(from, path.join(paths.home, ...parts), report);
    }
    const legacyData = path.join(source, "runtime", "data");
    if (fs.existsSync(legacyData)) copyLegacyTree(legacyData, paths.data, report);
    const legacyConfig = path.join(source, "runtime", "config");
    if (fs.existsSync(legacyConfig)) copyLegacyTree(legacyConfig, path.join(paths.data, "config"), report);
    const legacyModels = path.join(source, "assets", "models");
    if (fs.existsSync(legacyModels)) copyLegacyTree(legacyModels, paths.models, report);
  }
  fs.writeFileSync(marker, `${JSON.stringify(report, null, 2)}\n`);
  return report;
}

async function cleanupMigratedSource(paths) {
  const marker = path.join(paths.state, "storage-migration.json");
  let record;
  try { record = JSON.parse(await fs.promises.readFile(marker, "utf8")); } catch { return { cleaned: false }; }
  if (!record.cleanup_pending || path.resolve(record.target).toLowerCase() !== path.resolve(paths.home).toLowerCase()) {
    return { cleaned: false };
  }
  if (!fs.existsSync(path.join(paths.core, "pyproject.toml"))) return { cleaned: false };
  const source = path.resolve(record.source);
  for (const parts of MOVABLE_PATHS) {
    const oldPath = path.join(source, ...parts);
    if (oldPath.toLowerCase() === paths.core.toLowerCase()) continue;
    try { await fs.promises.rm(oldPath, { recursive: true, force: true }); } catch {}
  }
  record.cleanup_pending = false;
  record.cleaned_at = new Date().toISOString();
  await fs.promises.writeFile(marker, `${JSON.stringify(record, null, 2)}\n`);
  return { cleaned: true, source };
}

module.exports = {
  MOVABLE_PATHS, assertStorageTarget, cleanupMigratedSource, defaultHome,
  homeHasMutablePayload, homeHasUserPayload, inspectStorageAlignment, installAlignedHome, installationDirectory, legacyLocalHome,
  legacySiblingHome, legacyStorageHomes, locationFile, migrateLegacyStorage, migrateStorage,
  packagedInstallHome, readHomeLocation, replacePrefix, rewriteMovedPaths, storedHomeLocation,
  preservedInstallHome, writeHomeLocation,
};

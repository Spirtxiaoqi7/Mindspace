const fs = require("node:fs");
const path = require("node:path");

const MOVABLE_PATHS = [
  ["application", "core"],
  ["environment"],
  ["models"],
  ["data"],
  ["downloads"],
  ["logs"],
  ["backups"],
];

function locationFile(app) {
  return path.join(app.getPath("userData"), "storage-location.json");
}

function defaultHome(app, environment = process.env) {
  const local = environment.LOCALAPPDATA || app.getPath("userData");
  return path.resolve(local, "Mindspace");
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
    const core = path.join(staging, "application", "core", "pyproject.toml");
    if (fs.existsSync(path.join(source, "application", "core", "pyproject.toml")) && !fs.existsSync(core)) {
      throw new Error("核心程序迁移校验失败");
    }
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
  locationFile, migrateStorage, readHomeLocation, replacePrefix, rewriteMovedPaths,
  writeHomeLocation,
};

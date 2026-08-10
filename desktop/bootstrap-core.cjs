const fs = require("node:fs");
const path = require("node:path");
const extractZip = require("extract-zip");

function isCoreRoot(root) {
  if (!root) return false;
  return fs.existsSync(path.join(root, "pyproject.toml"))
    && fs.existsSync(path.join(root, "scripts", "start.ps1"));
}

function defaultUserRoot(app) {
  return path.join(app.getPath("userData"), "app");
}

function compareVersions(left, right) {
  const normalize = (value) => String(value || "0").split(/[.+-]/).map((part) => Number(part) || 0);
  const a = normalize(left);
  const b = normalize(right);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    if ((a[index] || 0) !== (b[index] || 0)) return (a[index] || 0) > (b[index] || 0) ? 1 : -1;
  }
  return 0;
}

function installedVersion(root) {
  try {
    return String(JSON.parse(fs.readFileSync(path.join(root, "payload.json"), "utf8")).version || "");
  } catch {
    return "";
  }
}

const PROTECTED_CORE_PATHS = Object.freeze([
  ["data"],
  ["models"],
  ["runtime", "config"],
  ["runtime", "data"],
  ["assets", "models"],
]);

function protectedStatePaths(root) {
  return PROTECTED_CORE_PATHS
    .map((segments) => path.join(root, ...segments))
    .filter((candidate) => fs.existsSync(candidate));
}

function assertReplaceableCore(root) {
  const resolved = path.resolve(root);
  const parsed = path.parse(resolved);
  if (resolved === parsed.root || resolved === path.dirname(resolved)) throw new Error(`Refusing unsafe Core root: ${resolved}`);
  const protectedPaths = protectedStatePaths(resolved);
  if (protectedPaths.length) {
    throw new Error(`Core contains user state that must be migrated before upgrade: ${protectedPaths.join(", ")}`);
  }
}

function assertSafeExtractedTree(root) {
  const pending = [root];
  while (pending.length) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const target = path.join(current, entry.name);
      const attributes = fs.lstatSync(target);
      if (attributes.isSymbolicLink()) throw new Error(`Core archive contains a symbolic link: ${target}`);
      if (entry.isDirectory()) pending.push(target);
    }
  }
}

function resolveWorkspaceRoot({ app, configuredRoot = "", environmentRoot = "", hintedRoot = "", dirname }) {
  if (environmentRoot) return path.resolve(environmentRoot);
  if (configuredRoot) return path.resolve(configuredRoot);
  if (!app.isPackaged) {
    const candidates = [hintedRoot, path.resolve(dirname, "..")].filter(Boolean);
    const developmentRoot = candidates.find(isCoreRoot);
    if (developmentRoot) return developmentRoot;
  }
  return defaultUserRoot(app);
}

function bundledArchive(resourcesPath, dirname) {
  const candidates = [
    path.join(resourcesPath || "", "bootstrap", "mindspace-core.zip"),
    path.join(dirname, "bootstrap", "mindspace-core.zip"),
  ];
  return candidates.find((candidate) => candidate && fs.existsSync(candidate)) || candidates[0];
}

function bundledVersion(resourcesPath, dirname) {
  const candidates = [
    path.join(resourcesPath || "", "bootstrap", "manifest.json"),
    path.join(dirname, "bootstrap", "manifest.json"),
  ];
  for (const candidate of candidates) {
    try {
      return String(JSON.parse(fs.readFileSync(candidate, "utf8")).version || "");
    } catch {}
  }
  return "";
}

async function extractArchive(archive, destination) {
  await extractZip(archive, { dir: path.resolve(destination) });
}

const BOOTSTRAP_RESIDUE_PREFIXES = Object.freeze([
  ".mindspace-bootstrap-",
  ".mindspace-core-backup-",
  ".mindspace-core-rejected-",
]);

function bootstrapResidues(parent) {
  if (!fs.existsSync(parent)) return [];
  return fs.readdirSync(parent)
    .filter((name) => BOOTSTRAP_RESIDUE_PREFIXES.some((prefix) => name.startsWith(prefix)))
    .map((name) => path.join(parent, name));
}

function cleanupRequired(target, label, remove) {
  if (!fs.existsSync(target)) return;
  try {
    remove(target);
  } catch (error) {
    const blocked = new Error(`${label}失败，Core 启动已阻塞：${target}：${String(error.message || error)}`);
    blocked.code = "CORE_BOOTSTRAP_CLEANUP_FAILED";
    blocked.cleanupTarget = target;
    throw blocked;
  }
  if (fs.existsSync(target)) {
    const blocked = new Error(`${label}失败，目标仍然存在，Core 启动已阻塞：${target}`);
    blocked.code = "CORE_BOOTSTRAP_CLEANUP_FAILED";
    blocked.cleanupTarget = target;
    throw blocked;
  }
}

function cleanupBootstrapResidues(parent, remove) {
  for (const residue of bootstrapResidues(parent)) cleanupRequired(residue, "清理历史 Core 切换残留", remove);
}

async function ensureCoreRoot({
  root,
  archive,
  version = "",
  extract = extractArchive,
  validate = isCoreRoot,
  remove = (target) => fs.rmSync(target, { recursive: true, force: true }),
}) {
  const existed = isCoreRoot(root);
  const currentVersion = installedVersion(root);
  const parent = path.dirname(root);
  const existingResidues = bootstrapResidues(parent);
  if (existed) cleanupBootstrapResidues(parent, remove);
  else if (existingResidues.length) {
    const blocked = new Error(`Core root 无效且存在待恢复的 staging/backup，拒绝自动删除，启动已阻塞：${existingResidues.join(", ")}`);
    blocked.code = "CORE_BOOTSTRAP_RECOVERY_REQUIRED";
    throw blocked;
  }
  if (existed && (!version || compareVersions(currentVersion, version) >= 0)) {
    return { root, created: false, upgraded: false, message: "基础核心已是最新版本" };
  }
  if (!archive || !fs.existsSync(archive)) throw new Error(`安装器缺少基础核心包：${archive}`);

  assertReplaceableCore(root);
  fs.mkdirSync(parent, { recursive: true });
  const staging = path.join(parent, `.mindspace-bootstrap-${process.pid}-${Date.now()}`);
  const backup = path.join(parent, `.mindspace-core-backup-${process.pid}-${Date.now()}`);
  fs.mkdirSync(staging, { recursive: true });
  let oldMoved = false;
  let newMoved = false;
  let committed = false;
  try {
    await extract(archive, staging);
    const payload = isCoreRoot(path.join(staging, "payload"))
      ? path.join(staging, "payload")
      : staging;
    if (!isCoreRoot(payload)) throw new Error("基础核心包结构无效：缺少 pyproject.toml 或 start.ps1");
    assertSafeExtractedTree(payload);
    fs.writeFileSync(
      path.join(payload, ".mindspace-bootstrap.json"),
      `${JSON.stringify({ installed_at: new Date().toISOString(), source: path.basename(archive) }, null, 2)}\n`,
    );
    if (fs.existsSync(root)) {
      fs.renameSync(root, backup);
      oldMoved = true;
    }
    fs.renameSync(payload, root);
    newMoved = true;
    if (!validate(root)) throw new Error("基础核心原子切换后校验失败");
    committed = true;
    cleanupRequired(staging, "清理 Core staging", remove);
    if (oldMoved) cleanupRequired(backup, "清理旧 Core 备份", remove);
    const residues = bootstrapResidues(parent);
    if (residues.length) {
      const blocked = new Error(`Core 切换后仍存在 staging/backup 残留，启动已阻塞：${residues.join(", ")}`);
      blocked.code = "CORE_BOOTSTRAP_CLEANUP_FAILED";
      throw blocked;
    }
    return {
      root,
      created: !existed,
      upgraded: existed,
      backup_cleaned: oldMoved,
      message: existed ? `基础核心已升级到 ${version || "安装包版本"}` : "基础核心已安装到用户工作区",
    };
  } catch (error) {
    if (committed) throw error;
    const rejected = path.join(parent, `.mindspace-core-rejected-${process.pid}-${Date.now()}`);
    try {
      if (newMoved && fs.existsSync(root)) fs.renameSync(root, rejected);
      if (oldMoved && fs.existsSync(backup)) fs.renameSync(backup, root);
      cleanupRequired(rejected, "清理未通过校验的新 Core", remove);
      cleanupRequired(staging, "清理失败切换的 staging", remove);
    } catch (rollbackError) {
      const blocked = new Error(`Core 切换失败且完整回滚未完成，启动已阻塞：${String(rollbackError.message || rollbackError)}`);
      blocked.code = "CORE_BOOTSTRAP_ROLLBACK_FAILED";
      blocked.cause = error;
      throw blocked;
    }
    throw error;
  }
}

module.exports = {
  bundledArchive,
  bundledVersion,
  bootstrapResidues,
  compareVersions,
  assertReplaceableCore,
  assertSafeExtractedTree,
  defaultUserRoot,
  ensureCoreRoot,
  extractArchive,
  isCoreRoot,
  installedVersion,
  protectedStatePaths,
  resolveWorkspaceRoot,
};

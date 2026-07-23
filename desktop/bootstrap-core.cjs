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

async function ensureCoreRoot({ root, archive, version = "", extract = extractArchive }) {
  const existed = isCoreRoot(root);
  const currentVersion = installedVersion(root);
  if (existed && (!version || compareVersions(currentVersion, version) >= 0)) {
    return { root, created: false, upgraded: false, message: "基础核心已是最新版本" };
  }
  if (!archive || !fs.existsSync(archive)) throw new Error(`安装器缺少基础核心包：${archive}`);

  const parent = path.dirname(root);
  fs.mkdirSync(parent, { recursive: true });
  const staging = path.join(parent, `.mindspace-bootstrap-${process.pid}-${Date.now()}`);
  fs.mkdirSync(staging, { recursive: true });
  try {
    await extract(archive, staging);
    const payload = isCoreRoot(path.join(staging, "payload"))
      ? path.join(staging, "payload")
      : staging;
    if (!isCoreRoot(payload)) throw new Error("基础核心包结构无效：缺少 pyproject.toml 或 start.ps1");
    fs.mkdirSync(root, { recursive: true });
    fs.cpSync(payload, root, { recursive: true, force: true });
    if (!isCoreRoot(root)) throw new Error("基础核心写入后校验失败");
    const markerRoot = path.join(root, "runtime");
    fs.mkdirSync(markerRoot, { recursive: true });
    fs.writeFileSync(
      path.join(markerRoot, "bootstrap.json"),
      `${JSON.stringify({ installed_at: new Date().toISOString(), source: path.basename(archive) }, null, 2)}\n`,
    );
    return {
      root,
      created: !existed,
      upgraded: existed,
      message: existed ? `基础核心已升级到 ${version || "安装包版本"}` : "基础核心已安装到用户工作区",
    };
  } finally {
    fs.rmSync(staging, { recursive: true, force: true });
  }
}

module.exports = {
  bundledArchive,
  bundledVersion,
  compareVersions,
  defaultUserRoot,
  ensureCoreRoot,
  extractArchive,
  isCoreRoot,
  installedVersion,
  resolveWorkspaceRoot,
};

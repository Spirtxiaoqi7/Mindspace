const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const OFFICIAL_CATALOG_URL = "https://douyinqijun.cn/downloads/mindspace/catalog/stable/windows-x64.json";

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function semverParts(value) {
  const match = String(value || "").match(/^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?/);
  if (!match) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3]), match[4] || ""];
}

function compareVersions(left, right) {
  const a = semverParts(left);
  const b = semverParts(right);
  if (!a || !b) throw new Error(`invalid semantic version: ${left} or ${right}`);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] > b[index] ? 1 : -1;
  }
  if (a[3] === b[3]) return 0;
  if (!a[3]) return 1;
  if (!b[3]) return -1;
  return a[3].localeCompare(b[3], "en", { numeric: true });
}

function readProjectVersion(root, fallback) {
  try {
    const content = fs.readFileSync(path.join(root, "pyproject.toml"), "utf8");
    return content.match(/^version\s*=\s*"([^"]+)"/m)?.[1] || fallback;
  } catch {
    return fallback;
  }
}

function verifyManifest(manifest, publicKey, expectedChannel) {
  if (!manifest || manifest.schema_version !== "1.0.0") throw new Error("不支持的更新清单版本");
  if (manifest.channel !== expectedChannel) throw new Error(`更新通道不匹配：${manifest.channel}`);
  if (!semverParts(manifest.version)) throw new Error("更新版本号无效");
  if (!manifest.package || manifest.package.format !== "zip") throw new Error("更新包格式无效");
  if (!/^[a-f0-9]{64}$/i.test(manifest.package.sha256 || "")) throw new Error("更新包 SHA-256 无效");
  if (!Number.isSafeInteger(manifest.package.size) || manifest.package.size <= 0) throw new Error("更新包大小无效");
  if (manifest.signature?.algorithm !== "ed25519" || !manifest.signature.value) throw new Error("更新清单未签名");
  const unsigned = { ...manifest };
  delete unsigned.signature;
  const valid = crypto.verify(
    null,
    Buffer.from(canonical(unsigned)),
    publicKey,
    Buffer.from(manifest.signature.value, "base64"),
  );
  if (!valid) throw new Error("更新清单签名验证失败");
  return manifest;
}

function verifyCatalog(catalog, publicKey, expectedChannel) {
  if (!catalog || catalog.schema_version !== "2.0.0") throw new Error("不支持的更新目录版本");
  if (catalog.channel !== expectedChannel) throw new Error(`更新通道不匹配：${catalog.channel}`);
  if (!catalog.release_id || !Number.isSafeInteger(catalog.sequence) || catalog.sequence < 1) throw new Error("更新目录缺少有效发布序号");
  if (catalog.signature?.algorithm !== "ed25519" || !catalog.signature.value) throw new Error("更新目录未签名");
  const unsigned = { ...catalog };
  delete unsigned.signature;
  const valid = crypto.verify(
    null,
    Buffer.from(canonical(unsigned)),
    publicKey,
    Buffer.from(catalog.signature.value, "base64"),
  );
  if (!valid) throw new Error("更新目录签名验证失败");
  if (catalog.core) {
    if (!semverParts(catalog.core.version)) throw new Error("Core 版本号无效");
    if (!catalog.core.package || catalog.core.package.format !== "zip") throw new Error("Core 更新包格式无效");
    if (!/^[a-f0-9]{64}$/i.test(catalog.core.package.sha256 || "")) throw new Error("Core 更新包 SHA-256 无效");
    if (!Number.isSafeInteger(catalog.core.package.size) || catalog.core.package.size <= 0) throw new Error("Core 更新包大小无效");
  }
  if (catalog.launcher) {
    if (!semverParts(catalog.launcher.version)) throw new Error("Launcher 版本号无效");
    safeUpdateUrl(catalog.launcher.feed_url);
  }
  if (catalog.release_history !== undefined) {
    if (!Array.isArray(catalog.release_history)) throw new Error("历史公告格式无效");
    for (const entry of catalog.release_history) {
      if (!semverParts(entry?.version) || !entry?.title || !Array.isArray(entry?.summary)) throw new Error("历史公告条目无效");
    }
  }
  return catalog;
}

function rolloutEligible(deviceId, releaseId, rollout = {}) {
  const percentage = Math.max(0, Math.min(100, Number(rollout.percentage ?? 100)));
  if (percentage >= 100) return true;
  if (percentage <= 0) return false;
  const value = crypto.createHash("sha256").update(`${deviceId}:${rollout.salt || releaseId}`).digest().readUInt32BE(0);
  return value % 10_000 < percentage * 100;
}

function sha256(file) {
  const hash = crypto.createHash("sha256");
  const descriptor = fs.openSync(file, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let count;
    while ((count = fs.readSync(descriptor, buffer, 0, buffer.length, null)) > 0) hash.update(buffer.subarray(0, count));
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest("hex");
}

function safeUpdateUrl(raw) {
  const parsed = new URL(String(raw || ""));
  if (parsed.protocol === "https:") return parsed.toString();
  if (parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname)) return parsed.toString();
  throw new Error("正式更新源必须使用 HTTPS；HTTP 仅允许本机测试地址");
}

function parseLastJson(output) {
  const lines = String(output || "").trim().split(/\r?\n/).reverse();
  for (const line of lines) {
    try { return JSON.parse(line); } catch {}
  }
  return null;
}

function createUpdateManager(options) {
  const state = {
    status: "idle",
    progress: 0,
    message: "尚未检查更新",
    latestVersion: "",
    releaseNotes: "",
    releaseTitle: "",
    releaseHistory: [],
    mandatory: false,
    downloaded: false,
    rollbackAvailable: false,
    updateKind: "none",
    coreAvailable: false,
    launcherAvailable: false,
    downloadedBytes: 0,
    totalBytes: 0,
    speedBps: 0,
    remainingSeconds: 0,
    releaseId: "",
    sequence: 0,
    rolloutEligible: true,
    error: "",
  };
  let manifest = null;
  let catalog = null;
  let manifestUrl = "";
  let downloadedPath = "";
  let rollbackToken = "";
  let downloadController = null;

  const fetchImpl = options.fetch || globalThis.fetch;

  function officialUrl(channel) {
    return OFFICIAL_CATALOG_URL.replace("/stable/", `/${channel}/`);
  }

  function config() {
    const stored = options.readConfig();
    const channel = stored.updateChannel || "stable";
    return {
      updateUrl: process.env.MINDSPACE_UPDATE_URL || stored.updateUrl || officialUrl(channel),
      channel,
    };
  }

  function snapshot() {
    const current = readProjectVersion(options.rootPath(), options.app.getVersion());
    const settings = config();
    const launcher = options.launcherUpdater?.snapshot() || null;
    const launcherTransfer = state.updateKind === "launcher" && launcher && ["checking", "available", "downloading", "downloaded", "cancelled", "error", "installing"].includes(launcher.status)
      ? {
        status: launcher.status,
        progress: launcher.progress,
        downloadedBytes: launcher.transferred || 0,
        totalBytes: launcher.total || 0,
        speedBps: launcher.speedBps || 0,
        downloaded: launcher.downloaded,
        message: launcher.message,
        error: launcher.error || "",
      }
      : {};
    const storedHistory = options.readConfig().announcementHistory;
    return {
      ...state,
      releaseHistory: state.releaseHistory.length ? state.releaseHistory : Array.isArray(storedHistory) ? storedHistory : [],
      ...launcherTransfer,
      currentVersion: current,
      launcherVersion: options.app.getVersion(),
      configured: true,
      updateUrl: settings.updateUrl,
      channel: settings.channel,
      launcher,
    };
  }

  function configure(updateUrl, channel = "stable") {
    if (!["stable", "beta"].includes(channel)) throw new Error("更新通道只能是 stable 或 beta");
    const normalized = safeUpdateUrl(updateUrl || officialUrl(channel));
    options.writeConfig({ ...options.readConfig(), updateUrl: normalized, updateChannel: channel });
    manifest = null;
    catalog = null;
    downloadedPath = "";
    Object.assign(state, { status: "idle", progress: 0, message: "官方更新通道已保存", latestVersion: "", downloaded: false, error: "" });
    return snapshot();
  }

  async function check() {
    const settings = config();
    manifestUrl = safeUpdateUrl(settings.updateUrl);
    Object.assign(state, { status: "checking", progress: 0, message: "正在检查更新…", error: "" });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12_000);
    try {
      const response = await fetchImpl(manifestUrl, { cache: "no-store", signal: controller.signal, headers: { "Cache-Control": "no-cache" } });
      if (!response.ok) throw new Error(`更新服务器返回 ${response.status}`);
      const candidate = await response.json();
      const publicKey = fs.readFileSync(options.publicKeyPath, "utf8");
      let eligible = true;
      let launcherAvailable = false;
      if (candidate.schema_version === "2.0.0") {
        catalog = verifyCatalog(candidate, publicKey, settings.channel);
        const stored = options.readConfig();
        const sequenceMap = stored.updateHighestSequence && typeof stored.updateHighestSequence === "object"
          ? stored.updateHighestSequence
          : { [settings.channel]: Number(stored.updateHighestSequence || 0) };
        const highestSequence = Number(sequenceMap[settings.channel] || 0);
        if (catalog.sequence < highestSequence) throw new Error(`拒绝旧更新目录：${catalog.sequence} < ${highestSequence}`);
        if (catalog.sequence > highestSequence) options.writeConfig({
          ...stored,
          updateHighestSequence: { ...sequenceMap, [settings.channel]: catalog.sequence },
        });
        if (Array.isArray(catalog.release_history) && catalog.release_history.length) {
          options.writeConfig({ ...options.readConfig(), announcementHistory: catalog.release_history });
        }
        eligible = Boolean(catalog.core?.mandatory || catalog.launcher?.mandatory)
          || rolloutEligible(options.deviceId || "mindspace", catalog.release_id, catalog.rollout);
        manifest = catalog.core ? {
          schema_version: "1.0.0",
          channel: catalog.channel,
          version: catalog.core.version,
          minimum_launcher: catalog.core.minimum_launcher || "0.4.0",
          mandatory: Boolean(catalog.core.mandatory),
          published_at: catalog.published_at,
          release_notes: catalog.core.release_notes || catalog.release_notes?.summary?.join("\n") || "",
          package: catalog.core.package,
        } : null;
        launcherAvailable = eligible && Boolean(catalog.launcher)
          && compareVersions(catalog.launcher.version, options.app.getVersion()) > 0;
        if (catalog.launcher?.feed_url) options.launcherUpdater?.configure(catalog.launcher.feed_url, catalog.launcher.mandatory);
      } else {
        catalog = null;
        manifest = verifyManifest(candidate, publicKey, settings.channel);
      }
      if (!eligible) {
        Object.assign(state, {
          status: "current", message: "当前灰度批次暂无更新", updateKind: "none", coreAvailable: false,
          launcherAvailable: false, releaseId: catalog?.release_id || "", sequence: catalog?.sequence || 0,
          rolloutEligible: false, error: "",
        });
        return snapshot();
      }
      const currentLauncher = options.app.getVersion();
      const launcherRequiredByCore = Boolean(manifest)
        && compareVersions(currentLauncher, manifest.minimum_launcher) < 0;
      if (launcherRequiredByCore && !launcherAvailable) {
        throw new Error(`需要先升级 Launcher 至 ${manifest.minimum_launcher}`);
      }
      const current = readProjectVersion(options.rootPath(), options.app.getVersion());
      const coreAvailable = Boolean(manifest) && compareVersions(manifest.version, current) > 0;
      const launcherRequired = launcherAvailable
        && (Boolean(catalog?.launcher?.mandatory) || launcherRequiredByCore);
      const updateKind = launcherRequired || (launcherAvailable && !coreAvailable)
        ? "launcher"
        : coreAvailable ? "core" : "none";
      if (updateKind === "launcher") await options.launcherUpdater?.check();
      const available = launcherAvailable || coreAvailable;
      const latestVersion = updateKind === "launcher" ? catalog.launcher.version : manifest?.version || current;
      Object.assign(state, {
        status: available ? "available" : "current",
        message: updateKind === "launcher" ? `发现 Launcher ${latestVersion}` : coreAvailable ? `发现 Mindspace Core ${latestVersion}` : `当前已是最新版本 ${current}`,
        latestVersion,
        releaseNotes: manifest?.release_notes || catalog?.release_notes?.summary?.join("\n") || "",
        releaseTitle: catalog?.release_notes?.title || `Mindspace ${latestVersion}`,
        releaseHistory: Array.isArray(catalog?.release_history) ? catalog.release_history : [],
        mandatory: updateKind === "launcher" ? Boolean(catalog?.launcher?.mandatory || launcherRequiredByCore) : Boolean(manifest?.mandatory),
        downloaded: false,
        updateKind,
        coreAvailable,
        launcherAvailable,
        releaseId: catalog?.release_id || "",
        sequence: catalog?.sequence || 0,
        rolloutEligible: true,
        error: "",
      });
      return snapshot();
    } catch (error) {
      Object.assign(state, { status: "error", message: "检查更新失败", error: String(error.message || error) });
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  async function download() {
    if (!manifest && state.updateKind === "none") await check();
    if (state.updateKind === "launcher") {
      Object.assign(state, { status: "downloading", updateKind: "launcher", message: "正在下载 Launcher 差分更新…", error: "" });
      const result = await options.launcherUpdater.download();
      Object.assign(state, { status: result.status, progress: result.progress, downloaded: result.downloaded, message: result.message, error: result.error || "" });
      return snapshot();
    }
    if (!manifest) await check();
    if (state.updateKind === "launcher") return download();
    if (!manifest || compareVersions(manifest.version, snapshot().currentVersion) <= 0) return snapshot();
    const source = new URL(manifest.package.url, manifestUrl).toString();
    safeUpdateUrl(source);
    const downloadRoot = options.downloadRoot || path.join(options.app.getPath("userData"), "updates", "downloads");
    fs.mkdirSync(downloadRoot, { recursive: true });
    const finalPath = path.join(downloadRoot, `mindspace-core-${manifest.version}.zip`);
    const partialPath = `${finalPath}.partial`;
    if (fs.existsSync(finalPath) && fs.statSync(finalPath).size === manifest.package.size && sha256(finalPath) === manifest.package.sha256.toLowerCase()) {
      downloadedPath = finalPath;
      Object.assign(state, { status: "downloaded", progress: 100, downloadedBytes: manifest.package.size, totalBytes: manifest.package.size, downloaded: true, message: "更新包已在本地并通过校验", error: "" });
      return snapshot();
    }
    if (fs.existsSync(partialPath) && fs.statSync(partialPath).size > manifest.package.size) fs.rmSync(partialPath, { force: true });
    if (fs.existsSync(partialPath) && fs.statSync(partialPath).size === manifest.package.size) {
      if (sha256(partialPath) === manifest.package.sha256.toLowerCase()) {
        fs.rmSync(finalPath, { force: true });
        fs.renameSync(partialPath, finalPath);
        downloadedPath = finalPath;
        Object.assign(state, { status: "downloaded", progress: 100, downloadedBytes: manifest.package.size, totalBytes: manifest.package.size, downloaded: true, message: "续传文件已完整并通过校验", error: "" });
        return snapshot();
      }
      fs.rmSync(partialPath, { force: true });
    }
    let offset = fs.existsSync(partialPath) ? fs.statSync(partialPath).size : 0;
    Object.assign(state, { status: "downloading", updateKind: "core", progress: manifest.package.size ? offset / manifest.package.size * 100 : 0, downloadedBytes: offset, totalBytes: manifest.package.size, message: offset ? "正在断点续传 Core 更新…" : "正在下载 Core 更新…", error: "" });
    downloadController = new AbortController();
    let output = null;
    try {
      let headers = offset ? { Range: `bytes=${offset}-` } : {};
      let response = await fetchImpl(source, { cache: "no-store", signal: downloadController.signal, headers });
      if (offset && response.status !== 206) {
        fs.rmSync(partialPath, { force: true });
        offset = 0;
        headers = {};
        response = await fetchImpl(source, { cache: "no-store", signal: downloadController.signal, headers });
      }
      if (!response.ok || !response.body) throw new Error(`更新包下载失败：${response.status}`);
      output = fs.createWriteStream(partialPath, { flags: offset ? "a" : "w" });
      let received = offset;
      let transferred = 0;
      const startedAt = Date.now();
      for await (const chunk of response.body) {
        received += chunk.length;
        transferred += chunk.length;
        if (received > manifest.package.size + 1024) throw new Error("更新包大小超过清单声明");
        if (!output.write(chunk)) await new Promise((resolve) => output.once("drain", resolve));
        const elapsed = Math.max(0.25, (Date.now() - startedAt) / 1000);
        const speedBps = transferred / elapsed;
        Object.assign(state, {
          progress: Math.min(100, Math.round(received / manifest.package.size * 1000) / 10),
          downloadedBytes: received,
          totalBytes: manifest.package.size,
          speedBps,
          remainingSeconds: speedBps ? Math.ceil((manifest.package.size - received) / speedBps) : 0,
        });
      }
      await new Promise((resolve, reject) => output.end((error) => error ? reject(error) : resolve()));
      if (received !== manifest.package.size) throw new Error("更新包大小与清单不一致");
      Object.assign(state, { status: "verifying", progress: 100, speedBps: 0, remainingSeconds: 0, message: "正在校验 Core 更新…" });
      if (sha256(partialPath) !== manifest.package.sha256.toLowerCase()) {
        fs.rmSync(partialPath, { force: true });
        throw new Error("更新包 SHA-256 校验失败，已删除损坏文件");
      }
      fs.rmSync(finalPath, { force: true });
      fs.renameSync(partialPath, finalPath);
      downloadedPath = finalPath;
      Object.assign(state, { status: "downloaded", progress: 100, speedBps: 0, remainingSeconds: 0, message: "Core 更新已下载并通过签名与哈希校验", downloaded: true });
      return snapshot();
    } catch (error) {
      output?.destroy();
      if (downloadController?.signal.aborted) {
        Object.assign(state, { status: "paused", speedBps: 0, remainingSeconds: 0, message: "下载已暂停，可继续断点续传", error: "" });
        return snapshot();
      }
      Object.assign(state, { status: "error", message: "更新下载失败", error: String(error.message || error) });
      throw error;
    } finally {
      downloadController = null;
    }
  }

  function pause() {
    if (state.updateKind === "launcher") options.launcherUpdater?.cancel();
    else downloadController?.abort();
    return snapshot();
  }

  function discard() {
    pause();
    if (manifest) {
      const root = options.downloadRoot || path.join(options.app.getPath("userData"), "updates", "downloads");
      fs.rmSync(path.join(root, `mindspace-core-${manifest.version}.zip.partial`), { force: true });
    }
    Object.assign(state, { status: "available", progress: 0, downloadedBytes: 0, speedBps: 0, remainingSeconds: 0, downloaded: false, message: "已取消并清除本次下载" });
    return snapshot();
  }

  function runApply(arguments_) {
    const ps7 = options.resolvePowerShell();
    if (!ps7) throw new Error("未找到 PowerShell 7");
    const script = path.join(options.rootPath(), "scripts", "apply-update.ps1");
    const result = spawnSync(ps7, ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, ...arguments_], {
      cwd: options.rootPath(), encoding: "utf8", windowsHide: true, timeout: 180_000,
    });
    if (result.status !== 0) throw new Error((result.stderr || result.stdout || "更新安装失败").trim());
    return parseLastJson(result.stdout);
  }

  async function install() {
    if (state.updateKind === "launcher") return options.launcherUpdater.install();
    if (!downloadedPath || !fs.existsSync(downloadedPath)) await download();
    Object.assign(state, { status: "installing", message: "正在停止服务并安装更新…", error: "" });
    await options.stopServicesForUpdate();
    let applied;
    try {
      applied = runApply(["-Root", options.rootPath(), "-Package", downloadedPath, "-Version", manifest.version]);
      if (!applied?.ok) throw new Error("更新安装脚本未返回成功状态");
      rollbackToken = applied.rollback_token;
      state.rollbackAvailable = true;
      await options.startServices();
      const healthy = await options.waitForHealth(120_000);
      if (!healthy) throw new Error("新版服务健康检查未通过");
      Object.assign(state, { status: "installed", message: `Mindspace ${manifest.version} 已安装并通过健康检查`, downloaded: false, progress: 100 });
      return snapshot();
    } catch (error) {
      if (rollbackToken) {
        try {
          await options.stopServicesForUpdate();
          runApply(["-Root", options.rootPath(), "-RollbackToken", rollbackToken]);
          await options.startServices();
          await options.waitForHealth(120_000);
        } catch (rollbackError) {
          error.message = `${error.message}；自动回滚失败：${rollbackError.message}`;
        }
      }
      Object.assign(state, { status: "error", message: "更新失败，已尝试回滚", error: String(error.message || error) });
      throw error;
    }
  }

  async function rollback() {
    if (!rollbackToken) throw new Error("当前没有可回滚版本");
    Object.assign(state, { status: "installing", message: "正在回滚上一版本…", error: "" });
    await options.stopServicesForUpdate();
    runApply(["-Root", options.rootPath(), "-RollbackToken", rollbackToken]);
    await options.startServices();
    const healthy = await options.waitForHealth(120_000);
    if (!healthy) throw new Error("回滚后健康检查未通过");
    rollbackToken = "";
    Object.assign(state, { status: "rolled-back", message: "已回滚到上一版本", rollbackAvailable: false, progress: 0 });
    return snapshot();
  }

  return { snapshot, configure, check, download, pause, discard, install, rollback };
}

module.exports = {
  OFFICIAL_CATALOG_URL, canonical, compareVersions, createUpdateManager, readProjectVersion,
  rolloutEligible, safeUpdateUrl, sha256, verifyCatalog, verifyManifest,
};

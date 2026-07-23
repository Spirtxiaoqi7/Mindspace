const { EventEmitter } = require("node:events");

const OFFICIAL_LAUNCHER_FEED = "https://douyinqijun.cn/downloads/mindspace/launcher/stable/";

function createLauncherUpdater(options = {}) {
  const state = {
    status: "idle",
    currentVersion: options.currentVersion?.() || "",
    latestVersion: "",
    progress: 0,
    transferred: 0,
    total: 0,
    speedBps: 0,
    downloaded: false,
    mandatory: false,
    message: "Launcher 已就绪",
    error: "",
    feedUrl: options.feedUrl || OFFICIAL_LAUNCHER_FEED,
  };
  let updater = options.updater;
  let cancellationToken = null;
  let cancelRequested = false;

  function snapshot() {
    state.currentVersion = options.currentVersion?.() || state.currentVersion;
    return { ...state };
  }

  function bindEvents(target) {
    target.on("checking-for-update", () => Object.assign(state, { status: "checking", message: "正在检查 Launcher 更新…", error: "" }));
    target.on("update-available", (info = {}) => Object.assign(state, {
      status: "available", latestVersion: String(info.version || ""), message: `发现 Launcher ${info.version || "新版本"}`, error: "",
    }));
    target.on("update-not-available", (info = {}) => Object.assign(state, {
      status: "current", latestVersion: String(info.version || state.currentVersion), message: "Launcher 已是最新版本", error: "",
    }));
    target.on("download-progress", (progress = {}) => Object.assign(state, {
      status: "downloading", progress: Number(progress.percent || 0), transferred: Number(progress.transferred || 0),
      total: Number(progress.total || 0), speedBps: Number(progress.bytesPerSecond || 0), message: "正在下载 Launcher 差分更新…", error: "",
    }));
    target.on("update-downloaded", (info = {}) => Object.assign(state, {
      status: "downloaded", latestVersion: String(info.version || state.latestVersion), progress: 100,
      downloaded: true, message: "Launcher 更新已下载，重启后安装", error: "",
    }));
    target.on("update-cancelled", () => Object.assign(state, {
      status: "cancelled", speedBps: 0, message: "Launcher 下载已取消，可重新开始", error: "",
    }));
    target.on("error", (error) => Object.assign(state, {
      status: "error", speedBps: 0, message: "Launcher 更新失败", error: String(error?.message || error),
    }));
  }

  function ensureUpdater() {
    if (updater) return updater;
    if (options.packaged === false) return null;
    const { NsisUpdater } = require("electron-updater");
    updater = new NsisUpdater({
      provider: "generic",
      url: state.feedUrl,
      // Some proxies return malformed multipart bodies for multi-range
      // requests. Sequential ranges retain differential updates and are much
      // more reliable on domestic networks.
      useMultipleRangeRequest: false,
    });
    updater.autoDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.allowPrerelease = false;
    bindEvents(updater);
    return updater;
  }

  if (updater) bindEvents(updater);

  function configure(feedUrl, mandatory = false) {
    if (feedUrl) {
      const parsed = new URL(feedUrl);
      if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname))) {
        throw new Error("Launcher 正式更新源必须使用 HTTPS");
      }
      state.feedUrl = parsed.toString();
    }
    state.mandatory = Boolean(mandatory);
    const target = ensureUpdater();
    target?.setFeedURL({ provider: "generic", url: state.feedUrl, useMultipleRangeRequest: false });
    return snapshot();
  }

  async function check() {
    const target = ensureUpdater();
    if (!target) {
      Object.assign(state, { status: "disabled", message: "开发模式不检查 Launcher 更新" });
      return snapshot();
    }
    Object.assign(state, { status: "checking", message: "正在检查 Launcher 更新…", error: "" });
    await target.checkForUpdates();
    return snapshot();
  }

  async function download() {
    const target = ensureUpdater();
    if (!target) throw new Error("当前环境不支持 Launcher 自动更新");
    const { CancellationToken } = require("electron-updater");
    cancelRequested = false;
    let lastError;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      cancellationToken = new CancellationToken();
      Object.assign(state, {
        status: "downloading",
        progress: attempt === 1 ? 0 : state.progress,
        message: attempt === 1 ? "正在下载 Launcher 差分更新…" : `网络中断，正在第 ${attempt} 次续试…`,
        error: "",
      });
      try {
        await target.downloadUpdate(cancellationToken);
        return snapshot();
      } catch (error) {
        lastError = error;
        if (cancelRequested) throw error;
        if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, options.retryDelayMs ?? 600));
      }
    }
    throw lastError;
  }

  function cancel() {
    cancelRequested = true;
    cancellationToken?.cancel();
    cancellationToken = null;
    Object.assign(state, { status: "cancelled", speedBps: 0, message: "Launcher 下载已取消，可重新开始" });
    return snapshot();
  }

  function install() {
    const target = ensureUpdater();
    if (!target || !state.downloaded) throw new Error("Launcher 更新尚未下载完成");
    Object.assign(state, { status: "installing", message: "正在静默更新 Launcher，完成后自动重启…" });
    target.quitAndInstall(true, true);
    return snapshot();
  }

  return { snapshot, configure, check, download, cancel, install };
}

module.exports = { OFFICIAL_LAUNCHER_FEED, createLauncherUpdater };

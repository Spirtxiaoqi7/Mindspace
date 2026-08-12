const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { createUpdateManager } = require("./update-manager.cjs");
const { createLauncherUpdater } = require("./launcher-updater.cjs");

function createUpdateController(dependencies) {
  const { app, dirname, fetch, rootPath, resolvePowerShell, currentLayout, readConfig, writeConfig, stopServices, startServices, waitForHealth, logRoot } = dependencies;
  let config = readConfig();
  if (!config.updateDeviceId) {
    config = { ...config, updateDeviceId: crypto.randomUUID() };
    writeConfig(config);
  }
  const launcherUpdater = createLauncherUpdater({ packaged: app.isPackaged, currentVersion: () => app.getVersion() });
  const manager = createUpdateManager({
    app, rootPath, resolvePowerShell,
    publicKeyPath: path.join(dirname, "assets", "update-public-key.pem"), fetch,
    downloadRoot: path.join(currentLayout().downloads, "updates"), deviceId: config.updateDeviceId,
    launcherUpdater,
    bundledRoot: app.isPackaged ? path.join(process.resourcesPath, "runtime", "bundled") : path.join(dirname, "bootstrap", "runtime-bundle"),
    readConfig, writeConfig, stopServicesForUpdate: stopServices, startServices, waitForHealth,
  });
  const automaticLogPath = () => path.join(logRoot ? logRoot() : currentLayout().logs, "update-controller.jsonl");

  function automaticCheckState() {
    const stored = readConfig().automaticUpdateCheck;
    return stored && typeof stored === "object" ? stored : { status: "idle", phase: "", at: "", error: "" };
  }

  function writeAutomaticCheck(state) {
    const next = {
      status: state.status === "error" ? "error" : "ok",
      phase: String(state.phase || "check"),
      at: new Date().toISOString(),
      error: state.status === "error" ? String(state.error || "未知更新错误") : "",
    };
    writeConfig({ ...readConfig(), automaticUpdateCheck: next });
    try {
      fs.mkdirSync(path.dirname(automaticLogPath()), { recursive: true });
      fs.appendFileSync(automaticLogPath(), `${JSON.stringify({ event: "automatic_update_check", ...next })}\n`, "utf8");
    } catch {
      // Update observability must never prevent normal manual update actions.
    }
    return next;
  }

  function snapshot() {
    return { ...manager.snapshot(), automaticCheck: automaticCheckState() };
  }

  async function action(actionName, { updateUrl, channel } = {}) {
    if (actionName === "snapshot") return snapshot();
    if (actionName === "configure") return manager.configure(updateUrl, channel);
    if (["check", "download", "pause", "discard", "install", "rollback"].includes(actionName)) return manager[actionName]();
    throw new Error("未知更新操作");
  }
  function registerIpc(ipcMain) {
    ipcMain.handle("launcher:update", (_event, { action: name, updateUrl, channel } = {}) => action(name, { updateUrl, channel }));
  }
  const checkConfiguredFeed = async () => {
    try {
      if (["checking", "downloading", "verifying", "installing"].includes(manager.snapshot().status)) return;
      const next = await manager.check();
      if (next.coreAvailable && !next.launcherAvailable && !next.downloaded && readConfig().autoDownloadUpdates !== false) {
        try {
          await manager.download();
          writeAutomaticCheck({ status: "ok", phase: "download" });
        } catch (error) {
          writeAutomaticCheck({ status: "error", phase: "download", error: String(error?.message || error) });
        }
      } else {
        writeAutomaticCheck({ status: "ok", phase: "check" });
      }
    } catch (error) {
      writeAutomaticCheck({ status: "error", phase: "check", error: String(error?.message || error) });
    }
  };
  setTimeout(checkConfiguredFeed, 5_000).unref();
    setInterval(checkConfiguredFeed, 6 * 60 * 60 * 1000).unref();
  return { action, checkConfiguredFeed, manager, registerIpc, snapshot };
}

module.exports = { createUpdateController };

const crypto = require("node:crypto");
const path = require("node:path");
const { createUpdateManager } = require("./update-manager.cjs");
const { createLauncherUpdater } = require("./launcher-updater.cjs");

function createUpdateController(dependencies) {
  const { app, dirname, fetch, rootPath, resolvePowerShell, currentLayout, readConfig, writeConfig, stopServices, startServices, waitForHealth } = dependencies;
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
  async function action(actionName, { updateUrl, channel } = {}) {
    if (actionName === "snapshot") return manager.snapshot();
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
      if (next.coreAvailable && !next.launcherAvailable && !next.downloaded && readConfig().autoDownloadUpdates !== false) await manager.download();
    } catch {}
  };
  setTimeout(checkConfiguredFeed, 5_000).unref();
    setInterval(checkConfiguredFeed, 6 * 60 * 60 * 1000).unref();
  return { action, manager, registerIpc };
}

module.exports = { createUpdateController };

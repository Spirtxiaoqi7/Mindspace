const fs = require("node:fs");
const path = require("node:path");
const { screen } = require("electron");
const { normalizeCompanionConfig, companionBoundsForDisplay } = require("./companion-policy.cjs");

const COMPANION_RELEASE = Object.freeze({
  available: false,
  targetVersion: "1.0",
  message: "Live2D 桌宠将在 V1.0 正式版开放",
});

function companionCaptureArguments(argv = process.argv) {
  return {
    capture: argv.find((argument) => argument.startsWith("--capture-companion=")) || "",
    blank: argv.find((argument) => argument.startsWith("--capture-companion-blank=")) || "",
  };
}

function isCompanionCaptureMode(argv = process.argv) {
  const capture = companionCaptureArguments(argv);
  return Boolean(capture.capture || capture.blank);
}

function createCompanionController({
  app,
  BrowserWindow,
  dirname,
  getLauncherWindow,
  isHostCaptureMode,
  isQuitting,
  readLauncherConfig,
  writeLauncherConfig,
}) {
  const captureArguments = companionCaptureArguments(process.argv);
  let companionWindow;
  let loadError = "";
  let boundsTimer = null;

  function config() {
    return normalizeCompanionConfig(readLauncherConfig().companion);
  }

  function writeConfig(patch = {}) {
    const launcherConfig = readLauncherConfig();
    const companion = normalizeCompanionConfig({ ...launcherConfig.companion, ...patch });
    writeLauncherConfig({ ...launcherConfig, companion });
    return companion;
  }

  function resourcePaths() {
    const renderer = path.join(dirname, "assets", "companion-renderer", "index.html");
    const modelRoot = path.join(dirname, "assets", "companion-renderer", "Resources", "mindspace-companion-v24");
    const model = path.join(modelRoot, "mindspace-companion-v24.model3.json");
    return { renderer, modelRoot, model };
  }

  function snapshot() {
    const current = config();
    const launcherWindow = getLauncherWindow();
    return {
      ...current,
      enabled: false,
      clickThrough: false,
      available: COMPANION_RELEASE.available,
      targetVersion: COMPANION_RELEASE.targetVersion,
      ready: false,
      visible: false,
      previewVisible: Boolean(launcherWindow && !launcherWindow.isDestroyed() && launcherWindow.isVisible()),
      error: COMPANION_RELEASE.message,
      sdkVersion: "",
      modelVersion: "",
    };
  }

  function scheduleBoundsSave() {
    if (!companionWindow || companionWindow.isDestroyed()) return;
    clearTimeout(boundsTimer);
    boundsTimer = setTimeout(() => {
      if (!companionWindow || companionWindow.isDestroyed()) return;
      writeConfig(companionWindow.getBounds());
    }, 250);
  }

  function createWindow({ blank = false } = {}) {
    if (companionWindow && !companionWindow.isDestroyed()) return companionWindow;
    const current = config();
    const remembered = current.x === null || current.y === null
      ? screen.getPrimaryDisplay()
      : screen.getDisplayMatching({ x: current.x, y: current.y, width: current.width, height: current.height });
    const bounds = companionBoundsForDisplay(current, remembered.workArea);
    companionWindow = new BrowserWindow({
      ...bounds,
      minWidth: 260,
      minHeight: 360,
      maxWidth: 720,
      maxHeight: 1000,
      show: false,
      frame: false,
      transparent: true,
      backgroundColor: "#00000000",
      alwaysOnTop: true,
      skipTaskbar: true,
      hasShadow: false,
      resizable: true,
      title: "Mindspace Companion",
      webPreferences: { contextIsolation: true, nodeIntegration: false, backgroundThrottling: true },
    });
    companionWindow.setAlwaysOnTop(true, "floating");
    companionWindow.setIgnoreMouseEvents(current.clickThrough, { forward: true });
    companionWindow.on("move", scheduleBoundsSave);
    companionWindow.on("resize", scheduleBoundsSave);
    companionWindow.on("closed", () => { companionWindow = undefined; });
    companionWindow.webContents.on("did-fail-load", (_event, code, description) => {
      loadError = `Live2D 加载失败：${code} ${description}`;
      companionWindow?.hide();
    });
    if (blank) companionWindow.loadURL("about:blank");
    else companionWindow.loadFile(resourcePaths().renderer);
    return companionWindow;
  }

  function syncVisibility() {
    if (isHostCaptureMode() || isQuitting()) return snapshot();
    const current = snapshot();
    if (!current.enabled || !current.ready) {
      companionWindow?.hide();
      return snapshot();
    }
    const win = createWindow();
    win.setIgnoreMouseEvents(current.clickThrough, { forward: true });
    win.showInactive();
    return snapshot();
  }

  function action(actionName) {
    if (actionName !== "snapshot") return { ...snapshot(), ok: false, error: COMPANION_RELEASE.message };
    return { ...snapshot(), ok: true };
  }

  function startCaptureMode() {
    if (!captureArguments.capture && !captureArguments.blank) return false;
    const outputArgument = captureArguments.capture || captureArguments.blank;
    const outputPrefix = captureArguments.capture ? "--capture-companion=" : "--capture-companion-blank=";
    const win = createWindow({ blank: Boolean(captureArguments.blank) });
    if (captureArguments.capture) win.showInactive();
    win.webContents.once("did-finish-load", () => {
      if (captureArguments.capture) {
        void win.webContents.executeJavaScript(`new Promise((resolve) => {
          let frames = 0;
          const started = performance.now();
          const tick = (now) => {
            frames += 1;
            if (now - started >= 5000) resolve(Number((frames * 1000 / (now - started)).toFixed(2)));
            else requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        })`).then((fps) => console.log(`[companion-qa] average-fps=${fps}`));
      }
      setTimeout(async () => {
        const output = outputArgument.slice(outputPrefix.length);
        const image = await win.webContents.capturePage();
        fs.writeFileSync(output, image.toPNG());
        app.quit();
      }, Math.max(800, Math.min(30_000, Number(process.env.MINDSPACE_CAPTURE_DELAY_MS) || 2_500)));
    });
    return true;
  }

  function destroy() {
    clearTimeout(boundsTimer);
    if (companionWindow && !companionWindow.isDestroyed()) companionWindow.destroy();
  }

  function registerIpc(ipcMain) {
    ipcMain.handle("companion:snapshot", () => snapshot());
    ipcMain.handle("companion:action", (_event, { action: actionName = "snapshot" } = {}) => action(String(actionName)));
  }

  return { action, createWindow, destroy, registerIpc, snapshot, startCaptureMode, syncVisibility };
}

module.exports = { createCompanionController, isCompanionCaptureMode };

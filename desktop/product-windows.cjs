const fs = require("node:fs");
const path = require("node:path");
const { classifyExternalUrl } = require("./external-navigation.cjs");

function createProductWindows(dependencies) {
  const {
    app, BrowserWindow, dialog, shell, services, dirname, captureArg, captureDashboardArg,
    dashboardPreviewArg, captureAnnouncement, softwareDesktopRendering, probe, productEntryState,
    recordStabilityEvent, syncCompanionVisibility, onLauncherChanged, onProductChanged,
  } = dependencies;
  let launcherWindow;
  let productWindow;
  const setLauncher = (value) => { launcherWindow = value; onLauncherChanged?.(value); };
  const setProduct = (value) => { productWindow = value; onProductChanged?.(value); };

  function showStabilityFallback(title, detail) {
    launcherWindow?.show(); launcherWindow?.focus();
    const options = { type: "error", title, message: "聊天窗口已安全关闭，服务控制中心仍可使用", detail, buttons: ["知道了"], noLink: true };
    const task = launcherWindow && !launcherWindow.isDestroyed() ? dialog.showMessageBox(launcherWindow, options) : dialog.showMessageBox(options);
    void task.catch(() => undefined);
  }
  function recoverProductWindow(kind, details = {}) {
    recordStabilityEvent(kind, details);
    const failedWindow = productWindow;
    setProduct(undefined);
    setImmediate(() => {
      if (failedWindow && !failedWindow.isDestroyed()) failedWindow.destroy();
      showStabilityFallback("Mindspace 聊天窗口已恢复", "检测到界面渲染异常。Mindspace 已停止该窗口，避免异常继续影响桌面；重新进入聊天会创建干净窗口。");
    });
  }
  async function openExternalSafely(rawUrl, parentWindow) {
    const decision = classifyExternalUrl(rawUrl);
    if (decision.action === "deny") return { ok: false, error: `已拒绝不安全外链：${decision.reason}` };
    if (decision.action === "confirm") {
      const options = { type: "question", title: "打开外部网站", message: `是否在系统浏览器中打开 ${decision.host}？`, detail: decision.url, buttons: ["取消", "继续打开"], defaultId: 0, cancelId: 0, noLink: true };
      const result = parentWindow && !parentWindow.isDestroyed() ? await dialog.showMessageBox(parentWindow, options) : await dialog.showMessageBox(options);
      if (result.response !== 1) return { ok: false, cancelled: true };
    }
    await shell.openExternal(decision.url);
    return { ok: true };
  }
  function isTrustedProductOrigin(value) {
    try { return new URL(String(value || "")).origin === services.api.origin; } catch { return false; }
  }
  function configureProductMediaPermissions(targetSession) {
    targetSession.webRequest.onBeforeRequest((details, callback) => {
      try { const target = new URL(details.url); callback({ cancel: target.origin === services.api.origin && target.pathname.toLowerCase().endsWith(".map") }); }
      catch { callback({ cancel: true }); }
    });
    targetSession.setPermissionCheckHandler((_contents, permission, requestingOrigin, details = {}) => {
      const origin = details.securityOrigin || details.requestingUrl || requestingOrigin;
      const audioOnly = !details.mediaType || details.mediaType === "audio" || details.mediaType === "unknown";
      return permission === "media" && audioOnly && isTrustedProductOrigin(origin);
    });
    targetSession.setPermissionRequestHandler((_contents, permission, callback, details = {}) => {
      const origin = details.requestingUrl || details.securityOrigin || "";
      const mediaTypes = Array.isArray(details.mediaTypes) ? details.mediaTypes : [];
      callback(permission === "media" && (mediaTypes.length === 0 || mediaTypes.every((type) => type === "audio")) && isTrustedProductOrigin(origin));
    });
  }
  function createLauncherWindow() {
    const win = new BrowserWindow({
      width: 1180, height: 760, minWidth: 920, minHeight: 620,
      show: !captureArg || Boolean(captureDashboardArg), backgroundColor: "#0b0d11", titleBarStyle: "hidden",
      titleBarOverlay: { color: "#0b0d11", symbolColor: "#bbc4d0", height: 40 },
      webPreferences: { preload: path.join(dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false },
    });
    setLauncher(win);
    win.loadFile(path.join(dirname, "dist", "index.html"), captureAnnouncement ? { query: { announcement: "history" } } : captureDashboardArg || dashboardPreviewArg ? { query: { dashboard: "1" } } : undefined);
    win.on("close", (event) => { if (!dependencies.isQuitting() && !captureArg) { event.preventDefault(); win.hide(); } });
    win.on("show", syncCompanionVisibility); win.on("hide", syncCompanionVisibility);
    win.on("closed", () => setLauncher(undefined));
    if (captureArg) win.webContents.once("did-finish-load", () => setTimeout(async () => {
      const output = captureArg.slice((captureDashboardArg ? "--capture-dashboard=" : "--capture=").length);
      fs.writeFileSync(output, (await win.webContents.capturePage()).toPNG()); app.quit();
    }, Math.max(500, Math.min(30_000, Number(process.env.MINDSPACE_CAPTURE_DELAY_MS) || 1800))));
    return win;
  }
  async function openProductWindow() {
    if (productWindow && !productWindow.isDestroyed()) { productWindow.show(); productWindow.focus(); return { ok: true }; }
    const api = await probe(services.api);
    if (!api.online) {
      launcherWindow?.show(); launcherWindow?.focus();
      await dialog.showMessageBox(launcherWindow, { type: "info", title: "Mindspace 尚未启动", message: "请先启动本地服务", detail: "在服务控制中心点击“启动并进入”，应用会等待核心服务就绪后自动打开。" });
      return { ok: false, error: "Mindspace Core 尚未就绪" };
    }
    const entry = productEntryState({ coreOnline: api.online, asrOnline: (await probe(services.asr)).online });
    if (entry.mode === "text-only") await dialog.showMessageBox(launcherWindow, { type: "info", title: "仅文字模式", message: "本次启动不存在语音功能", detail: "VAD/ASR 未启动。你仍可正常进入并使用文字对话；需要语音时可返回启动器，在“实时聆听”中安装并启动。", buttons: ["继续进入"], defaultId: 0, noLink: true });
    const win = new BrowserWindow({
      width: 1480, height: 920, minWidth: 1040, minHeight: 700, show: false,
      backgroundColor: "#f7efe4", title: "Mindspace", autoHideMenuBar: true, icon: path.join(dirname, "assets", "mindspace-icon.ico"),
      webPreferences: { preload: path.join(dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, backgroundThrottling: true },
    });
    setProduct(win);
    let productReady = false;
    const loadTimeout = setTimeout(() => { if (!productReady && !win.isDestroyed()) recoverProductWindow("product-load-timeout", { timeoutMs: 15_000 }); }, 15_000);
    loadTimeout.unref(); win.setMenuBarVisibility(false); configureProductMediaPermissions(win.webContents.session);
    win.webContents.setWindowOpenHandler(({ url }) => { void openExternalSafely(url, win); return { action: "deny" }; });
    const productUrl = new URL(`${services.api.origin}/`); productUrl.searchParams.set("desktop-build", `${app.getVersion()}-${Date.now()}`); win.loadURL(productUrl.toString());
    win.webContents.on("did-finish-load", () => { if (softwareDesktopRendering) void win.webContents.insertCSS("* { -webkit-backdrop-filter: none !important; backdrop-filter: none !important; } .voice-background { filter: none !important; opacity: .35 !important; }").catch((error) => recordStabilityEvent("software-rendering-css-failed", { error: String(error?.message || error) })); });
    win.once("ready-to-show", () => { productReady = true; clearTimeout(loadTimeout); win.show(); launcherWindow?.hide(); });
    win.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL, isMainFrame) => { if (isMainFrame && !win.isDestroyed()) recoverProductWindow("product-load-failed", { errorCode, errorDescription, url: validatedURL }); });
    win.on("unresponsive", () => recordStabilityEvent("product-window-unresponsive"));
    win.on("responsive", () => recordStabilityEvent("product-window-responsive"));
    win.on("closed", () => { clearTimeout(loadTimeout); if (productWindow === win) setProduct(undefined); });
    return { ok: true };
  }
  return {
    createLauncherWindow, getLauncherWindow: () => launcherWindow, getProductWindow: () => productWindow,
    isProductSender: (sender) => Boolean(productWindow && !productWindow.isDestroyed() && sender?.id === productWindow.webContents.id),
    openExternalSafely, openProductWindow, recoverProductWindow,
  };
}

module.exports = { createProductWindows };

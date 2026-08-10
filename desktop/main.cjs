const { app, BrowserWindow, dialog, ipcMain, Menu, net, safeStorage, screen, session, shell, Tray } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const extractZip = require("extract-zip");
const originalProxyEnvironment = {
  HTTP_PROXY: process.env.HTTP_PROXY,
  HTTPS_PROXY: process.env.HTTPS_PROXY,
  ALL_PROXY: process.env.ALL_PROXY,
};
const { createComponentManager } = require("./component-manager.cjs");
const { GPT_SOVITS_VOICES } = require("./gpt-sovits-catalog.cjs");
const { createRuntimeManager } = require("./runtime-manager.cjs");
const { evaluateHardwareAvailability } = require("./hardware-policy.cjs");
const {
  LLM_PRESETS,
  ONBOARDING_VERSION,
  deriveOnboardingSnapshot,
  isLoopbackUrl,
  normalizeVoicePreference,
  voicePreferenceFromProvider,
  voiceInstallPlan,
} = require("./onboarding-policy.cjs");
const { evaluateQwenRuntimePreflight } = require("./qwen-runtime-policy.cjs");
const {
  SERVICE_START_ORDER,
  isFatalStartFailure,
  isStaleCore,
  productEntryState,
  serviceRestartDelay,
} = require("./service-policy.cjs");
const { appPaths, ensureAppPaths, migrateLegacyLayout, reconcileLegacyModelPaths } = require("./app-paths.cjs");
const { cleanupMigratedSource, inspectStorageAlignment, migrateStorage } = require("./storage-location.cjs");
const { normalizeCompanionConfig, companionBoundsForDisplay } = require("./companion-policy.cjs");
const { createSecretStore } = require("./secret-store.cjs");
const { createServiceSupervisor } = require("./service-supervisor.cjs");
const { createSettingsController } = require("./settings-controller.cjs");
const { createProductWindows } = require("./product-windows.cjs");
const { createUpdateController } = require("./update-controller.cjs");
const { environmentForPorts, loadServicePorts, resolvePortConfigPath } = require("./service-ports.cjs");
const {
  bundledArchive,
  bundledVersion,
  ensureCoreRoot,
  resolveWorkspaceRoot,
} = require("./bootstrap-core.cjs");

// Chromium GPU composition has caused a Windows LiveKernelEvent 141 on the
// supported desktop path. Keep CUDA model workers independent, but render the
// Electron shell in software unless a developer explicitly opts back in.
const softwareDesktopRendering = process.env.MINDSPACE_ENABLE_HARDWARE_ACCELERATION !== "1";
if (softwareDesktopRendering) {
  app.disableHardwareAcceleration();
}

function resolvePowerShell() {
  const privateMarker = layout && readJson(path.join(layout.state, "components", "powershell.json"));
  const candidates = [
    privateMarker?.executable,
    process.env.MINDSPACE_PWSH,
    !app.isPackaged && process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Programs", "PowerShell", "7", "pwsh.exe"),
  ].filter(Boolean);
  const installed = candidates.find((candidate) => fs.existsSync(candidate));
  if (installed || app.isPackaged) return installed || "";
  const located = spawnSync("where.exe", ["pwsh.exe"], { encoding: "utf8", windowsHide: true });
  return located.status === 0 ? located.stdout.split(/\r?\n/).find(Boolean) : "";
}
const portRegistry = loadServicePorts({
  configPath: resolvePortConfigPath({ packaged: app.isPackaged, resourcesPath: process.resourcesPath, dirname: __dirname }),
});
const services = {
  api: { ...portRegistry.services.core, script: "start.ps1" },
  asr: { ...portRegistry.services.asr, script: "start-asr.ps1" },
  tts: { ...portRegistry.services.tts, script: "start-tts.ps1" },
  qwenTts: { ...portRegistry.services.qwen, script: "start-qwen3-tts.ps1" },
};

const captureDashboardArg = process.argv.find((argument) => argument.startsWith("--capture-dashboard="));
const captureArg = process.argv.find((argument) => argument.startsWith("--capture=")) || captureDashboardArg;
const dashboardPreviewArg = process.argv.includes("--dashboard-preview");
const captureCompanionArg = process.argv.find((argument) => argument.startsWith("--capture-companion="));
const captureCompanionBlankArg = process.argv.find((argument) => argument.startsWith("--capture-companion-blank="));
const captureAnnouncement = process.argv.includes("--capture-announcement");
let launcherWindow;
let productWindow;
let companionWindow;
let tray;
let quitting = false;
let componentManager;
let runtimeManager;
let credentialStore;
let serviceSupervisor;
let settingsController;
let productWindows;
let updateController;
let layout;
let storageMigration = { active: false, progress: 0, message: "", error: "" };
let modelPathCheck = { checked: false, moved: [], conflicts: [] };
let workspace = { ready: false, created: false, message: "正在准备用户工作区", error: "" };
let qwenPreflightCache = { expiresAt: 0, value: { eligible: false, code: "CHECKING", message: "正在检查 Qwen3 运行条件…" } };
let qwenPreflightTask = null;
let ttsTransition = { state: "idle", target: "", error: "", startedAt: "" };
let ttsTransitionTask = null;

function recordStabilityEvent(kind, details = {}) {
  try {
    const target = path.join(app.getPath("userData"), "mindspace-stability.log");
    fs.appendFileSync(target, `${JSON.stringify({
      timestamp: new Date().toISOString(),
      kind,
      ...details,
    })}\n`, "utf8");
  } catch {
    // A diagnostic write must never become a second crash source.
  }
}


let voiceBackgroundTask = null;
let voiceBackgroundState = { state: "idle", currentId: "", currentName: "", message: "", error: "" };
let voiceBackgroundGeneration = 0;
let observedTtsProvider = "";
let ttsProviderReconcileTask = null;
let shutdownTask = null;
let finalExit = false;
let companionLoadError = "";
let companionBoundsTimer = null;
const COMPANION_RELEASE = Object.freeze({
  available: false,
  targetVersion: "1.0",
  message: "Live2D 桌宠将在 V1.0 正式版开放",
});

function readJson(file, fallback = null) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return fallback; }
}

function currentLayout() {
  if (!layout) layout = ensureAppPaths(appPaths(app));
  return layout;
}

function serviceIdentityRoot() {
  return path.join(currentLayout().state, "services");
}

function serviceIdentityFile(name) {
  return path.join(serviceIdentityRoot(), `${name}.json`);
}

function writeServiceIdentity(name, child, executable, script) {
  writeJsonAtomic(serviceIdentityFile(name), {
    schema_version: "1.0.0",
    service: name,
    pid: child.pid,
    port: services[name].port,
    executable: path.resolve(executable),
    script: path.resolve(script),
    core_root: path.resolve(rootPath()),
    started_at: new Date().toISOString(),
    nonce: crypto.randomUUID(),
  });
}

function clearServiceIdentity(name) {
  fs.rmSync(serviceIdentityFile(name), { force: true });
}

function hintedRoot() {
  let hintedRoot = "";
  try { hintedRoot = JSON.parse(fs.readFileSync(path.join(__dirname, "root-hint.json"), "utf8")).root; } catch {}
  return hintedRoot;
}

function readLauncherConfig() {
  if (!app.isReady()) return {};
  const preferred = path.join(currentLayout().data, "launcher.json");
  const legacy = path.join(app.getPath("userData"), "launcher.json");
  return readJson(preferred, readJson(legacy, {}));
}

function writeLauncherConfig(value) {
  fs.mkdirSync(currentLayout().data, { recursive: true });
  fs.writeFileSync(path.join(currentLayout().data, "launcher.json"), JSON.stringify(value, null, 2));
}

function companionConfig() {
  return normalizeCompanionConfig(readLauncherConfig().companion);
}

function writeCompanionConfig(patch = {}) {
  const launcherConfig = readLauncherConfig();
  const companion = normalizeCompanionConfig({ ...launcherConfig.companion, ...patch });
  writeLauncherConfig({ ...launcherConfig, companion });
  return companion;
}

function companionResourcePaths() {
  const renderer = path.join(__dirname, "assets", "companion-renderer", "index.html");
  const modelRoot = path.join(__dirname, "assets", "companion-renderer", "Resources", "mindspace-companion-v24");
  const model = path.join(modelRoot, "mindspace-companion-v24.model3.json");
  return { renderer, modelRoot, model };
}

function companionSnapshot() {
  const config = companionConfig();
  return {
    ...config,
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

function scheduleCompanionBoundsSave() {
  if (!companionWindow || companionWindow.isDestroyed()) return;
  clearTimeout(companionBoundsTimer);
  companionBoundsTimer = setTimeout(() => {
    if (!companionWindow || companionWindow.isDestroyed()) return;
    writeCompanionConfig(companionWindow.getBounds());
  }, 250);
}

function createCompanionWindow({ blank = false } = {}) {
  if (companionWindow && !companionWindow.isDestroyed()) return companionWindow;
  const config = companionConfig();
  const remembered = config.x === null || config.y === null
    ? screen.getPrimaryDisplay()
    : screen.getDisplayMatching({ x: config.x, y: config.y, width: config.width, height: config.height });
  const bounds = companionBoundsForDisplay(config, remembered.workArea);
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
  companionWindow.setIgnoreMouseEvents(config.clickThrough, { forward: true });
  companionWindow.on("move", scheduleCompanionBoundsSave);
  companionWindow.on("resize", scheduleCompanionBoundsSave);
  companionWindow.on("closed", () => { companionWindow = undefined; });
  companionWindow.webContents.on("did-fail-load", (_event, code, description) => {
    companionLoadError = `Live2D 加载失败：${code} ${description}`;
    companionWindow?.hide();
  });
  if (blank) companionWindow.loadURL("about:blank");
  else companionWindow.loadFile(companionResourcePaths().renderer);
  return companionWindow;
}

function syncCompanionVisibility() {
  if (captureArg || quitting) return companionSnapshot();
  const snapshot = companionSnapshot();
  if (!snapshot.enabled || !snapshot.ready) {
    companionWindow?.hide();
    return companionSnapshot();
  }
  const win = createCompanionWindow();
  win.setIgnoreMouseEvents(snapshot.clickThrough, { forward: true });
  win.showInactive();
  return companionSnapshot();
}

function companionAction(action) {
  if (action !== "snapshot") {
    return { ...companionSnapshot(), ok: false, error: COMPANION_RELEASE.message };
  }
  return { ...companionSnapshot(), ok: true };
}

function downloadSource() {
  return readLauncherConfig().downloadSource === "official" ? "official" : "china";
}

function applyProcessProxy(proxy) {
  for (const key of ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]) {
    if (proxy) process.env[key] = proxy;
    else if (originalProxyEnvironment[key]) process.env[key] = originalProxyEnvironment[key];
    else delete process.env[key];
  }
}

async function synchronizeRuntimeProxy() {
  const configured = String(readLauncherConfig().runtimeProxy || "").trim();
  if (configured) {
    await session.defaultSession.setProxy({ proxyRules: configured });
    applyProcessProxy(configured);
    return configured;
  }
  await session.defaultSession.setProxy({ mode: "system" });
  const resolved = await session.defaultSession.resolveProxy("https://pypi.org/simple/");
  const match = /(?:PROXY|HTTPS|SOCKS5?)\s+([^;\s]+)/i.exec(resolved || "");
  const proxy = match ? `${/^SOCKS/i.test(resolved) ? "socks5" : "http"}://${match[1]}` : "";
  applyProcessProxy(proxy);
  return proxy;
}

function rootPath() {
  if (app.isPackaged) return currentLayout().core;
  const configuredRoot = String(readLauncherConfig().root || "");
  const configuredDrive = configuredRoot ? path.parse(configuredRoot).root : "";
  // A development Launcher must use the checkout it was launched from. The
  // shared launcher.json may point at an installed Core that deliberately has
  // no source-tree .venv; mixing those two layouts makes Core exit immediately.
  const developmentRoot = path.resolve(__dirname, "..");
  return resolveWorkspaceRoot({
    app,
    configuredRoot: configuredRoot && fs.existsSync(configuredDrive) ? configuredRoot : "",
    environmentRoot: process.env.MINDSPACE_ROOT || developmentRoot,
    hintedRoot: hintedRoot(),
    dirname: __dirname,
  });
}

function persistRoot(root) {
  if (app.isPackaged) return;
  writeLauncherConfig({ ...readLauncherConfig(), root });
}

async function initializeWorkspace(root = rootPath()) {
  try {
    const result = await ensureCoreRoot({
      root,
      archive: bundledArchive(process.resourcesPath, __dirname),
      version: bundledVersion(process.resourcesPath, __dirname),
    });
    persistRoot(root);
    workspace = { ready: true, created: result.created, message: result.message, error: "" };
  } catch (error) {
    workspace = {
      ready: false,
      created: false,
      message: "用户工作区准备失败",
      error: String(error.message || error),
    };
  }
  return workspace;
}

function runtimeDataRoot() {
  return app.isPackaged ? currentLayout().data : path.join(rootPath(), "runtime");
}

function modelRoot() {
  return app.isPackaged ? currentLayout().models : path.join(rootPath(), "assets", "models");
}

function qwenRuntimeRoot() {
  return path.join(currentLayout().home, "environment", "qwen3-vllm");
}

function qwenLauncherCandidates() {
  const configured = process.env.MINDSPACE_QWEN3_WSL_LAUNCHER;
  return [
    configured,
    path.join(qwenRuntimeRoot(), "start-qwen3-tts.sh"),
    path.join(currentLayout().home, "experimental", "vllm-omni", "start-qwen3-tts.sh"),
  ].filter((candidate, index, values) => candidate && values.indexOf(candidate) === index && fs.existsSync(candidate));
}

function runCommand(command, arguments_, timeoutMs) {
  return new Promise((resolve) => {
    const child = spawn(command, arguments_, { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; child.kill(); }, timeoutMs);
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.once("error", (error) => { clearTimeout(timer); resolve({ status: -1, stdout, stderr: `${stderr}${error.message || error}` }); });
    child.once("exit", (status) => { clearTimeout(timer); resolve({ status: timedOut ? -1 : status, stdout, stderr }); });
  });
}

async function refreshQwenRuntimePreflight() {
  if (qwenPreflightTask) return qwenPreflightTask;
  qwenPreflightTask = (async () => {
  const base = runtimeManager?.snapshot() || { system: {} };
  const system = base.system || {};
  const distro = process.env.MINDSPACE_QWEN3_WSL_DISTRO || "MindspaceVLLM";
  const wsl = await runCommand("where.exe", ["wsl.exe"], 3_000);
  const wslExecutable = wsl.status === 0 ? String(wsl.stdout || "").split(/\r?\n/).find(Boolean) : "";
  let installed = [];
  let wslGpuAvailable = false;
  let vramMiB = 0;
  if (wslExecutable) {
    const listed = await runCommand(wslExecutable, ["--list", "--quiet"], 5_000);
    installed = String(listed.stdout || "").split(/\r?\n/).map((value) => value.replace(/\0/g, "").trim()).filter(Boolean);
    if (installed.includes(distro)) {
      const gpu = await runCommand(wslExecutable, ["--distribution", distro, "--", "nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], 6_000);
      const values = String(gpu.stdout || "").match(/\d+/g) || [];
      vramMiB = Math.max(0, ...values.map(Number));
      wslGpuAvailable = gpu.status === 0 && vramMiB > 0;
    }
  }
  const launcherCandidates = qwenLauncherCandidates();
  const marker = path.join(qwenRuntimeRoot(), "ready.json");
  let modelSourceReady = fs.existsSync(marker);
  if (!modelSourceReady && wslExecutable && installed.includes(distro) && launcherCandidates.length) {
    try {
      const launcherText = fs.readFileSync(launcherCandidates[0], "utf8");
      const modelMatch = launcherText.match(/^MODEL=(.+)$/m);
      const model = modelMatch?.[1]?.trim().replace(/^['"]|['"]$/g, "");
      if (model) {
        const checked = await runCommand(
          wslExecutable,
          ["--distribution", distro, "--", "bash", "-lc", 'test -f "$1/config.json" && test -f "$1/model.safetensors"', "mindspace-qwen", model],
          6_000,
        );
        modelSourceReady = checked.status === 0;
      }
    } catch { /* malformed external launcher is treated as not ready */ }
  }
  const qwenHealth = await probe(services.qwenTts);
  const baseResult = evaluateQwenRuntimePreflight({
    system,
    wslAvailable: Boolean(wslExecutable),
    distroAvailable: installed.includes(distro),
    wslGpuAvailable,
    vramMiB,
    port: services.qwenTts.port,
    portConflict: !qwenHealth.online && await isTcpPortOccupied(services.qwenTts.port),
  });
    const value = baseResult.eligible && !modelSourceReady
    ? { eligible: false, code: "QWEN_MODEL_REQUIRED", message: "未发现完整的本地 Qwen3 模型与启动脚本；此安装包不会自动下载 WSL、vLLM 或大模型。" }
    : { ...baseResult, distro, vramMiB, modelReady: modelSourceReady };
    qwenPreflightCache = { expiresAt: Date.now() + 15_000, value };
    return value;
  })();
  try { return await qwenPreflightTask; }
  catch (error) {
    const value = { eligible: false, code: "PREFLIGHT_FAILED", message: `Qwen3 条件检查失败：${String(error.message || error)}` };
    qwenPreflightCache = { expiresAt: Date.now() + 8_000, value };
    return value;
  } finally { qwenPreflightTask = null; }
}

function qwenRuntimePreflight() {
  if (qwenPreflightCache.expiresAt <= Date.now() && !qwenPreflightTask) void refreshQwenRuntimePreflight();
  return qwenPreflightCache.value;
}

function logRoot() {
  return app.isPackaged ? currentLayout().logs : path.join(rootPath(), "runtime", "logs");
}

function redactDiagnosticText(value) {
  return String(value || "")
    .replace(/(authorization["'\s:=]+bearer\s+)[^\s"']+/gi, "$1[REDACTED]")
    .replace(/((?:api[_-]?key|token|password|secret)["'\s:=]+)[^\s,"']+/gi, "$1[REDACTED]")
    .replace(/(https?:\/\/)[^\s/@:]+:[^\s/@]+@/gi, "$1[REDACTED]@");
}

function tailLog(file, maximumLines = 240) {
  try {
    return redactDiagnosticText(fs.readFileSync(file, "utf8").split(/\r?\n/).slice(-maximumLines).join("\n"));
  } catch { return ""; }
}

function createDiagnosticReport() {
  const generatedAt = new Date();
  const folder = path.join(logRoot(), "diagnostics", `mindspace-${generatedAt.toISOString().replace(/[:.]/g, "-")}`);
  fs.mkdirSync(folder, { recursive: true });
  const runtime = unifiedRuntimeSnapshot();
  const report = {
    schema_version: "1.0.0",
    generated_at: generatedAt.toISOString(),
    launcher_version: app.getVersion(),
    packaged: app.isPackaged,
    platform: { platform: process.platform, arch: process.arch, release: runtime.system?.windowsRelease || "" },
    storage: { home: currentLayout().home, free_bytes: runtime.system?.freeBytes || 0, writable: runtime.system?.writable !== false },
    download_source: downloadSource(),
    runtime,
  };
  writeJsonAtomic(path.join(folder, "diagnostic-report.json"), report);
  const diagnosticLogs = new Set([
    "runtime-manager.jsonl",
    "component-download.log",
    "maintenance-verify.log",
    "api.launcher.log",
    "asr.launcher.log",
    "tts.launcher.log",
  ]);
  try {
    for (const entry of fs.readdirSync(logRoot(), { withFileTypes: true })) {
      if (entry.isFile() && /^[a-z0-9._-]+\.install\.log$/i.test(entry.name)) {
        diagnosticLogs.add(entry.name);
      }
    }
  } catch { /* a missing log directory is valid on a fresh install */ }
  for (const name of diagnosticLogs) {
    const content = tailLog(path.join(logRoot(), name));
    if (content) fs.writeFileSync(path.join(folder, name), `${content}\n`, "utf8");
  }
  return folder;
}







function configuredTtsProvider(root) {
  try {
    const settings = JSON.parse(fs.readFileSync(path.join(runtimeDataRoot(), "config", "settings.json"), "utf8"));
    const provider = String(settings?.audio?.tts_provider || "").toLowerCase();
    return ["browser", "cosyvoice", "gpt-sovits", "qwen3-vllm", "siliconflow"].includes(provider)
      ? provider
      : observedTtsProvider || "browser";
  } catch {
    // Atomic settings replacement can briefly make a read unavailable on
    // Windows. Keep the last confirmed provider instead of interpreting that
    // transient gap as a user request to switch engines.
    return observedTtsProvider || "browser";
  }
}

function configuredTtsVoice() {
  const settings = readJson(path.join(runtimeDataRoot(), "config", "settings.json"), {});
  return String(settings?.audio?.tts_gpt_sovits_voice || "v4-changli");
}

function isLocalTtsProvider(provider) {
  return ["cosyvoice", "gpt-sovits", "qwen3-vllm"].includes(String(provider || "").toLowerCase());
}

function ttsServiceName(provider = configuredTtsProvider(rootPath())) {
  return String(provider || "").toLowerCase() === "qwen3-vllm" ? "qwenTts" : "tts";
}

function writeJsonAtomic(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  try {
    fs.renameSync(temporary, file);
  } catch {
    fs.copyFileSync(temporary, file);
    fs.rmSync(temporary, { force: true });
  }
}

function productSettingsFile() {
  return path.join(runtimeDataRoot(), "config", "settings.json");
}

function readProductSettings() {
  return readJson(productSettingsFile(), {});
}

function initializeCredentialStore() {
  credentialStore = createSecretStore({
    file: path.join(currentLayout().state, "secrets", "product-secrets.json"),
    safeStorage,
  });
  const migration = credentialStore.migrateProductConfig(productSettingsFile());
  if (migration.migrated.length) recordStabilityEvent("credentials-migrated", { fields: migration.migrated });
  const launcherConfig = readLauncherConfig();
  if (Object.prototype.hasOwnProperty.call(launcherConfig, "llm")) {
    const { llm: _obsoletePublicSettings, ...withoutLlm } = launcherConfig;
    writeLauncherConfig(withoutLlm);
  }
}

function readCredential(name) {
  try { return credentialStore?.get(name) || ""; } catch { return ""; }
}

function configuredLlm() {
  const settings = readProductSettings();
  return {
    mode: String(settings?.llm?.mode || "openai"),
    base_url: String(settings?.llm?.base_url || LLM_PRESETS.deepseek.baseUrl),
    api_key: String(readCredential("llm_api_key") || ""),
    model: String(settings?.llm?.model || LLM_PRESETS.deepseek.model),
  };
}

function activeSupervisor() {
  if (!serviceSupervisor) throw new Error("服务监督器尚未初始化");
  return serviceSupervisor;
}
function probe(service) { return activeSupervisor().probe(service); }
function qwenSupervisorState() { return activeSupervisor().qwenSupervisorState(); }
function stopExternalQwenSupervisor() { return activeSupervisor().stopExternalQwenSupervisor(); }
function recordServiceEvent(event, details = {}) { return activeSupervisor().recordServiceEvent(event, details); }
function startService(name, recoveryAttempt = false) { return activeSupervisor().startService(name, recoveryAttempt); }
function scheduleStartupHealthRecheck(name, delayMs = 2000) { return activeSupervisor().scheduleStartupHealthRecheck(name, delayMs); }
function serviceEnvironment(extra = {}) { return activeSupervisor().serviceEnvironment(extra); }
function stopService(name) { return activeSupervisor().stopService(name); }
function waitForServiceOffline(name, timeoutMs = 9_000) { return activeSupervisor().waitForServiceOffline(name, timeoutMs); }
function stopServicesForUpdate() { return activeSupervisor().stopServicesForUpdate(() => allServices("stop")); }
function waitForHealth(timeout) { return activeSupervisor().waitForHealth(timeout); }
function isTcpPortOccupied(port, timeoutMs = 350) { return activeSupervisor().isTcpPortOccupied(port, timeoutMs); }
function recoverProductWindow(kind, details = {}) { return productWindows?.recoverProductWindow(kind, details); }
function createWindow() { return productWindows.createLauncherWindow(); }
function openProductWindow() { return productWindows.openProductWindow(); }
function openExternalSafely(rawUrl, parentWindow) { return productWindows.openExternalSafely(rawUrl, parentWindow); }

function initializeServiceSupervisor() {
  serviceSupervisor = createServiceSupervisor({
    app, services, portRegistry, fetch: (...args) => net.fetch(...args), rootPath, currentLayout, runtimeDataRoot, modelRoot,
    qwenRuntimeRoot, logRoot, resolvePowerShell, serviceIdentityRoot, writeServiceIdentity, clearServiceIdentity,
    configuredTtsProvider, configuredTtsVoice, configuredLlm, readCredential, readJson,
    runtimeSnapshot: () => runtimeManager, componentSnapshot: () => componentManager?.snapshot(), qwenRuntimePreflight,
    evaluateHardwareAvailability, gptVoices: GPT_SOVITS_VOICES, environmentForPorts, serviceRestartDelay,
    runCommand,
    isQuitting: () => quitting,
  });
}
function initializeSettingsController() {
  settingsController = createSettingsController({
    fetch: (...args) => net.fetch(...args), coreOrigin: services.api.origin, secretStore: credentialStore,
    isAuthorizedSender: (sender) => productWindows?.isProductSender(sender),
  });
  settingsController.registerIpc(ipcMain);
}
function initializeProductWindows() {
  productWindows = createProductWindows({
    app, BrowserWindow, dialog, shell, services, dirname: __dirname, captureArg, captureDashboardArg,
    dashboardPreviewArg, captureAnnouncement, softwareDesktopRendering, probe, productEntryState,
    recordStabilityEvent, syncCompanionVisibility, isQuitting: () => quitting,
    onLauncherChanged: (value) => { launcherWindow = value; }, onProductChanged: (value) => { productWindow = value; },
  });
}
function initializeUpdateManager() {
  updateController = createUpdateController({
    app, dirname: __dirname, fetch: (...args) => net.fetch(...args), rootPath, resolvePowerShell, currentLayout,
    readConfig: readLauncherConfig, writeConfig: writeLauncherConfig, stopServices: stopServicesForUpdate,
    startServices: () => allServices("start"), waitForHealth,
  });
  updateController.registerIpc(ipcMain);
}
function configuredGptVoiceComponent() {
  const voiceId = configuredTtsVoice();
  return GPT_SOVITS_VOICES.find((voice) => voice.id === voiceId)?.componentId
    || GPT_SOVITS_VOICES.find((voice) => voice.id === "v4-changli")?.componentId
    || "gpt-sovits-v4-changli";
}

function onboardingSnapshot() {
  const launcherConfig = readLauncherConfig();
  const providerPreference = voicePreferenceFromProvider(configuredTtsProvider());
  return deriveOnboardingSnapshot({
    runtime: unifiedRuntimeSnapshot(),
    llm: configuredLlm(),
    launcherConfig: {
      ...launcherConfig,
      onboarding: {
        ...(launcherConfig.onboarding || {}),
        voicePreference: providerPreference,
      },
    },
    componentItems: componentManager?.snapshot().items || [],
    voiceComponentId: configuredGptVoiceComponent(),
    voiceBackground: voiceBackgroundState,
  });
}

function updateOnboardingConfig(patch) {
  const config = readLauncherConfig();
  const onboarding = {
    version: ONBOARDING_VERSION,
    ...(config.onboarding || {}),
    ...patch,
  };
  if (onboardingSnapshot().baseReady && onboardingSnapshot().llmReady && !onboarding.completedAt) {
    onboarding.completedAt = new Date().toISOString();
  }
  writeLauncherConfig({ ...config, onboarding });
  return onboarding;
}

async function patchCoreSettings(patch) {
  const response = await net.fetch(`${services.api.origin}/api/v1/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(String(payload.detail || payload.error || `Core 设置失败（HTTP ${response.status}）`));
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function syncProductSettings(patch) {
  let warning = "";
  try {
    await patchCoreSettings(patch);
  } catch (error) {
    warning = `核心服务未同步：${String(error.message || error)}`;
  }
  return warning;
}

async function applyVoicePreference(preference) {
  const requested = String(preference || "").trim().toLowerCase();
  const selected = requested === "siliconflow" ? "siliconflow" : normalizeVoicePreference(requested);
  const audio = selected === "none"
    ? { tts_provider: "browser", auto_tts: false }
    : {
      tts_provider: selected,
      auto_tts: true,
      ...(selected === "gpt-sovits" ? { tts_gpt_sovits_voice: configuredTtsVoice() } : {}),
    };
  return syncProductSettings({ audio });
}

async function stopTtsProviderService(provider) {
  if (!isLocalTtsProvider(provider)) return;
  const serviceName = ttsServiceName(provider);
  serviceSupervisor.setDesired(serviceName, false);
  if (serviceSupervisor.hasChild(serviceName)) {
    stopService(serviceName);
    await waitForServiceOffline(serviceName);
  } else if (serviceName === "qwenTts") {
    stopExternalQwenSupervisor();
    await waitForServiceOffline(serviceName);
  }
}

async function selectVoiceProvider(preference, { startIfReady = true, requestDownload = true } = {}) {
  const requested = String(preference || "").trim().toLowerCase();
  const selected = requested === "siliconflow" ? "siliconflow" : normalizeVoicePreference(requested);
  if (selected === "qwen3-vllm") {
    const preflight = await refreshQwenRuntimePreflight();
    if (!preflight.eligible) throw new Error(preflight.message);
  }
  if (!["none", "siliconflow", "qwen3-vllm"].includes(selected)) {
    const hardware = evaluateHardwareAvailability(selected, runtimeManager?.snapshot().system || {});
    if (!hardware.eligible) throw new Error(hardware.message);
  }

  const previousProvider = configuredTtsProvider();
  voiceBackgroundGeneration += 1;
  if (voiceBackgroundTask && voiceBackgroundState.currentId) {
    componentManager?.cancel(voiceBackgroundState.currentId);
  }
  const previousService = isLocalTtsProvider(previousProvider) ? ttsServiceName(previousProvider) : "";
  const wasActive = Boolean(
    previousService
    && (
      serviceSupervisor.hasDesired(previousService)
      || serviceSupervisor.hasChild(previousService)
      || (await probe(services[previousService])).online
    )
  );

  updateOnboardingConfig({
    // The first-run wizard only presents local engines. Cloud TTS remains a
    // dashboard/application setting and must not be mistaken for a local plan.
    voicePreference: selected === "siliconflow" ? "none" : selected,
    voiceSelectionConfirmed: true,
    voiceDownloadRequested: requestDownload && isLocalTtsProvider(selected),
    voiceReadyAt: "",
    voiceReadyAcknowledgedAt: ["none", "siliconflow"].includes(selected) ? new Date().toISOString() : "",
  });
  const warning = await applyVoicePreference(selected);
  const nextProvider = selected === "none" ? "browser" : selected;
  observedTtsProvider = nextProvider;

  if (isLocalTtsProvider(previousProvider) && previousProvider !== nextProvider) {
    await stopTtsProviderService(previousProvider);
  }
  if (selected === "none" || selected === "siliconflow") {
    await stopTtsProviderService("gpt-sovits");
    await stopTtsProviderService("qwen3-vllm");
    voiceBackgroundState = {
      state: "idle",
      currentId: "",
      currentName: "",
      message: selected === "siliconflow" ? "已切换为云端声音，不占用本地显存" : "声音已关闭",
      error: "",
    };
    return {
      ok: true,
      warning,
      ready: true,
      started: false,
      onboarding: onboardingSnapshot(),
      ...ttsVoiceSnapshot(),
    };
  }

  const ready = voicePlanReady(selected);
  if (!ready) {
    voiceBackgroundState = {
      state: requestDownload ? "queued" : "idle",
      currentId: "",
      currentName: "",
      message: requestDownload ? "新声音已设为当前，正在等待后台安装" : "已记录声音选择，基础环境完成后再下载",
      error: "",
    };
    if (requestDownload) scheduleVoiceBackgroundDownload();
    return {
      ok: true,
      warning,
      ready: false,
      queued: requestDownload,
      started: false,
      onboarding: onboardingSnapshot(),
      ...ttsVoiceSnapshot(),
    };
  }

  updateOnboardingConfig({
    voiceReadyAt: new Date().toISOString(),
    voiceReadyAcknowledgedAt: new Date().toISOString(),
  });
  if (!startIfReady && !wasActive) {
    return {
      ok: true,
      warning,
      ready: true,
      started: false,
      onboarding: onboardingSnapshot(),
      ...ttsVoiceSnapshot(),
    };
  }

  const targetService = ttsServiceName(selected);
  // GPT-SoVITS and CosyVoice share the registered TTS port but run different workers. A
  // provider change on the same service name still requires a clean restart.
  if (previousProvider !== selected && serviceSupervisor.hasChild(targetService)) {
    stopService(targetService);
    await waitForServiceOffline(targetService);
  }
  serviceSupervisor.setDesired(targetService);
  const started = await ensureSelectedTtsService();
  return {
    ok: started.ok,
    error: started.error,
    warning,
    ready: true,
    started: started.ok,
    onboarding: onboardingSnapshot(),
    ...ttsVoiceSnapshot(),
  };
}

function voicePlanForPreference(preference) {
  return voiceInstallPlan(preference, configuredGptVoiceComponent());
}

function voicePlanItems(preference) {
  const ids = new Set(voicePlanForPreference(preference));
  return (componentManager?.snapshot().items || []).filter((item) => ids.has(item.id));
}

function voicePlanReady(preference) {
  const selected = normalizeVoicePreference(preference);
  if (selected === "none") return true;
  const plan = voicePlanForPreference(selected);
  const items = voicePlanItems(selected);
  return plan.length > 0 && items.length === plan.length && items.every((item) => item.ready);
}

function scheduleVoiceBackgroundDownload() {
  if (voiceBackgroundTask || !runtimeManager || !componentManager) return voiceBackgroundTask;
  const config = readLauncherConfig();
  const onboarding = config.onboarding || {};
  const preference = normalizeVoicePreference(onboarding.voicePreference);
  if (!onboarding.voiceDownloadRequested || preference === "none") return null;
  if (!unifiedRuntimeSnapshot().ready) {
    voiceBackgroundState = {
      state: "queued",
      currentId: "",
      currentName: "",
      message: "等待基础环境完成后自动继续",
      error: "",
    };
    return null;
  }
  if (voicePlanReady(preference)) {
    voiceBackgroundState = {
      state: "ready",
      currentId: "",
      currentName: "",
      message: "声音组件已就绪",
      error: "",
    };
    return null;
  }

  const plan = voicePlanForPreference(preference);
  const generation = voiceBackgroundGeneration;
  const task = (async () => {
    try {
      voiceBackgroundState = {
        state: "downloading",
        currentId: "",
        currentName: "",
        message: "声音组件已进入后台下载队列",
        error: "",
      };
      for (const id of plan) {
        if (generation !== voiceBackgroundGeneration) return;
        const current = componentManager.snapshot().items.find((item) => item.id === id);
        if (!current || current.ready) continue;
        if (id === "qwen3-vllm-runtime") {
          const preflight = await refreshQwenRuntimePreflight();
          if (!preflight.eligible) throw new Error(preflight.message);
        }
        const hardware = evaluateHardwareAvailability(id, runtimeManager.snapshot().system);
        if (!hardware.eligible) throw new Error(hardware.message);
        voiceBackgroundState = {
          state: "downloading",
          currentId: id,
          currentName: current.name,
          message: `正在后台准备 ${current.name}`,
          error: "",
        };
        await componentManager.download(id);
      }
      if (generation !== voiceBackgroundGeneration) return;
      if (!voicePlanReady(preference)) throw new Error("声音组件安装结束，但完整性检查尚未通过");
      const warning = await applyVoicePreference(preference);
      updateOnboardingConfig({
        voiceReadyAt: new Date().toISOString(),
        voiceReadyAcknowledgedAt: "",
      });
      voiceBackgroundState = {
        state: "ready",
        currentId: "",
        currentName: "",
        message: warning || "声音组件已就绪；文字对话无需等待",
        error: "",
      };
    } catch (error) {
      if (generation !== voiceBackgroundGeneration) return;
      voiceBackgroundState = {
        state: "error",
        currentId: voiceBackgroundState.currentId,
        currentName: voiceBackgroundState.currentName,
        message: "声音组件后台安装未完成；文字对话不受影响",
        error: String(error.message || error),
      };
    } finally {
      if (voiceBackgroundTask === task) voiceBackgroundTask = null;
      if (generation !== voiceBackgroundGeneration) {
        setTimeout(() => { scheduleVoiceBackgroundDownload(); }, 0).unref?.();
      }
    }
  })();
  voiceBackgroundTask = task;
  return voiceBackgroundTask;
}

function normalizeLlmInput(payload = {}) {
  const current = configuredLlm();
  const baseUrl = String(payload.baseUrl || current.base_url || "").trim().replace(/\/+$/, "");
  const model = String(payload.model || current.model || "").trim();
  const apiKey = String(payload.apiKey || current.api_key || "").trim();
  if (!/^https?:\/\//i.test(baseUrl)) throw new Error("API 地址必须以 http:// 或 https:// 开头");
  if (!model) throw new Error("模型名称不能为空");
  if (!apiKey && !isLoopbackUrl(baseUrl)) throw new Error("远程模型服务需要 API Key");
  return { mode: "openai", base_url: baseUrl, model, api_key: apiKey };
}

async function testLlmConfiguration(payload = {}) {
  const llm = normalizeLlmInput(payload);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20_000);
  try {
    const response = await net.fetch(`${llm.base_url}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(llm.api_key ? { Authorization: `Bearer ${llm.api_key}` } : {}),
      },
      body: JSON.stringify({
        model: llm.model,
        messages: [{ role: "user", content: "请只回复：好" }],
        max_tokens: 2,
        stream: false,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const codes = {
        401: "API Key 无效或没有权限",
        402: "账户余额不足",
        404: "API 地址或模型名称不存在",
        429: "服务请求过于频繁，请稍后重试",
      };
      throw new Error(codes[response.status] || `模型服务返回 HTTP ${response.status}`);
    }
    return { ok: true, llm };
  } catch (error) {
    if (controller.signal.aborted) throw new Error("连接模型服务超时，请检查网络或 API 地址");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function saveLlmConfiguration(payload = {}) {
  const tested = await testLlmConfiguration(payload);
  const llm = tested.llm;
  const saved = await settingsController.save({ llm });
  if (!saved.ok) throw new Error(saved.error || "设置保存失败");
  const current = onboardingSnapshot();
  updateOnboardingConfig({
    llmConfiguredAt: new Date().toISOString(),
    ...(current.baseReady ? { completedAt: new Date().toISOString() } : {}),
  });
  return { ok: true, warning: "", onboarding: onboardingSnapshot() };
}

function ttsVoiceSnapshot() {
  const current = configuredTtsVoice();
  const components = componentManager?.snapshot().items || [];
  return {
    provider: configuredTtsProvider(rootPath()),
    current,
    items: GPT_SOVITS_VOICES.map((voice) => {
      const component = components.find((candidate) => candidate.id === voice.componentId);
      return {
        ...voice,
        ready: Boolean(component?.ready),
        status: component?.status || "idle",
        progress: component?.progress || 0,
        downloadedBytes: component?.downloadedBytes || 0,
        totalBytes: component?.totalBytes || voice.estimatedBytes || 0,
        speedBps: component?.speedBps || 0,
        message: component?.message || "",
        error: component?.error || "",
      };
    }),
  };
}

function modelStatus(root, ttsProvider = configuredTtsProvider(root)) {
  const models = modelRoot();
  const cloudTts = ttsProvider !== "cosyvoice";
  const catalog = [
    ["embedding", "中文向量模型", "shibing624/text2vec-base-chinese", ["config.json", "pytorch_model.bin"], 0],
    ["tts", cloudTts ? "本地 CosyVoice（可选）" : "CosyVoice 3", "tts/Fun-CosyVoice3-0.5B-2512", ["cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt"], 0],
    ["asr", "Paraformer Streaming", "asr/paraformer-zh-streaming", ["config.yaml", "model.pt"], 800000000],
    ["asr-final", "Fun-ASR Nano 2512", "asr/Fun-ASR-Nano-2512", ["config.yaml", "model.pt", "Qwen3-0.6B/config.json", "Qwen3-0.6B/tokenizer.json"], 2000000000],
    ["vad", "FSMN VAD", "asr/fsmn-vad", ["config.yaml", "model.pt"], 1000000],
    ["punc", "CT Punctuation", "asr/ct-punc", ["config.yaml", "model.pt"], 10000000],
  ];
  return catalog.map(([id, name, relative, required, minimumWeightBytes]) => {
    const location = path.join(models, relative);
    const missing = required.filter((file) => !fs.existsSync(path.join(location, file)));
    const weight = path.join(location, "model.pt");
    if (minimumWeightBytes && fs.existsSync(weight) && fs.statSync(weight).size < minimumWeightBytes) {
      missing.push("model.pt（下载未完成）");
    }
    const asrVenv = app.isPackaged ? path.join(currentLayout().venvs, "asr-cuda") : path.join(root, ".venv-asr");
    if (id === "asr" && (
      !fs.existsSync(path.join(asrVenv, "Scripts", "python.exe"))
      || !fs.existsSync(path.join(asrVenv, ".mindspace-asr-ready.json"))
    )) {
      missing.push("ASR CUDA 运行时");
    }
    return { id, name, path: location, ready: id === "tts" && cloudTts ? true : missing.length === 0, optional: id === "tts" && cloudTts, missing: id === "tts" && cloudTts ? [] : missing };
  });
}

async function snapshot() {
  const root = rootPath();
  const ps7 = resolvePowerShell();
  const ttsProvider = configuredTtsProvider(root);
  const selectedTtsService = ttsServiceName(ttsProvider);
  const entries = await Promise.all([
    ["api", services.api],
    ["asr", services.asr],
  ].map(async ([name, service]) => [name, await probe(service)]));
  let ttsReport = !isLocalTtsProvider(ttsProvider)
    ? { online: true, detail: { provider: ttsProvider, remote: ttsProvider === "siliconflow", message: ttsProvider === "siliconflow" ? "使用云端 TTS API" : "无需本地 TTS Worker" } }
    : await probe(services[selectedTtsService]);
  // vLLM needs a noticeable model-load/JIT warm-up before its health endpoint
  // listens. A managed child is therefore a real intermediate state, not an
  // offline service. Keep the stable UI key `tts` while exposing that state.
  if (selectedTtsService === "qwenTts" && !ttsReport.online) {
    const child = serviceSupervisor.child("qwenTts");
    const managed = Boolean(child && child.exitCode === null && !child.killed);
    const external = managed ? { running: false, pid: "" } : await qwenSupervisorState();
    if (managed || external.running) {
      const startedAt = serviceSupervisor.launchTime("qwenTts") || Date.now();
      ttsReport = {
        ...ttsReport,
        starting: true,
        detail: { ...ttsReport.detail, provider: "qwen3-vllm", phase: "model_loading", managed, supervisor_pid: external.pid, started_at: new Date(startedAt).toISOString(), elapsed_ms: Date.now() - startedAt },
      };
    }
  }
  entries.push(["tts", ttsReport]);
  const reports = Object.fromEntries(entries);
  const storageAlignment = inspectStorageAlignment(app, process.env, currentLayout().home);
  return {
    root, home: currentLayout().home, workspace, ps7, ps7Ready: Boolean(ps7), ttsProvider,
    storage: { ...storageMigration, ...storageAlignment, modelPathCheck },
    services: reports, models: modelStatus(root, ttsProvider),
    components: componentManager?.snapshot() || { active: "", items: [] },
    voices: ttsVoiceSnapshot(),
    runtime: unifiedRuntimeSnapshot(),
    onboarding: onboardingSnapshot(),
  };
}






async function allServices(action) {
  if (action === "start") {
    let current = await snapshot();
    const expectedVersion = bundledVersion(process.resourcesPath, __dirname);
    if (current.services.api?.online && isStaleCore(current.services.api.detail, expectedVersion)) {
      stopServicesForUpdate();
      await new Promise((resolve) => setTimeout(resolve, 500));
      current = await snapshot();
    }
    const started = [];
    const warnings = [];
    for (const logicalName of SERVICE_START_ORDER) {
      if (logicalName === "tts") {
        if (!isLocalTtsProvider(current.ttsProvider)) {
          for (const name of ["tts", "qwenTts"]) {
            serviceSupervisor.setDesired(name, false);
            if (serviceSupervisor.hasChild(name)) stopService(name);
          }
          continue;
        }
        const result = await ensureSelectedTtsService();
        if (!result.ok) warnings.push(result.error || "TTS 未启动");
        else if (!result.alreadyRunning) started.push(logicalName);
        continue;
      }
      const name = logicalName;
      // Always pass through startService, even when the first snapshot reports
      // an occupied port. This records the desired state and lets the delayed
      // recheck recover when a previous Launcher process is still dying.
      const result = await startService(name);
      if (!result.ok) {
        if (isFatalStartFailure(name)) return result;
        warnings.push(result.error || `${name} 未启动`);
        continue;
      }
      if (!result.alreadyRunning) started.push(logicalName);
      scheduleStartupHealthRecheck(name);
    }
    return { ok: true, started, warnings };
  }
  if (action === "stop") {
    serviceSupervisor.clearDesired();
    for (const name of new Set([...children.keys(), ttsServiceName()])) stopService(name);
    ttsTransition = { state: "idle", target: "", error: "", startedAt: "" };
    return { ok: true };
  }
  return { ok: false, error: "未知批量操作" };
}


async function ensureSelectedTtsService() {
  if (ttsTransitionTask) return ttsTransitionTask;
  const target = ttsServiceName();
  const inactive = target === "qwenTts" ? "tts" : "qwenTts";
  ttsTransitionTask = (async () => {
    ttsTransition = { state: "stopping", target, error: "", startedAt: new Date().toISOString() };
    serviceSupervisor.setDesired(inactive, false);
    const inactiveChild = serviceSupervisor.child(inactive);
    if (inactiveChild) {
      stopService(inactive);
      if (!(await waitForServiceOffline(inactive))) {
        const error = "旧 TTS 引擎未在 9 秒内退出；为避免两个本地模型同时占用显存，已取消切换。";
        ttsTransition = { state: "failed", target, error, startedAt: ttsTransition.startedAt };
        return { ok: false, error };
      }
    } else if ((await probe(services[inactive])).online) {
      const error = "检测到旧 TTS 引擎不是由当前 Launcher 启动。为避免误杀或双占显存，请先在原启动器中关闭它后再切换。";
      ttsTransition = { state: "failed", target, error, startedAt: ttsTransition.startedAt };
      return { ok: false, error };
    }
    if (quitting) return { ok: false, cancelled: true, error: "应用正在退出，已取消 TTS 切换" };
    ttsTransition = { state: "starting", target, error: "", startedAt: ttsTransition.startedAt };
    const result = await startService(target);
    if (result.ok) scheduleStartupHealthRecheck(target);
    ttsTransition = result.ok
      ? { state: "ready", target, error: "", startedAt: ttsTransition.startedAt }
      : { state: "failed", target, error: result.error || "TTS 启动失败", startedAt: ttsTransition.startedAt };
    return result;
  })();
  try { return await ttsTransitionTask; }
  finally { ttsTransitionTask = null; }
}

async function reconcileSelectedTts() {
  if (quitting || ttsProviderReconcileTask) return ttsProviderReconcileTask;
  const task = (async () => {
    const provider = configuredTtsProvider();
    if (!observedTtsProvider) observedTtsProvider = provider;

    if (provider !== observedTtsProvider) {
      const previousProvider = observedTtsProvider;
      observedTtsProvider = provider;
      const preference = voicePreferenceFromProvider(provider);
      const ready = preference === "none" || voicePlanReady(preference);
      voiceBackgroundGeneration += 1;
      if (voiceBackgroundTask && voiceBackgroundState.currentId) {
        componentManager?.cancel(voiceBackgroundState.currentId);
      }
      updateOnboardingConfig({
        voicePreference: preference,
        voiceSelectionConfirmed: true,
        voiceDownloadRequested: preference !== "none" && !ready,
        voiceReadyAt: ready && preference !== "none" ? new Date().toISOString() : "",
        voiceReadyAcknowledgedAt: ready ? new Date().toISOString() : "",
      });

      if (isLocalTtsProvider(previousProvider)) {
        await stopTtsProviderService(previousProvider);
      }
      if (!isLocalTtsProvider(provider)) {
        voiceBackgroundState = { state: "idle", currentId: "", currentName: "", message: "当前未使用本地 TTS", error: "" };
        return { ok: true, local: false };
      }
      if (!ready) {
        voiceBackgroundState = { state: "queued", currentId: "", currentName: "", message: "应用内已切换声音，正在等待启动器补齐组件", error: "" };
        scheduleVoiceBackgroundDownload();
        return { ok: true, queued: true };
      }

      const selectedService = ttsServiceName(provider);
      serviceSupervisor.setDesired(selectedService);
      const switched = await ensureSelectedTtsService();
      if (!switched.ok && !switched.cancelled) {
        recordServiceEvent("service.tts_provider_switch_failed", {
          previous_provider: previousProvider,
          provider,
          service: selectedService,
          error: switched.error || "unknown",
        });
      }
      return switched;
    }

    // Stable selection: only recover a worker that the user explicitly
    // started. Core startup alone never opts a GPU-heavy TTS into memory.
    if (!isLocalTtsProvider(provider)) return { ok: true, local: false };
    const selected = ttsServiceName(provider);
    if (!serviceSupervisor.hasDesired(selected)) return { ok: true, idle: true };
    if (!serviceSupervisor.hasChild(selected)) {
      const health = await probe(services[selected]);
      if (health.online) return { ok: true, alreadyRunning: true };
    }
    if (ttsTransition.state === "failed" && ttsTransition.target === selected) {
      const failedAt = Date.parse(ttsTransition.startedAt || "") || Date.now();
      if (Date.now() - failedAt < 20_000) return { ok: false, coolingDown: true };
    }
    const result = await ensureSelectedTtsService();
    if (!result.ok && !result.cancelled) {
      recordServiceEvent("service.tts_provider_switch_failed", {
        provider,
        service: selected,
        error: result.error || "unknown",
      });
    }
    return result;
  })();
  ttsProviderReconcileTask = task;
  try {
    return await task;
  } finally {
    if (ttsProviderReconcileTask === task) ttsProviderReconcileTask = null;
  }
}



async function startDefaultCore() {
  if (quitting || captureArg || !workspace.ready) return { ok: false, skipped: true };
  if (app.isPackaged && !runtimeManager?.snapshot().ready) {
    return { ok: false, skipped: true, error: "基础运行环境尚未完成" };
  }
  const result = await startService("api");
  if (result.ok) scheduleStartupHealthRecheck("api", 2500);
  else recordServiceEvent("service.default_core_start_failed", { error: result.error || "unknown" });
  return result;
}

function installComponent(component, signal, onProgress) {
  return new Promise((resolve, reject) => {
    const root = rootPath();
    const ps7 = resolvePowerShell();
    const script = path.join(root, component.installScript || "");
    const runtimeName = component.id === "tts-runtime" ? "CosyVoice" : component.id === "gpt-sovits-runtime" ? "GPT-SoVITS" : component.id === "qwen3-vllm-runtime" ? "Qwen3 实时语音" : "ASR";
    if (!ps7) return reject(new Error(`未找到 PowerShell 7，无法安装 ${runtimeName} 运行时`));
    if (!component.installScript || !fs.existsSync(script)) {
      return reject(new Error(`缺少运行时安装脚本：${component.installScript || "未配置"}`));
    }
    const logs = logRoot();
    fs.mkdirSync(logs, { recursive: true });
    const log = fs.createWriteStream(path.join(logs, `${component.id}.install.log`), {
      flags: "a",
    });
    const child = spawn(
      ps7,
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, ...(component.installArgs || [])],
      { cwd: root, env: serviceEnvironment(), windowsHide: true, stdio: ["ignore", "pipe", "pipe"] },
    );
    const sourceLabel = downloadSource() === "official" ? "官方源" : "国内镜像";
    const stages = component.id === "tts-runtime" ? {
      preflight: [8, "正在检查并复用现有 ASR/CUDA 环境…"],
      reuse: [72, "现有依赖完整，无需重复下载…"],
      "build-tools": [18, "正在准备 Whisper 构建兼容环境…"],
      torch: [30, "正在校验并复用 CUDA PyTorch…"],
      dependencies: [42, `正在从${sourceLabel}解析缺失的 CosyVoice 依赖…`],
      verify: [88, "正在验证 CosyVoice 与 CUDA…"],
      marker: [96, "正在写入运行时校验凭证…"],
      done: [99, "CosyVoice 运行时安装完成，正在校验…"],
    } : component.id === "gpt-sovits-runtime" ? {
      preflight: [8, "正在检查 ASR CUDA Torch 与公共模型…"],
      venv: [16, "正在创建隔离的 GPT-SoVITS 环境…"],
      torch: [28, "正在链接已验证的 CUDA Torch 文件…"],
      dependencies: [48, `正在从${sourceLabel}安装独立推理依赖…`],
      project: [78, "正在连接 GPT-SoVITS 推理代码…"],
      verify: [92, "正在验证 GPT-SoVITS、CUDA 与声学模型…"],
      marker: [97, "正在写入运行时校验凭证…"],
      done: [99, "GPT-SoVITS 运行时安装完成，正在校验…"],
    } : component.id === "qwen3-vllm-runtime" ? {
      preflight: [8, "正在检查 WSL2、GPU 与受管运行目录…"],
      gpu: [35, "正在验证 WSL2 内 NVIDIA GPU…"],
      model: [58, "正在核验 Qwen3 CustomVoice 模型与 Serena 声线锁定…"],
      verify: [76, "正在核验 Qwen3 模型与启动脚本…"],
      done: [99, "Qwen3 运行时可用；首次后台预热不会阻塞聊天…"],
    } : {
      venv: [5, "正在创建独立 Python 环境…"],
      torch: [12, "正在下载并安装 CUDA 版 PyTorch…"],
      funasr: [68, "正在安装 FunASR 与实时服务依赖…"],
      project: [84, "正在连接 Mindspace ASR 服务…"],
      verify: [94, "正在验证 CUDA 与 FunASR…"],
      done: [99, "ASR 运行时安装完成，正在校验…"],
    };
    const stagePrefix = component.id === "tts-runtime" ? "TTS" : component.id === "gpt-sovits-runtime" ? "GPT_SOVITS" : component.id === "qwen3-vllm-runtime" ? "QWEN3" : "ASR";
    const installerOutput = [];
    const observe = (chunk) => {
      log.write(chunk);
      const text = chunk.toString("utf8");
      installerOutput.push(text);
      if (installerOutput.length > 80) installerOutput.shift();
      for (const [stage, [progress, message]] of Object.entries(stages)) {
        if (text.includes(`${stagePrefix}_STAGE=${stage}`)) onProgress(progress, message);
      }
      if (component.id === "tts-runtime") {
        if (/Resolved\s+\d+\s+packages?/i.test(text)) onProgress(55, "缺失依赖解析完成，正在准备安装…");
        if (/Prepared\s+\d+\s+packages?/i.test(text)) onProgress(74, "依赖包准备完成，正在写入共享环境…");
        if (/Installed\s+\d+\s+packages?/i.test(text)) onProgress(84, "增量依赖安装完成，正在执行兼容验证…");
      }
    };
    child.stdout.on("data", observe);
    child.stderr.on("data", observe);
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", cancel);
      log.end();
      if (error) reject(error); else resolve();
    };
    const cancel = () => {
      spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
        windowsHide: true,
      });
      finish(new Error("下载已取消"));
    };
    signal.addEventListener("abort", cancel, { once: true });
    child.once("error", (error) => finish(error));
    child.once("exit", (code) => {
      if (signal.aborted) return finish(new Error("下载已取消"));
      if (code !== 0) {
        const output = installerOutput.join("");
        let reason = "请查看运行日志";
        if (/No module named ['\"]pkg_resources['\"]/i.test(output)) reason = "Whisper 构建缺少 pkg_resources";
        else if (/Failed to build `?openai-whisper/i.test(output)) reason = "openai-whisper 构建失败";
        else if (/CUDA is unavailable/i.test(output)) reason = "CUDA 当前不可用";
        else if (/No space left|ENOSPC|磁盘空间不足/i.test(output)) reason = "磁盘空间不足，请更换存储位置或清理空间后重试";
        else if (/directory already exists|UV_VENV_CLEAR=1|already exists at/i.test(output)) reason = "检测到上次中断留下的残缺运行时；再次点击继续时会隔离重建";
        else if (/从阿里云镜像安装失败/i.test(output)) reason = "国内镜像依赖安装失败";
        return finish(new Error(`${runtimeName} 运行时安装失败（退出码 ${code}）：${reason}`));
      }
      return finish();
    });
    onProgress(3, `正在启动 ${runtimeName} 运行时安装器…`);
  });
}

async function finalizeComponent(component, targetRoot) {
  for (const [index, rule] of (component.archives || []).entries()) {
    const source = path.resolve(targetRoot, rule.source);
    const targetBase = path.resolve(targetRoot);
    if (!source.startsWith(`${targetBase}${path.sep}`) || !fs.existsSync(source)) throw new Error(`缺少待解压模型：${rule.source}`);
    const staging = path.join(targetRoot, `.extract-${component.id}-${process.pid}-${index}`);
    fs.rmSync(staging, { recursive: true, force: true });
    fs.mkdirSync(staging, { recursive: true });
    try {
      if (rule.type === "tar.gz" || rule.encoding) {
        const python = app.isPackaged
          ? path.join(currentLayout().venvs, "gpt-sovits", "Scripts", "python.exe")
          : path.join(rootPath(), ".venv-gpt-sovits", "Scripts", "python.exe");
        const helper = path.join(rootPath(), "scripts", "extract-voice-archive.py");
        if (!fs.existsSync(python)) throw new Error("GPT-SoVITS 私有 Python 尚未就绪，无法安全解压人物音色");
        if (!fs.existsSync(helper)) throw new Error("应用缺少人物音色安全解压脚本，请先更新 Mindspace Core");
        await new Promise((resolve, reject) => {
          const output = [];
          const child = spawn(python, [
            helper,
            "--source", source,
            "--destination", staging,
            "--type", rule.type === "tar.gz" ? "tar.gz" : "zip",
            ...(rule.encoding ? ["--encoding", rule.encoding] : []),
          ], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
          child.stdout.on("data", (chunk) => output.push(chunk));
          child.stderr.on("data", (chunk) => output.push(chunk));
          child.once("error", reject);
          child.once("exit", (code) => code === 0
            ? resolve()
            : reject(new Error(`人物音色解压失败（退出码 ${code}）：${Buffer.concat(output).toString("utf8").trim().slice(-800)}`)));
        });
      } else {
        await extractZip(source, { dir: staging });
      }
      const extracted = path.resolve(staging, rule.root || ".");
      const destination = path.resolve(targetRoot, rule.destination || ".");
      const stagingBase = path.resolve(staging);
      const extractedSafe = extracted === stagingBase || extracted.startsWith(`${stagingBase}${path.sep}`);
      const destinationSafe = destination === targetBase || destination.startsWith(`${targetBase}${path.sep}`);
      if (!extractedSafe || !destinationSafe) {
        throw new Error("模型压缩包包含不安全目标路径");
      }
      if (!fs.existsSync(extracted)) throw new Error(`压缩包结构不符合预期：${rule.root}`);
      fs.mkdirSync(destination, { recursive: true });
      fs.cpSync(extracted, destination, { recursive: true, force: true });
      for (const [from, to] of Object.entries(rule.rename || {})) {
        const fromPath = path.resolve(destination, from);
        const toPath = path.resolve(destination, to);
        if (!fromPath.startsWith(`${destination}${path.sep}`) || !toPath.startsWith(`${destination}${path.sep}`)) throw new Error("模型重命名规则不安全");
        if (!fs.existsSync(fromPath)) throw new Error(`压缩包缺少参考音频：${from}`);
        fs.mkdirSync(path.dirname(toPath), { recursive: true });
        fs.copyFileSync(fromPath, toPath);
      }
      if (rule.reference) {
        const referenceRoot = path.resolve(destination, rule.reference.root || ".");
        if (referenceRoot !== destination && !referenceRoot.startsWith(`${destination}${path.sep}`)) throw new Error("参考音频查找规则不安全");
        if (!fs.existsSync(referenceRoot)) throw new Error(`压缩包缺少参考音频目录：${rule.reference.root}`);
        const candidates = [];
        const visit = (folder) => {
          for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
            const item = path.join(folder, entry.name);
            if (entry.isDirectory()) visit(item);
            else if (/\.(wav|mp3|flac|m4a|ogg)$/i.test(entry.name)) candidates.push(item);
          }
        };
        visit(referenceRoot);
        candidates.sort((left, right) => {
          const preferred = String(rule.reference.prefer || "");
          const leftRank = preferred && path.basename(left).startsWith(preferred) ? 0 : 1;
          const rightRank = preferred && path.basename(right).startsWith(preferred) ? 0 : 1;
          return leftRank - rightRank || left.localeCompare(right, "zh-CN");
        });
        if (!candidates.length) throw new Error("压缩包内没有可用的参考音频");
        const referenceTarget = path.resolve(destination, rule.reference.destination || "reference.wav");
        if (!referenceTarget.startsWith(`${destination}${path.sep}`)) throw new Error("参考音频目标路径不安全");
        fs.copyFileSync(candidates[0], referenceTarget);
      }
    } finally {
      fs.rmSync(staging, { recursive: true, force: true });
    }
    if (rule.remove) fs.rmSync(source, { force: true });
  }
}

function componentTarget(component) {
  if (!app.isPackaged) return path.join(rootPath(), component.target);
  const targets = {
    embedding: path.join(currentLayout().models, "shibing624", "text2vec-base-chinese"),
    asr: path.join(currentLayout().models, "asr", "paraformer-zh-streaming"),
    "asr-final": path.join(currentLayout().models, "asr", "Fun-ASR-Nano-2512"),
    vad: path.join(currentLayout().models, "asr", "fsmn-vad"),
    punc: path.join(currentLayout().models, "asr", "ct-punc"),
    "asr-runtime": path.join(currentLayout().venvs, "asr-cuda"),
    tts: path.join(currentLayout().models, "tts", "Fun-CosyVoice3-0.5B-2512"),
    "tts-runtime": path.join(currentLayout().state, "components", "tts-runtime"),
    "gpt-sovits-v4-base": path.join(currentLayout().models, "tts", "gpt-sovits", "runtime", "GPT_SoVITS"),
    "gpt-sovits-ffmpeg": path.join(currentLayout().tools, "ffmpeg", "8.1.2"),
    "gpt-sovits-runtime": path.join(currentLayout().venvs, "gpt-sovits"),
    "qwen3-vllm-runtime": path.join(currentLayout().home, "environment", "qwen3-vllm"),
  };
  if (component.category === "voice" && component.id.startsWith("gpt-sovits-")) {
    return path.join(currentLayout().models, "tts", "gpt-sovits", "runtime");
  }
  return targets[component.id] || path.join(currentLayout().home, component.target);
}

function initializeComponentManager() {
  componentManager = createComponentManager({
    rootPath,
    fetch: (...arguments_) => net.fetch(...arguments_),
    logFile: path.join(logRoot(), "component-download.log"),
    markerRoot: path.join(currentLayout().state, "model-components"),
    managedRoots: () => [
      currentLayout().models,
      currentLayout().environment,
    ],
    resolveTarget: componentTarget,
    getDownloadSource: downloadSource,
    installComponent,
    finalizeComponent,
  });
}

function initializeRuntimeManager() {
  const manifestCandidates = [
    path.join(process.resourcesPath || "", "runtime", "runtime-manifest.json"),
    path.join(__dirname, "assets", "runtime-manifest.json"),
  ];
  runtimeManager = createRuntimeManager({
    paths: currentLayout(),
    corePath: rootPath,
    manifestPath: manifestCandidates.find((candidate) => fs.existsSync(candidate)) || manifestCandidates[0],
    publicKeyPath: path.join(__dirname, "assets", "update-public-key.pem"),
    bundledRoot: app.isPackaged
      ? path.join(process.resourcesPath, "runtime", "bundled")
      : path.join(__dirname, "bootstrap", "runtime-bundle"),
    fetch: (...arguments_) => net.fetch(...arguments_),
    extract: extractZip,
    getDownloadSource: downloadSource,
  });
}

function unifiedRuntimeSnapshot() {
  const base = runtimeManager?.snapshot() || { active: "", ready: false, system: {}, items: [] };
  const models = componentManager?.snapshot() || { active: "", items: [] };
  const qwenPreflight = qwenRuntimePreflight();
  const modelItems = models.items.map((item) => {
    const hardware = evaluateHardwareAvailability(item.id, base.system);
    const available = item.id === "qwen3-vllm-runtime" ? qwenPreflight.eligible : hardware.eligible;
    return {
      ...item,
      category: item.category || (item.id === "embedding" ? "base" : "voice"),
      kind: item.provider === "installer" ? "environment" : "model",
      required: !item.optional,
      hardwareAvailable: available,
      unavailableReason: item.id === "qwen3-vllm-runtime" ? qwenPreflight.message : hardware.message,
      preflightCode: item.id === "qwen3-vllm-runtime" ? qwenPreflight.code : hardware.code,
    };
  });
  const items = [...base.items, ...modelItems];
  const requiredItems = items.filter((item) => item.required);
  const failed = requiredItems.find((item) => item.status === "error");
  const running = items.find((item) => item.id === (base.active || models.active));
  const completed = requiredItems.filter((item) => item.ready).length;
  return {
    ...base,
    downloadSource: downloadSource(),
    active: base.active || models.active,
    ready: base.ready && modelItems.filter((item) => item.required).every((item) => item.ready),
    items,
    qwenPreflight,
    ttsTransition,
    pipeline: {
      status: failed ? "error" : running ? "running" : completed === requiredItems.length ? "ready" : "idle",
      currentId: running?.id || failed?.id || requiredItems.find((item) => !item.ready)?.id || "",
      currentName: running?.name || failed?.name || requiredItems.find((item) => !item.ready)?.name || "",
      completed,
      total: requiredItems.length,
      progress: requiredItems.length ? requiredItems.reduce((sum, item) => sum + (item.ready ? 100 : item.progress || 0), 0) / requiredItems.length : 100,
      operationId: running?.operationId || failed?.operationId || "",
      errorCode: failed?.errorCode || "",
      error: failed?.error || "",
    },
  };
}

async function runtimeAction(action, id = "") {
  if (!runtimeManager || !componentManager) throw new Error("运行时管理器尚未就绪");
  await synchronizeRuntimeProxy();
  const baseComponent = runtimeManager.componentFor(id);
  const modelComponent = componentManager.snapshot().items.find((item) => item.id === id);
  if (action === "snapshot") return unifiedRuntimeSnapshot();
  if (action === "cancel") {
    runtimeManager.cancel(id);
    componentManager.cancel(id);
    return unifiedRuntimeSnapshot();
  }
  if (action === "install-all") {
    stopServicesForUpdate();
    await runtimeManager.installAll();
    await componentManager.downloadAll();
    updateOnboardingConfig({ baseInstalledAt: new Date().toISOString() });
    scheduleVoiceBackgroundDownload();
    return unifiedRuntimeSnapshot();
  }
  if (action === "repair") {
    stopServicesForUpdate();
    await runtimeManager.repair();
    await componentManager.downloadAll();
    return unifiedRuntimeSnapshot();
  }
  if (action === "remove") {
    if (!modelComponent) throw new Error("只有可选模型与语音组件可以卸载");
    if (["asr-runtime", "asr", "asr-final", "vad", "punc"].includes(id)) stopService("asr");
    if (id === "qwen3-vllm-runtime") stopService("qwenTts");
    if (id === "tts" || id === "tts-runtime" || id.startsWith("gpt-sovits-")) stopService("tts");
    await componentManager.remove(id);
    return unifiedRuntimeSnapshot();
  }
  if (["install", "retry"].includes(action)) {
    if (baseComponent) {
      if (["python", "core-venv"].includes(id)) stopServicesForUpdate();
      await runtimeManager.install(id);
    }
    else if (modelComponent) {
      if (id === "qwen3-vllm-runtime") {
        const preflight = await refreshQwenRuntimePreflight();
        if (!preflight.eligible) throw new Error(preflight.message);
      }
      const hardware = evaluateHardwareAvailability(id, runtimeManager.snapshot().system);
      if (!hardware.eligible) throw new Error(hardware.message);
      await componentManager.download(id);
    } else throw new Error(`未知运行时组件：${id}`);
    return unifiedRuntimeSnapshot();
  }
  throw new Error("未知运行时操作");
}

async function selectTtsVoice(id) {
  const voice = GPT_SOVITS_VOICES.find((candidate) => candidate.id === id);
  if (!voice) throw new Error("未知 GPT-SoVITS 音色");
  if (!runtimeManager?.snapshot().system.nvidia) throw new Error("GPT-SoVITS 本地推理需要兼容的 NVIDIA 显卡与驱动");
  const component = componentManager?.snapshot().items.find((candidate) => candidate.id === voice.componentId);
  if (!component?.ready) throw new Error(`${voice.label} 尚未下载，请先点击“单独下载”`);

  observedTtsProvider = "gpt-sovits";
  updateOnboardingConfig({
    voicePreference: "gpt-sovits",
    voiceSelectionConfirmed: true,
    voiceDownloadRequested: false,
    voiceReadyAt: new Date().toISOString(),
    voiceReadyAcknowledgedAt: new Date().toISOString(),
  });

  const apiWarning = await syncProductSettings({ audio: { tts_provider: "gpt-sovits", tts_gpt_sovits_voice: voice.id } });

  const started = await ensureSelectedTtsService();
  return { ok: started.ok, error: started.error, warning: apiWarning, ...ttsVoiceSnapshot() };
}

async function installTtsVoice(id) {
  const voice = GPT_SOVITS_VOICES.find((candidate) => candidate.id === id);
  if (!voice) throw new Error("未知 GPT-SoVITS 音色");
  if (!runtimeManager?.snapshot().system.nvidia) throw new Error("GPT-SoVITS 本地推理需要兼容的 NVIDIA 显卡与驱动");
  const component = componentManager?.snapshot().items.find((candidate) => candidate.id === voice.componentId);
  await runtimeAction(component?.status === "error" || component?.partial ? "retry" : "install", voice.componentId);
  return ttsVoiceSnapshot();
}

function runMaintenance(action) {
  if (action === "integrity" && app.isPackaged) {
    return { ok: false, error: "源码完整性校验仅能在权威开发源执行，已安装 runtime 不是开发源" };
  }
  const root = rootPath();
  const ps7 = resolvePowerShell();
  if (!ps7) return { ok: false, error: "未找到 PowerShell 7，请先安装或设置 MINDSPACE_PWSH" };
  const commands = {
    verify: ["-File", path.join(root, "scripts", "runtime-verify.ps1")],
    integrity: ["-File", path.join(root, "scripts", "verify-source-integrity.ps1"), "-SourceRoot", root],
    repair: ["-File", path.join(root, "scripts", "repair.ps1")],
    prepareAsr: ["-File", path.join(root, "scripts", "prepare-asr.ps1")],
  };
  if (!commands[action]) return { ok: false, error: "未知维护命令" };
  const logs = logRoot();
  fs.mkdirSync(logs, { recursive: true });
  const out = fs.openSync(path.join(logs, `maintenance-${action}.log`), "a");
  const child = spawn(ps7, ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", ...commands[action]], { cwd: root, env: serviceEnvironment(), windowsHide: true, stdio: ["ignore", out, out] });
  return { ok: true, pid: child.pid, log: path.join(logs, `maintenance-${action}.log`) };
}







function createTray() {
  tray = new Tray(path.join(__dirname, "assets", "mindspace-icon.ico"));
  tray.setToolTip("Mindspace");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "打开 Mindspace", click: () => { void openProductWindow(); } },
    { label: "服务控制中心", click: () => { launcherWindow?.show(); launcherWindow?.focus(); } },
    { label: "Live2D 桌宠（V1.0 开放）", enabled: false },
    { type: "separator" },
    { label: "停止本机服务", click: () => allServices("stop") },
    { label: "退出", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("double-click", () => { launcherWindow?.show(); launcherWindow?.focus(); });
}

async function migrateToStorageTarget(target) {
  if (storageMigration.active) throw new Error("存储迁移正在进行");
  if (runtimeManager?.snapshot().active || componentManager?.snapshot().active) {
    throw new Error("请先等待或取消当前组件安装，再迁移存储位置");
  }
  storageMigration = { active: true, progress: 0, message: "正在准备跨盘迁移", error: "" };
  try {
    await stopServicesForUpdate();
    const migrated = await migrateStorage({
      app,
      sourceHome: currentLayout().home,
      targetHome: target,
      onProgress: (progress, message) => { storageMigration = { active: true, progress, message, error: "" }; },
    });
    storageMigration = { active: false, progress: 100, message: `已迁移到 ${migrated.target}，正在重启验证`, error: "" };
    setTimeout(() => { quitting = true; app.relaunch(); app.exit(0); }, 700);
    return { ...(await snapshot()), storage: { ...storageMigration, ...inspectStorageAlignment(app, process.env, migrated.target) } };
  } catch (error) {
    storageMigration = { active: false, progress: 0, message: "存储迁移失败，原位置保持不变", error: String(error.message || error) };
    throw error;
  }
}

ipcMain.handle("launcher:snapshot", snapshot);
ipcMain.handle("companion:snapshot", () => companionSnapshot());
ipcMain.handle("companion:action", (_event, { action = "snapshot" } = {}) => companionAction(String(action)));
ipcMain.handle("launcher:service", async (_, { service, action }) => {
  const name = service === "tts" ? ttsServiceName() : service;
  if (action === "start") return startService(name);
  if (action === "stop") return stopService(name);
  if (action === "restart") { stopService(name); return startService(name); }
  return { ok: false, error: "未知操作" };
});
ipcMain.handle("launcher:all", (_, action) => allServices(action));
ipcMain.handle("launcher:open", async (_, kind) => {
  const root = rootPath();
  if (kind === "app") {
    return openProductWindow();
  }
  const targets = { logs: logRoot(), models: modelRoot(), root: currentLayout().home };
  const target = targets[kind];
  if (!target) return { ok: false };
  await shell.openPath(target);
  return { ok: true };
});
ipcMain.handle("launcher:external", async (_, rawUrl) => {
  const result = await openExternalSafely(rawUrl, launcherWindow);
  if (!result.ok && !result.cancelled) throw new Error(result.error);
  return result;
});
ipcMain.handle("launcher:maintenance", async (_, action) => {
  if (action === "repair") {
    try { await runtimeAction("repair"); return { ok: true }; }
    catch (error) { return { ok: false, error: String(error.message || error) }; }
  }
  return runMaintenance(action);
});
ipcMain.handle("launcher:select-root", async () => {
  if (app.isPackaged) return snapshot();
  const result = await dialog.showOpenDialog({ properties: ["openDirectory"], defaultPath: rootPath() });
  if (!result.canceled && result.filePaths[0]) {
    await initializeWorkspace(result.filePaths[0]);
    if (workspace.ready) initializeComponentManager();
  }
  return snapshot();
});
ipcMain.handle("launcher:select-storage", async () => {
  const result = await dialog.showOpenDialog({
    title: "选择 Mindspace 存储位置",
    buttonLabel: "迁移到这里",
    properties: ["openDirectory", "createDirectory"],
    defaultPath: path.dirname(currentLayout().home),
  });
  if (result.canceled || !result.filePaths[0]) return snapshot();
  const selected = path.resolve(result.filePaths[0]);
  const selectedName = path.basename(selected).toLowerCase();
  const target = ["mindspace", "mindspacedata"].includes(selectedName) ? selected : path.join(selected, "MindspaceData");
  return migrateToStorageTarget(target);
});
ipcMain.handle("launcher:migrate-recommended-storage", async () => {
  const alignment = inspectStorageAlignment(app, process.env, currentLayout().home);
  if (!alignment.migrationRecommended || !alignment.recommended) return snapshot();
  return migrateToStorageTarget(alignment.recommended);
});
ipcMain.handle("launcher:shortcut", () => {
  const shortcut = path.join(app.getPath("desktop"), "Mindspace.lnk");
  const ok = shell.writeShortcutLink(shortcut, { target: process.execPath, cwd: path.dirname(process.execPath), description: "Mindspace 本地 AI 应用" });
  return { ok, path: shortcut };
});
ipcMain.handle("launcher:component", async (_, { action, id } = {}) => {
  if (!componentManager) throw new Error("组件下载器尚未就绪");
  if (action === "snapshot") return componentManager.snapshot();
  if (action === "download") {
    if (id === "qwen3-vllm-runtime") {
      const preflight = await refreshQwenRuntimePreflight();
      if (!preflight.eligible) throw new Error(preflight.message);
    }
    return componentManager.download(id);
  }
  if (action === "download-all") return componentManager.downloadAll();
  if (action === "cancel") return componentManager.cancel(id);
  if (action === "remove") return runtimeAction("remove", id);
  throw new Error("未知组件操作");
});
ipcMain.handle("launcher:voice", async (_, { action, id } = {}) => {
  if (action === "snapshot") return ttsVoiceSnapshot();
  if (action === "install") return installTtsVoice(id);
  if (action === "select") return selectTtsVoice(id);
  if (action === "provider") return selectVoiceProvider(id, { startIfReady: true, requestDownload: true });
  throw new Error("未知音色操作");
});
ipcMain.handle("launcher:onboarding", async (_, { action, payload = {} } = {}) => {
  if (action === "snapshot") return onboardingSnapshot();
  if (action === "select-voice") {
    const preference = normalizeVoicePreference(payload.preference);
    const before = onboardingSnapshot();
    await selectVoiceProvider(preference, {
      startIfReady: before.complete,
      requestDownload: before.complete,
    });
    return onboardingSnapshot();
  }
  if (action === "install-base") {
    const current = readLauncherConfig().onboarding || {};
    updateOnboardingConfig({
      voiceDownloadRequested: normalizeVoicePreference(current.voicePreference) !== "none",
    });
    await runtimeAction("install-all");
    return onboardingSnapshot();
  }
  if (action === "test-llm") {
    await testLlmConfiguration(payload);
    return { ok: true, message: "模型连接成功" };
  }
  if (action === "save-llm") return saveLlmConfiguration(payload);
  if (action === "retry-voice") {
    updateOnboardingConfig({ voiceDownloadRequested: true, voiceReadyAcknowledgedAt: "" });
    voiceBackgroundState = { state: "queued", currentId: "", currentName: "", message: "正在重新加入后台队列", error: "" };
    scheduleVoiceBackgroundDownload();
    return onboardingSnapshot();
  }
  if (action === "acknowledge-voice") {
    const preference = onboardingSnapshot().voicePreference;
    await applyVoicePreference(preference);
    if (payload.restart) {
      const result = await ensureSelectedTtsService();
      if (!result.ok) throw new Error(result.error || "声音服务启动失败");
    }
    updateOnboardingConfig({ voiceReadyAcknowledgedAt: new Date().toISOString() });
    return { ok: true, onboarding: onboardingSnapshot() };
  }
  if (action === "finish") {
    const current = onboardingSnapshot();
    if (!current.complete) throw new Error("基础环境与 LLM 尚未全部就绪");
    updateOnboardingConfig({ completedAt: new Date().toISOString() });
    return onboardingSnapshot();
  }
  throw new Error("未知首次配置操作");
});
ipcMain.handle("runtime:action", async (_, { action, id } = {}) => runtimeAction(action, id));
ipcMain.handle("runtime:snapshot", async () => runtimeAction("snapshot"));
ipcMain.handle("runtime:install", async (_, { id } = {}) => runtimeAction("install", id));
ipcMain.handle("runtime:cancel", async (_, { id } = {}) => runtimeAction("cancel", id));
ipcMain.handle("runtime:retry", async (_, { id } = {}) => runtimeAction("retry", id));
ipcMain.handle("runtime:repair", async () => runtimeAction("repair"));
ipcMain.handle("runtime:diagnostics", async () => {
  const reportPath = createDiagnosticReport();
  await shell.openPath(reportPath);
  return { ok: true, path: reportPath };
});
ipcMain.handle("runtime:source", async (_, { source = "china" } = {}) => {
  const value = source === "official" ? "official" : source === "china" ? "china" : "";
  if (!value) throw new Error("未知下载源");
  if (unifiedRuntimeSnapshot().active) throw new Error("下载或安装进行中，完成或取消后才能切换下载源");
  writeLauncherConfig({ ...readLauncherConfig(), downloadSource: value });
  return unifiedRuntimeSnapshot();
});
ipcMain.handle("runtime:proxy", async (_, { proxy = "" } = {}) => {
  const value = String(proxy || "").trim();
  if (value && !/^(https?|socks5):\/\//i.test(value)) throw new Error("代理地址必须以 http://、https:// 或 socks5:// 开头");
  writeLauncherConfig({ ...readLauncherConfig(), runtimeProxy: value });
  await synchronizeRuntimeProxy();
  return { ok: true, proxy: value };
});

const singleInstance = captureArg || captureCompanionArg || captureCompanionBlankArg ? true : app.requestSingleInstanceLock();
if (!singleInstance) app.quit();
if (!captureArg) app.on("second-instance", () => { launcherWindow?.show(); launcherWindow?.focus(); });
app.on("render-process-gone", (_event, webContents, details) => {
  if (quitting || details.reason === "clean-exit") return;
  const productGone = Boolean(
    productWindow
    && !productWindow.isDestroyed()
    && productWindow.webContents.id === webContents.id,
  );
  recordStabilityEvent("render-process-gone", {
    product: productGone,
    reason: details.reason,
    exitCode: details.exitCode,
  });
  if (productGone) recoverProductWindow("product-render-process-gone", details);
});
app.on("child-process-gone", (_event, details) => {
  if (quitting || details.reason === "clean-exit") return;
  recordStabilityEvent("child-process-gone", {
    type: details.type,
    reason: details.reason,
    exitCode: details.exitCode,
    serviceName: details.serviceName || "",
    name: details.name || "",
  });
  if (details.type === "GPU" && productWindow && !productWindow.isDestroyed()) {
    recoverProductWindow("gpu-process-gone", details);
  }
});
app.whenReady().then(async () => {
  recordStabilityEvent("desktop-ready", {
    hardwareAcceleration: app.isHardwareAccelerationEnabled(),
    electron: process.versions.electron,
  });
  if (captureCompanionArg || captureCompanionBlankArg) {
    const outputArgument = captureCompanionArg || captureCompanionBlankArg;
    const outputPrefix = captureCompanionArg ? "--capture-companion=" : "--capture-companion-blank=";
    const win = createCompanionWindow({ blank: Boolean(captureCompanionBlankArg) });
    if (captureCompanionArg) win.showInactive();
    win.webContents.once("did-finish-load", () => {
      if (captureCompanionArg) {
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
    return;
  }
  currentLayout();
  modelPathCheck = reconcileLegacyModelPaths(currentLayout());
  await cleanupMigratedSource(currentLayout());
  const legacyConfig = readLauncherConfig();
  if (process.env.MINDSPACE_SKIP_LEGACY_MIGRATION !== "1") {
    migrateLegacyLayout({
      paths: currentLayout(),
      legacyRoots: [legacyConfig.root, path.join(app.getPath("userData"), "app")],
      version: "0.4.0",
    });
  }
  initializeCredentialStore();
  initializeServiceSupervisor();
  initializeProductWindows();
  initializeSettingsController();
  await synchronizeRuntimeProxy();
  await initializeWorkspace();
  initializeUpdateManager();
  initializeRuntimeManager();
  initializeComponentManager();
  observedTtsProvider = configuredTtsProvider();
  setTimeout(() => { scheduleVoiceBackgroundDownload(); }, 1_500).unref();
  setInterval(() => { void reconcileSelectedTts(); }, 1_000).unref();
  // IPC consumers render immediately after the window is created. Initialize
  // every manager first so the first snapshot cannot race normal startup.
  createWindow();
  if (!captureArg) createTray();
  // Text chat is the baseline product. Start Core immediately and leave ASR,
  // VAD and TTS opt-in so missing voice components cannot block entry.
  if (!dashboardPreviewArg) setTimeout(() => { void startDefaultCore(); }, 250).unref();
});
app.on("before-quit", (event) => {
  if (finalExit) return;
  event.preventDefault();
  if (shutdownTask) return;
  quitting = true;
  clearTimeout(companionBoundsTimer);
  if (companionWindow && !companionWindow.isDestroyed()) companionWindow.destroy();
  shutdownTask = (async () => {
    try {
      await allServices("stop");
      // Give child process trees a short, bounded period to release audio/GPU
      // handles. We never wait on an unmanaged WSL service during app exit.
      await new Promise((resolve) => setTimeout(resolve, 450));
    } finally {
      finalExit = true;
      tray?.destroy();
      app.exit(0);
    }
  })();
});

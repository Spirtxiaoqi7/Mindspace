const { app, BrowserWindow, dialog, ipcMain, Menu, net, safeStorage, session, shell, Tray } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const extractZip = require("extract-zip");
const { createComponentManager } = require("./component-manager.cjs");
const { createCompanionController, isCompanionCaptureMode } = require("./companion-controller.cjs");
const { createDiagnosticsController } = require("./diagnostics-controller.cjs");
const { createEnvironmentRegistry } = require("./environment-registry.cjs");
const { createRuntimeManager } = require("./runtime-manager.cjs");
const { evaluateHardwareAvailability } = require("./hardware-policy.cjs");
const { LLM_PRESETS, isLoopbackUrl } = require("./onboarding-policy.cjs");
const { createOnboardingController } = require("./onboarding-controller.cjs");
const { createQwenController } = require("./qwen-controller.cjs");
const { createRuntimeController } = require("./runtime-controller.cjs");
const { createVoiceController } = require("./voice-controller.cjs");
const {
  SERVICE_START_ORDER,
  isFatalStartFailure,
  isStaleCore,
  productEntryState,
  serviceRestartDelay,
} = require("./service-policy.cjs");
const { appPaths, ensureAppPaths } = require("./app-paths.cjs");
const { createSecretStore } = require("./secret-store.cjs");
const { createServiceSupervisor } = require("./service-supervisor.cjs");
const { createSettingsController } = require("./settings-controller.cjs");
const { createStorageController } = require("./storage-controller.cjs");
const { createProductWindows } = require("./product-windows.cjs");
const { createUpdateController } = require("./update-controller.cjs");
const { environmentForPorts, loadServicePorts, resolvePortConfigPath } = require("./service-ports.cjs");
const {
  bundledVersion,
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
const captureAnnouncement = process.argv.includes("--capture-announcement");
let launcherWindow;
let productWindow;
let companionController;
let tray;
let quitting = false;
let componentManager;
let diagnosticsController;
let environmentRegistry;
let onboardingController;
let qwenController;
let runtimeController;
let runtimeManager;
let credentialStore;
let serviceSupervisor;
let settingsController;
let storageController;
let voiceController;
let productWindows;
let updateController;
let layout;

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


let shutdownTask = null;
let finalExit = false;

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

function companionSnapshot() {
  return activeCompanionController().snapshot();
}

function createCompanionWindow({ blank = false } = {}) {
  return activeCompanionController().createWindow({ blank });
}

function syncCompanionVisibility() {
  return activeCompanionController().syncVisibility();
}

function companionAction(action) {
  return activeCompanionController().action(action);
}

function downloadSource() {
  return activeRuntimeController().downloadSource();
}

async function synchronizeRuntimeProxy() {
  return activeRuntimeController().synchronizeProxy();
}

function rootPath() {
  return activeStorageController().rootPath();
}

function persistRoot(root) {
  return activeStorageController().persistRoot(root);
}

async function initializeWorkspace(root = rootPath()) {
  return activeStorageController().initializeWorkspace(root);
}

function runtimeDataRoot() {
  return app.isPackaged ? currentLayout().data : path.join(rootPath(), "runtime");
}

function modelRoot() {
  const fallback = app.isPackaged ? currentLayout().models : path.join(rootPath(), "assets", "models");
  return environmentRegistry?.resolveModelRoot(fallback) || fallback;
}

function logRoot() {
  return app.isPackaged ? currentLayout().logs : path.join(rootPath(), "runtime", "logs");
}







function configuredTtsProvider(root) {
  return activeVoiceController().configuredProvider(root);
}

function configuredTtsVoice() {
  return activeVoiceController().configuredVoice();
}

function isLocalTtsProvider(provider) {
  return activeVoiceController().isLocalProvider(provider);
}

function ttsServiceName(provider = configuredTtsProvider(rootPath())) {
  return activeVoiceController().serviceName(provider);
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
function activeQwenController() {
  if (!qwenController) throw new Error("Qwen 控制器尚未初始化");
  return qwenController;
}
function activeVoiceController() {
  if (!voiceController) throw new Error("语音控制器尚未初始化");
  return voiceController;
}
function activeOnboardingController() {
  if (!onboardingController) throw new Error("首次配置控制器尚未初始化");
  return onboardingController;
}
function activeRuntimeController() {
  if (!runtimeController) throw new Error("运行时控制器尚未初始化");
  return runtimeController;
}
function activeStorageController() {
  if (!storageController) throw new Error("存储控制器尚未初始化");
  return storageController;
}
function activeCompanionController() {
  if (!companionController) throw new Error("桌宠控制器尚未初始化");
  return companionController;
}

function initializeCompanionController() {
  if (companionController) return companionController;
  companionController = createCompanionController({
    app,
    BrowserWindow,
    dirname: __dirname,
    getLauncherWindow: () => launcherWindow,
    isHostCaptureMode: () => Boolean(captureArg),
    isQuitting: () => quitting,
    readLauncherConfig,
    writeLauncherConfig,
  });
  companionController.registerIpc(ipcMain);
  return companionController;
}
function qwenRuntimeRoot() { return activeQwenController().runtimeRoot(); }
function runCommand(command, arguments_, timeoutMs) { return activeQwenController().runCommand(command, arguments_, timeoutMs); }
function refreshQwenRuntimePreflight() { return activeQwenController().refreshPreflight(); }
function qwenRuntimePreflight() { return activeQwenController().preflight(); }
function probe(service) { return activeSupervisor().probe(service); }
function qwenSupervisorState() { return activeQwenController().supervisorState(); }
function stopExternalQwenSupervisor() { return activeQwenController().stopExternalSupervisor(); }
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

function initializeHostControllers() {
  initializeCompanionController();
  storageController = createStorageController({
    app,
    currentLayout,
    dialog,
    dirname: __dirname,
    getComponentManager: () => componentManager,
    getLauncherSnapshot: snapshot,
    getRuntimeManager: () => runtimeManager,
    hintedRoot,
    initializeComponentManager,
    readLauncherConfig,
    setQuitting: (value) => { quitting = value; },
    stopServicesForUpdate,
    writeLauncherConfig,
  });
  qwenController = createQwenController({
    currentLayout,
    getEnvironmentRegistry: () => environmentRegistry,
    getRuntimeManager: () => runtimeManager,
    getServiceSupervisor: () => serviceSupervisor,
    isTcpPortOccupied: (port, timeoutMs) => isTcpPortOccupied(port, timeoutMs),
    probe: (service) => probe(service),
    service: services.qwenTts,
  });
  diagnosticsController = createDiagnosticsController({
    app,
    currentLayout,
    downloadSource,
    logRoot,
    runtimeSnapshot: unifiedRuntimeSnapshot,
    writeJsonAtomic,
  });
  onboardingController = createOnboardingController({
    configuredLlm,
    fetch: (...args) => net.fetch(...args),
    getComponentManager: () => componentManager,
    getSettingsController: () => settingsController,
    getVoiceController: () => voiceController,
    normalizeLlmInput,
    readLauncherConfig,
    runtimeAction,
    runtimeSnapshot: unifiedRuntimeSnapshot,
    writeLauncherConfig,
  });
  voiceController = createVoiceController({
    evaluateHardwareAvailability,
    getComponentManager: () => componentManager,
    getOnboardingSnapshot: () => onboardingController.snapshot(),
    getRuntimeManager: () => runtimeManager,
    getServiceSupervisor: () => serviceSupervisor,
    isQuitting: () => quitting,
    onboardingUpdate: (patch) => onboardingController.update(patch),
    patchCoreSettings,
    probe,
    readJson,
    readLauncherConfig,
    recordServiceEvent,
    refreshQwenRuntimePreflight,
    rootPath,
    runtimeAction,
    runtimeDataRoot,
    runtimeSnapshot: unifiedRuntimeSnapshot,
    scheduleStartupHealthRecheck,
    services,
    startService,
    stopExternalQwenSupervisor,
    stopService,
    waitForServiceOffline,
  });
  runtimeController = createRuntimeController({
    app,
    currentLayout,
    evaluateHardwareAvailability,
    extractZip,
    getComponentManager: () => componentManager,
    getEnvironmentRegistry: () => environmentRegistry,
    getRuntimeManager: () => runtimeManager,
    getTtsTransition: () => voiceController.transitionSnapshot(),
    onboardingUpdate: (patch) => onboardingController.update(patch),
    logRoot,
    modelRoot,
    qwenRuntimePreflight,
    readLauncherConfig,
    refreshQwenRuntimePreflight,
    resolvePowerShell,
    rootPath,
    scheduleVoiceBackgroundDownload: () => voiceController.scheduleBackgroundDownload(),
    session,
    serviceEnvironment,
    stopService,
    stopServicesForUpdate,
    writeLauncherConfig,
  });
  voiceController.registerIpc(ipcMain);
  onboardingController.registerIpc(ipcMain);
  runtimeController.registerIpc(ipcMain);
  storageController.registerIpc(ipcMain);
}

function initializeServiceSupervisor() {
  serviceSupervisor = createServiceSupervisor({
    app, services, portRegistry, fetch: (...args) => net.fetch(...args), rootPath, currentLayout, runtimeDataRoot, modelRoot,
    qwenRuntimeRoot, logRoot, resolvePowerShell, serviceIdentityRoot, writeServiceIdentity, clearServiceIdentity,
    configuredTtsProvider, configuredTtsVoice, configuredLlm, readCredential, readJson,
    runtimeSnapshot: () => runtimeManager, componentSnapshot: () => componentManager?.snapshot(), qwenRuntimePreflight,
    evaluateHardwareAvailability, gptVoices: voiceController.voices, environmentForPorts, serviceRestartDelay,
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
  return activeVoiceController().configuredGptVoiceComponent();
}

function onboardingSnapshot() {
  return activeOnboardingController().snapshot();
}

function updateOnboardingConfig(patch) {
  return activeOnboardingController().update(patch);
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

async function applyVoicePreference(preference) { return activeVoiceController().applyPreference(preference); }

async function selectVoiceProvider(preference, { startIfReady = true, requestDownload = true } = {}) { return activeVoiceController().selectProvider(preference, { startIfReady, requestDownload }); }

function scheduleVoiceBackgroundDownload() { return activeVoiceController().scheduleBackgroundDownload(); }

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

function ttsVoiceSnapshot() { return activeVoiceController().snapshot(); }

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
    ttsReport = await activeQwenController().withStartingStatus(ttsReport);
  }
  entries.push(["tts", ttsReport]);
  const reports = Object.fromEntries(entries);
  const storage = activeStorageController().snapshot();
  return {
    root, home: currentLayout().home, workspace: storage.workspace, ps7, ps7Ready: Boolean(ps7), ttsProvider,
    storage: storage.storage,
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
    for (const name of new Set([...children.keys(), "tts", "qwenTts"])) stopService(name);
    activeVoiceController().resetTransition();
    return { ok: true };
  }
  return { ok: false, error: "未知批量操作" };
}


async function ensureSelectedTtsService() { return activeVoiceController().ensureSelectedService(); }

async function reconcileSelectedTts() { return activeVoiceController().reconcileSelected(); }

async function startDefaultCore() {
  if (quitting || captureArg || !activeStorageController().workspaceSnapshot().ready) return { ok: false, skipped: true };
  if (app.isPackaged && !runtimeManager?.snapshot().ready) {
    return { ok: false, skipped: true, error: "基础运行环境尚未完成" };
  }
  const result = await startService("api");
  if (result.ok) scheduleStartupHealthRecheck("api", 2500);
  else recordServiceEvent("service.default_core_start_failed", { error: result.error || "unknown" });
  return result;
}

function installComponent(component, signal, onProgress) { return activeRuntimeController().installComponent(component, signal, onProgress); }

async function finalizeComponent(component, targetRoot) { return activeRuntimeController().finalizeComponent(component, targetRoot); }

function defaultComponentTarget(component) { return activeRuntimeController().defaultComponentTarget(component); }

function componentTarget(component) { return activeRuntimeController().componentTarget(component); }

function initializeComponentManager() {
  environmentRegistry = createEnvironmentRegistry({
    paths: currentLayout(),
    environment: process.env,
    developmentRoot: rootPath(),
    userDataRoot: app.getPath("userData"),
    localAppData: process.env.LOCALAPPDATA,
    packaged: app.isPackaged,
    logFile: path.join(logRoot(), "environment-registry.jsonl"),
  });
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
    inspectTarget: (component) => environmentRegistry.inspectTarget(component, defaultComponentTarget(component)),
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

function unifiedRuntimeSnapshot() { return activeRuntimeController().snapshot(); }

async function runtimeAction(action, id = "") { return activeRuntimeController().action(action, id); }

async function selectTtsVoice(id) { return activeVoiceController().selectVoice(id); }

async function installTtsVoice(id) { return activeVoiceController().installVoice(id); }

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
  return activeStorageController().migrateToStorageTarget(target);
}

ipcMain.handle("launcher:snapshot", snapshot);
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
ipcMain.handle("launcher:shortcut", () => {
  const shortcut = path.join(app.getPath("desktop"), "Mindspace.lnk");
  const ok = shell.writeShortcutLink(shortcut, { target: process.execPath, cwd: path.dirname(process.execPath), description: "Mindspace 本地 AI 应用" });
  return { ok, path: shortcut };
});
ipcMain.handle("runtime:diagnostics", async () => {
  const reportPath = diagnosticsController.createReport();
  await shell.openPath(reportPath);
  return { ok: true, path: reportPath };
});
const singleInstance = captureArg || isCompanionCaptureMode(process.argv) ? true : app.requestSingleInstanceLock();
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
  initializeCompanionController();
  if (activeCompanionController().startCaptureMode()) return;
  currentLayout();
  initializeHostControllers();
  await activeStorageController().prepareLegacyLayout();
  initializeCredentialStore();
  initializeServiceSupervisor();
  initializeProductWindows();
  initializeSettingsController();
  await synchronizeRuntimeProxy();
  await initializeWorkspace();
  initializeUpdateManager();
  initializeRuntimeManager();
  initializeComponentManager();
  activeVoiceController().initializeObservedProvider();
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
  companionController?.destroy();
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

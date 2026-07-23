const { app, BrowserWindow, dialog, ipcMain, Menu, net, session, shell, Tray } = require("electron");
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
const { createUpdateManager } = require("./update-manager.cjs");
const { createLauncherUpdater } = require("./launcher-updater.cjs");
const { createComponentManager } = require("./component-manager.cjs");
const { GPT_SOVITS_VOICES } = require("./gpt-sovits-catalog.cjs");
const { createRuntimeManager } = require("./runtime-manager.cjs");
const { SERVICE_START_ORDER, isFatalStartFailure, isStaleCore, shouldWaitForAsrBeforeLocalTts } = require("./service-policy.cjs");
const { appPaths, ensureAppPaths, migrateLegacyLayout } = require("./app-paths.cjs");
const { cleanupMigratedSource, migrateStorage } = require("./storage-location.cjs");
const {
  bundledArchive,
  bundledVersion,
  ensureCoreRoot,
  resolveWorkspaceRoot,
} = require("./bootstrap-core.cjs");

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
const services = {
  api: { port: 8765, health: "http://127.0.0.1:8765/api/v1/health", script: "start.ps1" },
  asr: { port: 8766, health: "http://127.0.0.1:8766/health", script: "start-asr.ps1" },
  tts: { port: 5055, health: "http://127.0.0.1:5055/health", script: "start-tts.ps1" },
};
const children = new Map();
const starts = new Map();
const startGenerations = new Map();
const captureArg = process.argv.find((argument) => argument.startsWith("--capture="));
const captureAnnouncement = process.argv.includes("--capture-announcement");
let launcherWindow;
let productWindow;
let tray;
let quitting = false;
let updateManager;
let launcherUpdater;
let componentManager;
let runtimeManager;
let layout;
let storageMigration = { active: false, progress: 0, message: "", error: "" };
let workspace = { ready: false, created: false, message: "正在准备用户工作区", error: "" };

function readJson(file, fallback = null) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return fallback; }
}

function currentLayout() {
  if (!layout) layout = ensureAppPaths(appPaths(app));
  return layout;
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
  return resolveWorkspaceRoot({
    app,
    configuredRoot: configuredRoot && fs.existsSync(configuredDrive) ? configuredRoot : "",
    environmentRoot: process.env.MINDSPACE_ROOT || "",
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
  for (const name of ["runtime-manager.jsonl", "component-download.log", "maintenance-verify.log", "api.launcher.log", "asr.launcher.log", "tts.launcher.log"]) {
    const content = tailLog(path.join(logRoot(), name));
    if (content) fs.writeFileSync(path.join(folder, name), `${content}\n`, "utf8");
  }
  return folder;
}

async function probe(service) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 900);
  try {
    const response = await fetch(service.health, { signal: controller.signal });
    return { online: response.ok, detail: response.ok ? await response.json() : {} };
  } catch (error) {
    return { online: false, detail: { error: String(error.message || error) } };
  } finally { clearTimeout(timeout); }
}

function configuredTtsProvider(root) {
  try {
    const settings = JSON.parse(fs.readFileSync(path.join(runtimeDataRoot(), "config", "settings.json"), "utf8"));
    return String(settings?.audio?.tts_provider || "siliconflow").toLowerCase();
  } catch {
    return "siliconflow";
  }
}

function configuredTtsVoice() {
  const settings = readJson(path.join(runtimeDataRoot(), "config", "settings.json"), {});
  return String(settings?.audio?.tts_gpt_sovits_voice || "v4-changli");
}

function isLocalTtsProvider(provider) {
  return ["cosyvoice", "gpt-sovits"].includes(String(provider || "").toLowerCase());
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
  const entries = await Promise.all(Object.entries(services).map(async ([name, service]) => [
    name,
    name === "tts" && !isLocalTtsProvider(ttsProvider)
      ? { online: true, detail: { provider: ttsProvider, remote: ttsProvider === "siliconflow", message: ttsProvider === "siliconflow" ? "使用云端 TTS API" : "无需本地 TTS Worker" } }
      : await probe(service),
  ]));
  const reports = Object.fromEntries(entries);
  return {
    root, home: currentLayout().home, workspace, ps7, ps7Ready: Boolean(ps7), ttsProvider,
    storage: storageMigration,
    services: reports, models: modelStatus(root, ttsProvider),
    components: componentManager?.snapshot() || { active: "", items: [] },
    voices: ttsVoiceSnapshot(),
    runtime: unifiedRuntimeSnapshot(),
  };
}

function launchService(name) {
  const root = rootPath();
  const ps7 = resolvePowerShell();
  const service = services[name];
  const script = service && path.join(root, "scripts", service.script);
  if (app.isPackaged && !runtimeManager?.snapshot().ready) return { ok: false, error: "基础运行环境尚未完成，请先点击“一键初始化”" };
  if (!ps7) return { ok: false, error: "应用私有 PowerShell 7 尚未安装" };
  if (!service || !fs.existsSync(script)) return { ok: false, error: `缺少 ${service?.script || name}` };
  const asrPython = app.isPackaged
    ? path.join(currentLayout().venvs, "asr-cuda", "Scripts", "python.exe")
    : path.join(root, ".venv-asr", "Scripts", "python.exe");
  const asrReadyMarker = path.join(path.dirname(path.dirname(asrPython)), ".mindspace-asr-ready.json");
  if (name === "asr" && (!fs.existsSync(asrPython) || !fs.existsSync(asrReadyMarker))) {
    const partial = fs.existsSync(asrPython);
    return { ok: false, error: partial
      ? "上次 ASR CUDA 安装未完成；请点击“继续修复并启动”，已下载内容会被复用"
      : "ASR CUDA 尚未安装；请点击“安装并启动”，基础文字功能不受影响" };
  }
  if (name === "asr") {
    const verification = spawnSync(asrPython, ["-c", "import torch, torchaudio, funasr, fastapi, uvicorn, websockets; assert torch.cuda.is_available()"], {
      encoding: "utf8", windowsHide: true, timeout: 45_000, env: serviceEnvironment(),
    });
    if (verification.status !== 0) {
      fs.rmSync(asrReadyMarker, { force: true });
      return { ok: false, error: `ASR CUDA 校验未通过，已标记为可续修：${String(verification.stderr || verification.stdout || "依赖缺失").trim().slice(-360)}` };
    }
  }
  if (name === "tts" && configuredTtsProvider(root) === "cosyvoice") {
    const ttsCandidates = app.isPackaged
      ? [path.join(currentLayout().venvs, "tts-cuda", "Scripts", "python.exe"), asrPython]
      : [path.join(root, ".venv-tts", "Scripts", "python.exe"), asrPython];
    const pythonReady = ttsCandidates.some((candidate) => fs.existsSync(candidate));
    const ttsMarker = app.isPackaged
      ? path.join(currentLayout().state, "components", "tts-runtime", "ready.json")
      : path.join(root, "runtime", "components", "tts-runtime", "ready.json");
    if (!pythonReady || !fs.existsSync(ttsMarker)) {
      return { ok: false, error: "CosyVoice 运行时尚未安装，请先在组件区安装“CosyVoice 运行时”" };
    }
    if (!fs.existsSync(path.join(root, "vendor", "CosyVoice", "cosyvoice", "cli", "cosyvoice.py"))) {
      return { ok: false, error: "CosyVoice 运行代码缺失，请先检查应用更新" };
    }
    const settings = readJson(path.join(runtimeDataRoot(), "config", "settings.json"), {});
    const reference = String(settings?.audio?.tts_reference_audio || "");
    if (!reference || !fs.existsSync(reference)) {
      return { ok: false, error: "尚未上传有效的 TTS 参考音频，请先在声音设置中上传" };
    }
  }
  if (name === "tts" && configuredTtsProvider(root) === "gpt-sovits") {
    const voiceId = configuredTtsVoice();
    const voice = GPT_SOVITS_VOICES.find((candidate) => candidate.id === voiceId);
    const python = app.isPackaged
      ? path.join(currentLayout().venvs, "gpt-sovits", "Scripts", "python.exe")
      : path.join(root, ".venv-gpt-sovits", "Scripts", "python.exe");
    const marker = app.isPackaged
      ? path.join(currentLayout().venvs, "gpt-sovits", "ready.json")
      : path.join(root, ".venv-gpt-sovits", "ready.json");
    const worker = path.join(root, "vendor", "gpt_sovits_mindspace_worker.py");
    const code = path.join(root, "vendor", "GPT-SoVITS", "GPT_SoVITS", "TTS_infer_pack", "TTS.py");
    const selectedComponent = voice && componentManager?.snapshot().items.find((item) => item.id === voice.componentId);
    if (!voice) return { ok: false, error: `未知 GPT-SoVITS 音色：${voiceId}` };
    if (!fs.existsSync(python) || !fs.existsSync(marker)) return { ok: false, error: "GPT-SoVITS 运行时尚未安装，请先在音色区安装所选音色" };
    if (!fs.existsSync(worker) || !fs.existsSync(code)) return { ok: false, error: "GPT-SoVITS 推理代码缺失，请先检查应用更新" };
    if (!selectedComponent?.ready) return { ok: false, error: `${voice.label} 模型尚未完整下载` };
  }
  const logs = logRoot();
  fs.mkdirSync(logs, { recursive: true });
  const out = fs.openSync(path.join(logs, `${name}.launcher.log`), "a");
  const child = spawn(ps7, ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script], {
    cwd: root, env: serviceEnvironment(), windowsHide: true, detached: false, stdio: ["ignore", out, out],
  });
  children.set(name, child);
  child.once("exit", () => {
    if (children.get(name) === child) children.delete(name);
  });
  return { ok: true, pid: child.pid };
}

async function startService(name) {
  if (starts.has(name)) return starts.get(name);
  const running = children.get(name);
  if (running && running.exitCode === null && !running.killed) {
    return { ok: true, pid: running.pid, alreadyRunning: true };
  }
  const service = services[name];
  if (!service) return { ok: false, error: `未知服务：${name}` };
  const generation = startGenerations.get(name) || 0;
  const task = (async () => {
    const health = await probe(service);
    if (health.online) return { ok: true, alreadyRunning: true, detail: health.detail };
    if ((startGenerations.get(name) || 0) !== generation) {
      return { ok: false, cancelled: true, error: "启动已被停止操作取消" };
    }
    return launchService(name);
  })();
  starts.set(name, task);
  try {
    return await task;
  } finally {
    if (starts.get(name) === task) starts.delete(name);
  }
}

function serviceEnvironment(extra = {}) {
  const base = runtimeManager?.privateEnvironment() || process.env;
  const coreMarker = readJson(path.join(currentLayout().state, "components", "core-venv.json"), {});
  const ffmpegRoot = app.isPackaged ? path.join(currentLayout().tools, "ffmpeg", "8.1.2") : path.join(rootPath(), ".tools", "ffmpeg", "8.1.2");
  return {
    ...base,
    MINDSPACE_HOME: currentLayout().home,
    MINDSPACE_ENVIRONMENT: currentLayout().environment,
    MINDSPACE_MODEL_ROOT: modelRoot(),
    MINDSPACE_DATA_ROOT: runtimeDataRoot(),
    MINDSPACE_RUNTIME_DIR: runtimeDataRoot(),
    MINDSPACE_CORE_PYTHON: app.isPackaged ? String(coreMarker.executable || "") : path.join(rootPath(), ".venv", "Scripts", "python.exe"),
    MINDSPACE_ASR_VENV: app.isPackaged ? path.join(currentLayout().venvs, "asr-cuda") : path.join(rootPath(), ".venv-asr"),
    MINDSPACE_TTS_VENV: app.isPackaged ? path.join(currentLayout().venvs, "tts-cuda") : path.join(rootPath(), ".venv-tts"),
    MINDSPACE_TTS_MARKER_ROOT: app.isPackaged ? path.join(currentLayout().state, "components", "tts-runtime") : path.join(rootPath(), "runtime", "components", "tts-runtime"),
    MINDSPACE_GPT_SOVITS_VENV: app.isPackaged ? path.join(currentLayout().venvs, "gpt-sovits") : path.join(rootPath(), ".venv-gpt-sovits"),
    MINDSPACE_GPT_SOVITS_CODE_ROOT: path.join(rootPath(), "vendor", "GPT-SoVITS"),
    MINDSPACE_GPT_SOVITS_RUNTIME_ROOT: path.join(modelRoot(), "tts", "gpt-sovits", "runtime"),
    MINDSPACE_FFMPEG: path.join(ffmpegRoot, "ffmpeg.exe"),
    CUDA_MODULE_LOADING: base.CUDA_MODULE_LOADING || "LAZY",
    PYTORCH_CUDA_ALLOC_CONF: base.PYTORCH_CUDA_ALLOC_CONF || "expandable_segments:True,max_split_size_mb:128",
    PATH: `${ffmpegRoot}${path.delimiter}${base.PATH || base.Path || process.env.PATH || ""}`,
    ...extra,
  };
}

function stopService(name) {
  startGenerations.set(name, (startGenerations.get(name) || 0) + 1);
  const child = children.get(name);
  if (!child) return { ok: false, error: "该服务不是由当前 Launcher 启动" };
  spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
  children.delete(name);
  return { ok: true };
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
    for (const name of SERVICE_START_ORDER) {
      if (name === "tts" && isLocalTtsProvider(current.ttsProvider)) {
        const asrReport = await probe(services.asr);
        if (shouldWaitForAsrBeforeLocalTts(current.ttsProvider, started.includes("asr"), asrReport)) {
          const asrReady = await waitForServiceReady("asr", 90_000);
          if (!asrReady) {
            warnings.push("ASR 模型尚未完成加载；为避免 CUDA 模型并行争抢，本次暂不启动本地 TTS");
            continue;
          }
        }
      }
      if (!current.services[name]?.online) {
        const result = await startService(name);
        if (!result.ok) {
          if (isFatalStartFailure(name)) return result;
          warnings.push(result.error || `${name} 未启动`);
          continue;
        }
        started.push(name);
      }
    }
    return { ok: true, started, warnings };
  }
  if (action === "stop") {
    for (const name of [...children.keys()]) stopService(name);
    return { ok: true };
  }
  return { ok: false, error: "未知批量操作" };
}

async function waitForServiceReady(name, timeout) {
  const service = services[name];
  if (!service) return false;
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const report = await probe(service);
    if (report.online && (name !== "asr" || report.detail?.ready === true)) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

function stopServicesForUpdate() {
  const ps7 = resolvePowerShell();
  const script = path.join(rootPath(), "scripts", "stop-services.ps1");
  if (!ps7 || !fs.existsSync(script)) return allServices("stop");
  const result = spawnSync(ps7, ["-NoProfile", "-File", script], { cwd: rootPath(), encoding: "utf8", windowsHide: true, timeout: 30_000 });
  children.clear();
  if (result.status !== 0) throw new Error((result.stderr || result.stdout || "停止服务失败").trim());
  return { ok: true };
}

async function waitForHealth(timeout) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if ((await probe(services.api)).online) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

function initializeUpdateManager() {
  let launcherConfig = readLauncherConfig();
  if (!launcherConfig.updateDeviceId) {
    launcherConfig = { ...launcherConfig, updateDeviceId: crypto.randomUUID() };
    writeLauncherConfig(launcherConfig);
  }
  launcherUpdater = createLauncherUpdater({
    packaged: app.isPackaged,
    currentVersion: () => app.getVersion(),
  });
  updateManager = createUpdateManager({
    app,
    rootPath,
    resolvePowerShell,
    publicKeyPath: path.join(__dirname, "assets", "update-public-key.pem"),
    fetch: (...arguments_) => net.fetch(...arguments_),
    downloadRoot: path.join(currentLayout().downloads, "updates"),
    deviceId: launcherConfig.updateDeviceId,
    launcherUpdater,
    bundledRoot: app.isPackaged
      ? path.join(process.resourcesPath, "runtime", "bundled")
      : path.join(__dirname, "bootstrap", "runtime-bundle"),
    readConfig: readLauncherConfig,
    writeConfig: writeLauncherConfig,
    stopServicesForUpdate,
    startServices: () => allServices("start"),
    waitForHealth,
  });
  const checkConfiguredFeed = async () => {
    try {
      const current = updateManager.snapshot();
      if (["checking", "downloading", "verifying", "installing"].includes(current.status)) return;
      const next = await updateManager.check();
      if (next.coreAvailable && !next.launcherAvailable && !next.downloaded && readLauncherConfig().autoDownloadUpdates !== false) {
        await updateManager.download();
      }
    } catch {}
  };
  setTimeout(checkConfiguredFeed, 5_000).unref();
  setInterval(checkConfiguredFeed, 6 * 60 * 60 * 1000).unref();
}

function installComponent(component, signal, onProgress) {
  return new Promise((resolve, reject) => {
    const root = rootPath();
    const ps7 = resolvePowerShell();
    const script = path.join(root, component.installScript || "");
    const runtimeName = component.id === "tts-runtime" ? "CosyVoice" : component.id === "gpt-sovits-runtime" ? "GPT-SoVITS" : "ASR";
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
    } : {
      venv: [5, "正在创建独立 Python 环境…"],
      torch: [12, "正在下载并安装 CUDA 版 PyTorch…"],
      funasr: [68, "正在安装 FunASR 与实时服务依赖…"],
      project: [84, "正在连接 Mindspace ASR 服务…"],
      verify: [94, "正在验证 CUDA 与 FunASR…"],
      done: [99, "ASR 运行时安装完成，正在校验…"],
    };
    const stagePrefix = component.id === "tts-runtime" ? "TTS" : component.id === "gpt-sovits-runtime" ? "GPT_SOVITS" : "ASR";
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
    vad: path.join(currentLayout().models, "asr", "fsmn-vad"),
    punc: path.join(currentLayout().models, "asr", "ct-punc"),
    "asr-runtime": path.join(currentLayout().venvs, "asr-cuda"),
    tts: path.join(currentLayout().models, "tts", "Fun-CosyVoice3-0.5B-2512"),
    "tts-runtime": path.join(currentLayout().state, "components", "tts-runtime"),
    "gpt-sovits-v4-base": path.join(currentLayout().models, "tts", "gpt-sovits", "runtime", "GPT_SoVITS"),
    "gpt-sovits-ffmpeg": path.join(currentLayout().tools, "ffmpeg", "8.1.2"),
    "gpt-sovits-runtime": path.join(currentLayout().venvs, "gpt-sovits"),
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
  const modelItems = models.items.map((item) => ({
    ...item,
    category: item.category || (item.id === "embedding" ? "base" : "voice"),
    kind: item.provider === "installer" ? "environment" : "model",
    required: !item.optional,
    hardwareAvailable: item.hardware !== "nvidia" || Boolean(base.system.nvidia),
    unavailableReason: item.hardware === "nvidia" && !base.system.nvidia ? "需要兼容的 NVIDIA 显卡与驱动" : "",
  }));
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
    return unifiedRuntimeSnapshot();
  }
  if (action === "repair") {
    stopServicesForUpdate();
    await runtimeManager.repair();
    await componentManager.downloadAll();
    return unifiedRuntimeSnapshot();
  }
  if (["install", "retry"].includes(action)) {
    if (baseComponent) {
      if (["python", "core-venv"].includes(id)) stopServicesForUpdate();
      await runtimeManager.install(id);
    }
    else if (modelComponent) {
      if (modelComponent.hardware === "nvidia" && !runtimeManager.snapshot().system.nvidia) throw new Error("此组件需要兼容的 NVIDIA 显卡与驱动");
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

  const file = path.join(runtimeDataRoot(), "config", "settings.json");
  const settings = readJson(file, {});
  settings.audio = { ...(settings.audio || {}), tts_provider: "gpt-sovits", tts_gpt_sovits_voice: voice.id };
  writeJsonAtomic(file, settings);

  let apiWarning = "";
  try {
    const response = await net.fetch("http://127.0.0.1:8765/api/v1/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audio: { tts_provider: "gpt-sovits", tts_gpt_sovits_voice: voice.id } }),
    });
    if (!response.ok) apiWarning = `核心服务暂未同步（HTTP ${response.status}），下次启动自动生效`;
  } catch {
    apiWarning = "核心服务未运行，配置已保存并会在下次启动生效";
  }

  if (children.has("tts")) stopService("tts");
  const occupied = await probe(services.tts);
  const started = occupied.online ? { ok: true } : await startService("tts");
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
  const root = rootPath();
  const ps7 = resolvePowerShell();
  if (!ps7) return { ok: false, error: "未找到 PowerShell 7，请先安装或设置 MINDSPACE_PWSH" };
  const commands = {
    verify: ["-File", path.join(root, "scripts", "runtime-verify.ps1")],
    integrity: ["-File", path.join(root, "scripts", "verify-source-integrity.ps1")],
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

function createWindow() {
  const win = new BrowserWindow({
    width: 1180, height: 760, minWidth: 920, minHeight: 620,
    show: !captureArg,
    backgroundColor: "#0b0d11", titleBarStyle: "hidden", titleBarOverlay: { color: "#0b0d11", symbolColor: "#bbc4d0", height: 40 },
    webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false },
  });
  launcherWindow = win;
  win.loadFile(
    path.join(__dirname, "dist", "index.html"),
    captureAnnouncement ? { query: { announcement: "history" } } : undefined,
  );
  win.on("close", (event) => {
    if (!quitting && !captureArg) {
      event.preventDefault();
      win.hide();
    }
  });
  if (captureArg) {
    win.webContents.once("did-finish-load", () => {
      setTimeout(async () => {
        const output = captureArg.slice("--capture=".length);
        const image = await win.webContents.capturePage();
        fs.writeFileSync(output, image.toPNG());
        app.quit();
      }, 1800);
    });
  }
}

async function openProductWindow() {
  if (productWindow && !productWindow.isDestroyed()) {
    productWindow.show();
    productWindow.focus();
    return { ok: true };
  }
  const api = await probe(services.api);
  if (!api.online) {
    launcherWindow?.show();
    launcherWindow?.focus();
    await dialog.showMessageBox(launcherWindow, {
      type: "info",
      title: "Mindspace 尚未启动",
      message: "请先启动本地服务",
      detail: "在服务控制中心点击“启动并进入”，应用会等待核心服务就绪后自动打开。",
    });
    return { ok: false, error: "Mindspace Core 尚未就绪" };
  }
  productWindow = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 1040,
    minHeight: 700,
    show: false,
    backgroundColor: "#f7efe4",
    title: "Mindspace",
    icon: path.join(__dirname, "assets", "mindspace-icon.ico"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  productWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  productWindow.loadURL("http://127.0.0.1:8765/");
  productWindow.once("ready-to-show", () => {
    productWindow.show();
    launcherWindow?.hide();
  });
  productWindow.on("closed", () => { productWindow = undefined; });
  return { ok: true };
}

function createTray() {
  tray = new Tray(path.join(__dirname, "assets", "mindspace-icon.ico"));
  tray.setToolTip("Mindspace");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "打开 Mindspace", click: () => { void openProductWindow(); } },
    { label: "服务控制中心", click: () => { launcherWindow?.show(); launcherWindow?.focus(); } },
    { type: "separator" },
    { label: "停止本机服务", click: () => allServices("stop") },
    { label: "退出", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("double-click", () => { launcherWindow?.show(); launcherWindow?.focus(); });
}

ipcMain.handle("launcher:snapshot", snapshot);
ipcMain.handle("launcher:service", async (_, { service, action }) => {
  if (action === "start") return startService(service);
  if (action === "stop") return stopService(service);
  if (action === "restart") { stopService(service); return startService(service); }
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
  if (target.startsWith("http")) await shell.openExternal(target); else await shell.openPath(target);
  return { ok: true };
});
ipcMain.handle("launcher:external", async (_, rawUrl) => {
  const target = new URL(String(rawUrl || ""));
  const allowed = new Set(["modelscope.cn", "www.modelscope.cn", "huggingface.co"]);
  if (target.protocol !== "https:" || !allowed.has(target.hostname)) throw new Error("只允许打开已核验的模型来源");
  await shell.openExternal(target.toString());
  return { ok: true };
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
  if (storageMigration.active) throw new Error("存储迁移正在进行");
  if (runtimeManager?.snapshot().active || componentManager?.snapshot().active) {
    throw new Error("请先等待或取消当前组件安装，再迁移存储位置");
  }
  const result = await dialog.showOpenDialog({
    title: "选择 Mindspace 存储位置",
    buttonLabel: "迁移到这里",
    properties: ["openDirectory", "createDirectory"],
    defaultPath: path.dirname(currentLayout().home),
  });
  if (result.canceled || !result.filePaths[0]) return snapshot();
  const selected = path.resolve(result.filePaths[0]);
  const target = path.basename(selected).toLowerCase() === "mindspace" ? selected : path.join(selected, "Mindspace");
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
    return { ...(await snapshot()), storage: storageMigration };
  } catch (error) {
    storageMigration = { active: false, progress: 0, message: "存储迁移失败，原位置保持不变", error: String(error.message || error) };
    throw error;
  }
});
ipcMain.handle("launcher:shortcut", () => {
  const shortcut = path.join(app.getPath("desktop"), "Mindspace.lnk");
  const ok = shell.writeShortcutLink(shortcut, { target: process.execPath, cwd: path.dirname(process.execPath), description: "Mindspace 本地 AI 应用" });
  return { ok, path: shortcut };
});
ipcMain.handle("launcher:update", async (_, { action, updateUrl, channel } = {}) => {
  if (!updateManager) throw new Error("更新管理器尚未就绪");
  if (action === "snapshot") return updateManager.snapshot();
  if (action === "configure") return updateManager.configure(updateUrl, channel);
  if (action === "check") return updateManager.check();
  if (action === "download") return updateManager.download();
  if (action === "pause") return updateManager.pause();
  if (action === "discard") return updateManager.discard();
  if (action === "install") return updateManager.install();
  if (action === "rollback") return updateManager.rollback();
  throw new Error("未知更新操作");
});
ipcMain.handle("launcher:component", async (_, { action, id } = {}) => {
  if (!componentManager) throw new Error("组件下载器尚未就绪");
  if (action === "snapshot") return componentManager.snapshot();
  if (action === "download") return componentManager.download(id);
  if (action === "download-all") return componentManager.downloadAll();
  if (action === "cancel") return componentManager.cancel(id);
  throw new Error("未知组件操作");
});
ipcMain.handle("launcher:voice", async (_, { action, id } = {}) => {
  if (action === "snapshot") return ttsVoiceSnapshot();
  if (action === "install") return installTtsVoice(id);
  if (action === "select") return selectTtsVoice(id);
  throw new Error("未知音色操作");
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

const singleInstance = captureArg ? true : app.requestSingleInstanceLock();
if (!singleInstance) app.quit();
if (!captureArg) app.on("second-instance", () => { launcherWindow?.show(); launcherWindow?.focus(); });
app.whenReady().then(async () => {
  currentLayout();
  await cleanupMigratedSource(currentLayout());
  const legacyConfig = readLauncherConfig();
  if (process.env.MINDSPACE_SKIP_LEGACY_MIGRATION !== "1") {
    migrateLegacyLayout({
      paths: currentLayout(),
      legacyRoots: [legacyConfig.root, path.join(app.getPath("userData"), "app")],
      version: "0.4.0",
    });
  }
  await synchronizeRuntimeProxy();
  await initializeWorkspace();
  initializeUpdateManager();
  initializeRuntimeManager();
  initializeComponentManager();
  // IPC consumers render immediately after the window is created. Initialize
  // every manager first so the first snapshot cannot race normal startup.
  createWindow();
  if (!captureArg) createTray();
});
app.on("before-quit", () => {
  quitting = true;
  for (const name of [...children.keys()]) stopService(name);
});

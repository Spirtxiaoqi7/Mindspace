const fs = require("node:fs");
const path = require("node:path");
const tcp = require("node:net");
const { spawn, spawnSync } = require("node:child_process");

function createServiceSupervisor(dependencies) {
  const {
    app, services, portRegistry, fetch, rootPath, currentLayout, runtimeDataRoot, modelRoot,
    qwenRuntimeRoot, logRoot, resolvePowerShell, serviceIdentityRoot, writeServiceIdentity,
    clearServiceIdentity, configuredTtsProvider, configuredTtsVoice, configuredLlm,
    readCredential, readJson, runtimeSnapshot, componentSnapshot, qwenRuntimePreflight,
    evaluateHardwareAvailability, gptVoices, environmentForPorts, serviceRestartDelay, isQuitting, runCommand,
  } = dependencies;
  const children = new Map();
  const starts = new Map();
  const startGenerations = new Map();
  const serviceLaunchTimes = new Map();
  const desiredServices = new Set();
  const serviceRecovery = new Map();

  async function probe(service) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 900);
    try {
      const response = await fetch(service.health, { signal: controller.signal });
      let detail = {};
      if (response.ok) {
        try { detail = await response.json(); } catch {}
      }
      return { online: response.ok, detail };
    } catch (error) {
      return { online: false, detail: { error: String(error.message || error) } };
    } finally { clearTimeout(timeout); }
  }

  function isTcpPortOccupied(port, timeoutMs = 350) {
    return new Promise((resolve) => {
      const socket = tcp.createConnection({ host: "127.0.0.1", port });
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        socket.destroy();
        resolve(value);
      };
      socket.setTimeout(timeoutMs, () => finish(false));
      socket.once("connect", () => finish(true));
      socket.once("error", () => finish(false));
    });
  }

  async function qwenSupervisorState() {
    const pidPath = path.join(qwenRuntimeRoot(), "qwen3-vllm.pid");
    const pid = String(readJson(pidPath, "") || fs.existsSync(pidPath) && fs.readFileSync(pidPath, "utf8") || "").trim();
    if (!/^\d+$/.test(pid)) return { running: false, pid: "" };
    const distro = process.env.MINDSPACE_QWEN3_WSL_DISTRO || "MindspaceVLLM";
    const result = await runCommand("wsl.exe", ["--distribution", distro, "--", "bash", "-lc", 'kill -0 "$1" 2>/dev/null', "mindspace-qwen", pid], 5_000);
    return { running: result.status === 0, pid };
  }

  function stopExternalQwenSupervisor() {
    const pidPath = path.join(qwenRuntimeRoot(), "qwen3-vllm.pid");
    let pid = "";
    try { pid = fs.readFileSync(pidPath, "utf8").trim(); } catch {}
    const distro = process.env.MINDSPACE_QWEN3_WSL_DISTRO || "MindspaceVLLM";
    let signalled = false;
    if (/^\d+$/.test(pid)) {
      const result = spawnSync(
        "wsl.exe",
        ["--distribution", distro, "--", "bash", "-lc", 'kill -TERM "$1" 2>/dev/null', "mindspace-qwen", pid],
        { windowsHide: true, timeout: 5_000 },
      );
      signalled = result.status === 0;
    }
    // MindspaceVLLM is a dedicated application distro. The Windows wrapper can
    // disappear while its Linux model server survives, so the PID file alone is
    // not authoritative. Terminating the distro is the final ownership-safe
    // cleanup and releases GPU memory before another voice service starts.
    const terminated = spawnSync("wsl.exe", ["--terminate", distro], { windowsHide: true, timeout: 15_000 });
    try { fs.rmSync(pidPath, { force: true }); } catch {}
    return signalled || terminated.status === 0;
  }

  function recordServiceEvent(event, details = {}) {
    try {
      fs.mkdirSync(logRoot(), { recursive: true });
      fs.appendFileSync(path.join(logRoot(), "runtime-manager.jsonl"), `${JSON.stringify({ at: new Date().toISOString(), event, ...details })}\n`);
    } catch {}
  }

  function clearServiceRecovery(name) {
    const recovery = serviceRecovery.get(name);
    if (recovery?.timer) clearTimeout(recovery.timer);
    serviceRecovery.delete(name);
  }

  function scheduleServiceRecovery(name, startedAt, exitCode, signal) {
    if (isQuitting() || !desiredServices.has(name)) return;
    const previous = serviceRecovery.get(name) || { failures: 0, timer: null };
    const uptimeMs = Math.max(0, Date.now() - startedAt);
    const failures = uptimeMs >= 120_000 ? 1 : previous.failures + 1;
    const delayMs = serviceRestartDelay(failures);
    if (delayMs == null) {
      desiredServices.delete(name);
      serviceRecovery.set(name, { failures, timer: null });
      recordServiceEvent("service.recovery_exhausted", { service: name, failures, uptime_ms: uptimeMs, exit_code: exitCode, signal });
      return;
    }
    const timer = setTimeout(async () => {
      const current = serviceRecovery.get(name);
      if (!current || current.timer !== timer || isQuitting() || !desiredServices.has(name)) return;
      serviceRecovery.set(name, { ...current, timer: null });
      recordServiceEvent("service.restart_attempt", { service: name, failure: failures });
      const result = await startService(name, true);
      if (!result.ok) recordServiceEvent("service.restart_failed", { service: name, failure: failures, error: result.error || "unknown" });
    }, delayMs);
    serviceRecovery.set(name, { failures, timer });
    recordServiceEvent("service.restart_scheduled", { service: name, failure: failures, delay_ms: delayMs, uptime_ms: uptimeMs, exit_code: exitCode, signal });
  }

  function asrRuntimeReport() {
    return componentSnapshot()?.items?.find((item) => item.id === "asr-runtime") || null;
  }

  function asrRuntimePath() {
    return asrRuntimeReport()?.path
      || (app.isPackaged ? path.join(currentLayout().venvs, "asr-cuda") : path.join(rootPath(), ".venv-asr"));
  }

  function componentPath(id, fallback) {
    return componentSnapshot()?.items?.find((item) => item.id === id)?.path || fallback;
  }

  function serviceEnvironment(extra = {}) {
    const base = runtimeSnapshot()?.privateEnvironment?.() || process.env;
    const llm = configuredLlm();
    const coreMarker = readJson(path.join(currentLayout().state, "components", "core-venv.json"), {});
    const ffmpegRoot = app.isPackaged ? path.join(currentLayout().tools, "ffmpeg", "8.1.2") : path.join(rootPath(), ".tools", "ffmpeg", "8.1.2");
    return {
      ...base,
      MINDSPACE_HOME: currentLayout().home,
      MINDSPACE_ENVIRONMENT: currentLayout().environment,
      MINDSPACE_MODEL_ROOT: modelRoot(), MINDSPACE_DATA_ROOT: runtimeDataRoot(), MINDSPACE_RUNTIME_DIR: currentLayout().home,
      ...environmentForPorts(portRegistry),
      MINDSPACE_SERVICE_IDENTITY_ROOT: serviceIdentityRoot(),
      MINDSPACE_LLM_MODE: llm.mode, MINDSPACE_LLM_BASE_URL: llm.base_url, MINDSPACE_LLM_API_KEY: llm.api_key, MINDSPACE_LLM_MODEL: llm.model,
      MINDSPACE_ASR_API_KEY: readCredential("asr_api_key"), MINDSPACE_TTS_SILICONFLOW_API_KEY: readCredential("tts_siliconflow_api_key"),
      MINDSPACE_CORE_PYTHON: app.isPackaged ? String(coreMarker.executable || "") : path.join(rootPath(), ".venv", "Scripts", "python.exe"),
      MINDSPACE_ASR_VENV: asrRuntimePath(),
      MINDSPACE_TTS_VENV: app.isPackaged ? path.join(currentLayout().venvs, "tts-cuda") : path.join(rootPath(), ".venv-tts"),
      MINDSPACE_TTS_MARKER_ROOT: componentPath("tts-runtime", app.isPackaged ? path.join(currentLayout().state, "components", "tts-runtime") : path.join(rootPath(), "runtime", "components", "tts-runtime")),
      MINDSPACE_GPT_SOVITS_VENV: componentPath("gpt-sovits-runtime", app.isPackaged ? path.join(currentLayout().venvs, "gpt-sovits") : path.join(rootPath(), ".venv-gpt-sovits")),
      MINDSPACE_GPT_SOVITS_CODE_ROOT: path.join(rootPath(), "vendor", "GPT-SoVITS"),
      MINDSPACE_GPT_SOVITS_RUNTIME_ROOT: path.join(modelRoot(), "tts", "gpt-sovits", "runtime"),
      MINDSPACE_QWEN3_WSL_DISTRO: base.MINDSPACE_QWEN3_WSL_DISTRO || "MindspaceVLLM",
      MINDSPACE_QWEN3_RUNTIME_ROOT: componentPath("qwen3-vllm-runtime", path.join(currentLayout().home, "environment", "qwen3-vllm")),
      MINDSPACE_FFMPEG: path.join(componentPath("gpt-sovits-ffmpeg", ffmpegRoot), "ffmpeg.exe"), CUDA_MODULE_LOADING: base.CUDA_MODULE_LOADING || "LAZY",
      PYTORCH_CUDA_ALLOC_CONF: base.PYTORCH_CUDA_ALLOC_CONF || "expandable_segments:True,max_split_size_mb:128",
      PATH: `${ffmpegRoot}${path.delimiter}${base.PATH || base.Path || process.env.PATH || ""}`,
      ...extra,
    };
  }

  async function launchService(name, generation) {
    const root = rootPath();
    const ps7 = resolvePowerShell();
    const service = services[name];
    const script = service && path.join(root, "scripts", service.script);
    const hardwareId = name === "asr" ? "asr" : name === "qwenTts" ? "qwen3-vllm-runtime" : name === "tts" ? configuredTtsProvider(root) : "";
    const runtime = runtimeSnapshot();
    const hardware = evaluateHardwareAvailability(hardwareId, runtime?.snapshot?.().system || {});
    if (!hardware.eligible) return { ok: false, error: hardware.message };
    if (app.isPackaged && !runtime?.snapshot?.().ready) return { ok: false, error: "基础运行环境尚未完成，请先点击“一键初始化”" };
    if (!ps7) return { ok: false, error: "应用私有 PowerShell 7 尚未安装" };
    if (!service || !fs.existsSync(script)) return { ok: false, error: `缺少 ${service?.script || name}` };
    const asrRuntime = asrRuntimeReport();
    const asrPython = path.join(asrRuntimePath(), "Scripts", "python.exe");
    const asrReadyMarker = path.join(path.dirname(path.dirname(asrPython)), ".mindspace-asr-ready.json");
    if (name === "asr" && !asrRuntime?.ready) {
      return { ok: false, error: asrRuntime?.partial || fs.existsSync(asrPython) ? "ASR CUDA 环境不完整；请点击“继续修复并启动”，已有文件会被复用" : "确认未找到可用 ASR CUDA 环境；请点击“安装并启动”，基础文字功能不受影响" };
    }
    if (name === "tts" && configuredTtsProvider(root) === "cosyvoice") {
      const candidates = app.isPackaged ? [path.join(currentLayout().venvs, "tts-cuda", "Scripts", "python.exe"), asrPython] : [path.join(root, ".venv-tts", "Scripts", "python.exe"), asrPython];
      const marker = app.isPackaged ? path.join(currentLayout().state, "components", "tts-runtime", "ready.json") : path.join(root, "runtime", "components", "tts-runtime", "ready.json");
      if (!candidates.some(fs.existsSync) || !fs.existsSync(marker)) return { ok: false, error: "CosyVoice 运行时尚未安装，请先在组件区安装“CosyVoice 运行时”" };
      if (!fs.existsSync(path.join(root, "vendor", "CosyVoice", "cosyvoice", "cli", "cosyvoice.py"))) return { ok: false, error: "CosyVoice 运行代码缺失，请先检查应用更新" };
      const reference = String(readJson(path.join(runtimeDataRoot(), "config", "settings.json"), {})?.audio?.tts_reference_audio || "");
      if (!reference || !fs.existsSync(reference)) return { ok: false, error: "尚未上传有效的 TTS 参考音频，请先在声音设置中上传" };
    }
    if (name === "tts" && configuredTtsProvider(root) === "gpt-sovits") {
      const voiceId = configuredTtsVoice();
      const voice = gptVoices.find((candidate) => candidate.id === voiceId);
      const python = path.join(componentPath("gpt-sovits-runtime", app.isPackaged ? path.join(currentLayout().venvs, "gpt-sovits") : path.join(root, ".venv-gpt-sovits")), "Scripts", "python.exe");
      const marker = app.isPackaged ? path.join(currentLayout().venvs, "gpt-sovits", "ready.json") : path.join(root, ".venv-gpt-sovits", "ready.json");
      const selected = voice && componentSnapshot()?.items.find((item) => item.id === voice.componentId);
      if (!voice) return { ok: false, error: `未知 GPT-SoVITS 音色：${voiceId}` };
      if (!fs.existsSync(python) || !fs.existsSync(marker)) return { ok: false, error: "GPT-SoVITS 运行时尚未安装，请先在音色区安装所选音色" };
      if (!fs.existsSync(path.join(root, "vendor", "gpt_sovits_mindspace_worker.py")) || !fs.existsSync(path.join(root, "vendor", "GPT-SoVITS", "GPT_SoVITS", "TTS_infer_pack", "TTS.py"))) return { ok: false, error: "GPT-SoVITS 推理代码缺失，请先检查应用更新" };
      if (!selected?.ready) return { ok: false, error: `${voice.label} 模型尚未完整下载` };
    }
    if (name === "qwenTts") {
      const preflight = qwenRuntimePreflight();
      if (!preflight.eligible) return { ok: false, error: preflight.message };
      if (!componentSnapshot()?.items.find((item) => item.id === "qwen3-vllm-runtime")?.ready) return { ok: false, error: "Qwen3 实时语音运行时尚未安装，请先在组件区安装或修复" };
    }
    if (generation !== undefined && (startGenerations.get(name) || 0) !== generation) return { ok: false, cancelled: true, error: "启动已被停止操作取消" };
    fs.mkdirSync(logRoot(), { recursive: true });
    const out = fs.openSync(path.join(logRoot(), `${name}.launcher.log`), "a");
    const cwd = name === "qwenTts" ? qwenRuntimeRoot() : name === "api" ? root : currentLayout().home;
    const child = spawn(ps7, ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script], { cwd, env: serviceEnvironment(), windowsHide: true, detached: false, stdio: ["ignore", out, out] });
    writeServiceIdentity(name, child, ps7, script);
    const startedAt = Date.now();
    children.set(name, child); serviceLaunchTimes.set(name, startedAt);
    child.once("exit", (code, signal) => {
      if (children.get(name) === child) { children.delete(name); serviceLaunchTimes.delete(name); clearServiceIdentity(name); }
      scheduleServiceRecovery(name, startedAt, code, signal);
    });
    return { ok: true, pid: child.pid };
  }

  async function startService(name, recoveryAttempt = false) {
    if (!recoveryAttempt) clearServiceRecovery(name);
    desiredServices.add(name);
    if (starts.has(name)) return starts.get(name);
    const running = children.get(name);
    if (running && running.exitCode === null && !running.killed) return { ok: true, pid: running.pid, alreadyRunning: true };
    const service = services[name];
    if (!service) return { ok: false, error: `未知服务：${name}` };
    const generation = startGenerations.get(name) || 0;
    const task = (async () => {
      const health = await probe(service);
      if (health.online) return { ok: true, alreadyRunning: true, detail: health.detail };
      if (await isTcpPortOccupied(service.port)) return { ok: false, portConflict: true, error: `端口 ${service.port} 已被未知服务占用；Mindspace 未终止该进程` };
      if ((startGenerations.get(name) || 0) !== generation) return { ok: false, cancelled: true, error: "启动已被停止操作取消" };
      return launchService(name, generation);
    })();
    starts.set(name, task);
    try { return await task; } finally { if (starts.get(name) === task) starts.delete(name); }
  }

  function scheduleStartupHealthRecheck(name, delayMs = 2000) {
    const timer = setTimeout(async () => {
      if (isQuitting() || !desiredServices.has(name)) return;
      if ((await probe(services[name])).online) return;
      const child = children.get(name);
      if (child && child.exitCode === null && !child.killed) return;
      recordServiceEvent("service.startup_recheck", { service: name });
      const result = await startService(name, true);
      if (!result.ok) recordServiceEvent("service.startup_recheck_failed", { service: name, error: result.error || "unknown" });
    }, delayMs);
    timer.unref?.();
  }

  function stopService(name) {
    desiredServices.delete(name); clearServiceRecovery(name);
    startGenerations.set(name, (startGenerations.get(name) || 0) + 1);
    const child = children.get(name);
    if (child) {
      spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
      clearServiceIdentity(name); children.delete(name); serviceLaunchTimes.delete(name);
    }
    const external = name === "qwenTts" && stopExternalQwenSupervisor();
    if (!child && !external) return { ok: false, error: "该服务不是由当前 Launcher 启动" };
    return { ok: true, external: Boolean(external) };
  }

  async function waitForServiceOffline(name, timeoutMs = 9_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (!(await probe(services[name])).online) return true;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    return false;
  }

  function stopServicesForUpdate(fallback) {
    for (const name of desiredServices) clearServiceRecovery(name);
    desiredServices.clear();
    const ps7 = resolvePowerShell();
    const script = path.join(rootPath(), "scripts", "stop-services.ps1");
    if (!ps7 || !fs.existsSync(script)) return fallback();
    const result = spawnSync(ps7, ["-NoProfile", "-File", script, "-ProjectRoot", rootPath(), "-IdentityRoot", serviceIdentityRoot(), "-IncludeQwen"], { cwd: rootPath(), env: serviceEnvironment(), encoding: "utf8", windowsHide: true, timeout: 30_000 });
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

  return {
    probe, isTcpPortOccupied, qwenSupervisorState, stopExternalQwenSupervisor, recordServiceEvent, startService, scheduleStartupHealthRecheck,
    serviceEnvironment, stopService, waitForServiceOffline, stopServicesForUpdate, waitForHealth,
    child: (name) => children.get(name), hasChild: (name) => children.has(name), childNames: () => [...children.keys()],
    launchTime: (name) => serviceLaunchTimes.get(name), hasDesired: (name) => desiredServices.has(name),
    setDesired: (name, desired = true) => desired ? desiredServices.add(name) : desiredServices.delete(name),
    clearDesired: () => desiredServices.clear(),
  };
}

module.exports = { createServiceSupervisor };

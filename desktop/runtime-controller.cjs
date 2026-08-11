const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const originalProxyEnvironment = {
  HTTP_PROXY: process.env.HTTP_PROXY,
  HTTPS_PROXY: process.env.HTTPS_PROXY,
  ALL_PROXY: process.env.ALL_PROXY,
};

function createRuntimeController({
  app,
  currentLayout,
  evaluateHardwareAvailability,
  extractZip,
  getComponentManager,
  getEnvironmentRegistry,
  getRuntimeManager,
  getTtsTransition,
  onboardingUpdate,
  logRoot,
  modelRoot,
  qwenRuntimePreflight,
  readLauncherConfig,
  refreshQwenRuntimePreflight,
  resolvePowerShell,
  rootPath,
  scheduleVoiceBackgroundDownload,
  session,
  serviceEnvironment,
  stopService,
  stopServicesForUpdate,
  writeLauncherConfig,
}) {
  function defaultComponentTarget(component) {
    if (!app.isPackaged) return path.join(rootPath(), component.target);
    const targets = {
      embedding: path.join(modelRoot(), "shibing624", "text2vec-base-chinese"),
      asr: path.join(modelRoot(), "asr", "paraformer-zh-streaming"),
      "asr-final": path.join(modelRoot(), "asr", "Fun-ASR-Nano-2512"),
      vad: path.join(modelRoot(), "asr", "fsmn-vad"),
      punc: path.join(modelRoot(), "asr", "ct-punc"),
      "asr-runtime": path.join(currentLayout().venvs, "asr-cuda"),
      tts: path.join(modelRoot(), "tts", "Fun-CosyVoice3-0.5B-2512"),
      "tts-runtime": path.join(currentLayout().state, "components", "tts-runtime"),
      "gpt-sovits-v4-base": path.join(modelRoot(), "tts", "gpt-sovits", "runtime", "GPT_SoVITS"),
      "gpt-sovits-ffmpeg": path.join(currentLayout().tools, "ffmpeg", "8.1.2"),
      "gpt-sovits-runtime": path.join(currentLayout().venvs, "gpt-sovits"),
      "qwen3-vllm-runtime": path.join(currentLayout().home, "environment", "qwen3-vllm"),
    };
    if (component.category === "voice" && component.id.startsWith("gpt-sovits-")) {
      return path.join(modelRoot(), "tts", "gpt-sovits", "runtime");
    }
    return targets[component.id] || path.join(currentLayout().home, component.target);
  }

  function componentTarget(component) {
    const fallback = defaultComponentTarget(component);
    return getEnvironmentRegistry()?.resolveTarget(component, fallback) || fallback;
  }

  function installComponent(component, signal, onProgress) {
    return new Promise((resolve, reject) => {
      const root = rootPath();
      const ps7 = resolvePowerShell();
      const script = path.join(root, component.installScript || "");
      const runtimeName = component.id === "tts-runtime" ? "CosyVoice" : component.id === "gpt-sovits-runtime" ? "GPT-SoVITS" : component.id === "qwen3-vllm-runtime" ? "Qwen3 实时语音" : "ASR";
      if (!ps7) return reject(new Error(`未找到 PowerShell 7，无法安装 ${runtimeName} 运行时`));
      if (!component.installScript || !fs.existsSync(script)) return reject(new Error(`缺少运行时安装脚本：${component.installScript || "未配置"}`));
      const logs = logRoot();
      fs.mkdirSync(logs, { recursive: true });
      const log = fs.createWriteStream(path.join(logs, `${component.id}.install.log`), { flags: "a" });
      const child = spawn(
        ps7,
        ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, ...(component.installArgs || [])],
        { cwd: root, env: serviceEnvironment(), windowsHide: true, stdio: ["ignore", "pipe", "pipe"] },
      );
      const sourceLabel = downloadSource() === "official" ? "官方源" : "国内镜像";
      const stages = component.id === "tts-runtime" ? {
        preflight: [8, "正在检查并复用现有 ASR/CUDA 环境…"], reuse: [72, "现有依赖完整，无需重复下载…"],
        "build-tools": [18, "正在准备 Whisper 构建兼容环境…"], torch: [30, "正在校验并复用 CUDA PyTorch…"],
        dependencies: [42, `正在从${sourceLabel}解析缺失的 CosyVoice 依赖…`], verify: [88, "正在验证 CosyVoice 与 CUDA…"],
        marker: [96, "正在写入运行时校验凭证…"], done: [99, "CosyVoice 运行时安装完成，正在校验…"],
      } : component.id === "gpt-sovits-runtime" ? {
        preflight: [8, "正在检查 ASR CUDA Torch 与公共模型…"], venv: [16, "正在创建隔离的 GPT-SoVITS 环境…"],
        torch: [28, "正在链接已验证的 CUDA Torch 文件…"], dependencies: [48, `正在从${sourceLabel}安装独立推理依赖…`],
        project: [78, "正在连接 GPT-SoVITS 推理代码…"], verify: [92, "正在验证 GPT-SoVITS、CUDA 与声学模型…"],
        marker: [97, "正在写入运行时校验凭证…"], done: [99, "GPT-SoVITS 运行时安装完成，正在校验…"],
      } : component.id === "qwen3-vllm-runtime" ? {
        preflight: [8, "正在检查 WSL2、GPU 与受管运行目录…"], gpu: [35, "正在验证 WSL2 内 NVIDIA GPU…"],
        model: [58, "正在核验 Qwen3 CustomVoice 模型与 Serena 声线锁定…"], verify: [76, "正在核验 Qwen3 模型与启动脚本…"],
        done: [99, "Qwen3 运行时可用；首次后台预热不会阻塞聊天…"],
      } : {
        venv: [5, "正在创建独立 Python 环境…"], torch: [12, "正在下载并安装 CUDA 版 PyTorch…"],
        funasr: [68, "正在安装 FunASR 与实时服务依赖…"], project: [84, "正在连接 Mindspace ASR 服务…"],
        verify: [94, "正在验证 CUDA 与 FunASR…"], done: [99, "ASR 运行时安装完成，正在校验…"],
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
        spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
        finish(new Error("下载已取消"));
      };
      signal.addEventListener("abort", cancel, { once: true });
      child.once("error", (error) => finish(error));
      child.once("exit", (code) => {
        if (signal.aborted) return finish(new Error("下载已取消"));
        if (code !== 0) {
          const output = installerOutput.join("");
          let reason = "请查看运行日志";
          if (/No module named ['"]pkg_resources['"]/i.test(output)) reason = "Whisper 构建缺少 pkg_resources";
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
          const python = app.isPackaged ? path.join(currentLayout().venvs, "gpt-sovits", "Scripts", "python.exe") : path.join(rootPath(), ".venv-gpt-sovits", "Scripts", "python.exe");
          const helper = path.join(rootPath(), "scripts", "extract-voice-archive.py");
          if (!fs.existsSync(python)) throw new Error("GPT-SoVITS 私有 Python 尚未就绪，无法安全解压人物音色");
          if (!fs.existsSync(helper)) throw new Error("应用缺少人物音色安全解压脚本，请先更新 Mindspace Core");
          await new Promise((resolve, reject) => {
            const output = [];
            const child = spawn(python, [helper, "--source", source, "--destination", staging, "--type", rule.type === "tar.gz" ? "tar.gz" : "zip", ...(rule.encoding ? ["--encoding", rule.encoding] : [])], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
            child.stdout.on("data", (chunk) => output.push(chunk));
            child.stderr.on("data", (chunk) => output.push(chunk));
            child.once("error", reject);
            child.once("exit", (code) => code === 0 ? resolve() : reject(new Error(`人物音色解压失败（退出码 ${code}）：${Buffer.concat(output).toString("utf8").trim().slice(-800)}`)));
          });
        } else {
          await extractZip(source, { dir: staging });
        }
        const extracted = path.resolve(staging, rule.root || ".");
        const destination = path.resolve(targetRoot, rule.destination || ".");
        const stagingBase = path.resolve(staging);
        if (!(extracted === stagingBase || extracted.startsWith(`${stagingBase}${path.sep}`)) || !(destination === targetBase || destination.startsWith(`${targetBase}${path.sep}`))) throw new Error("模型压缩包包含不安全目标路径");
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

  async function synchronizeProxy() {
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

  function snapshot() {
    const runtimeManager = getRuntimeManager();
    const componentManager = getComponentManager();
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
      ttsTransition: getTtsTransition(),
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

  async function action(actionName, id = "") {
    const runtimeManager = getRuntimeManager();
    const componentManager = getComponentManager();
    if (!runtimeManager || !componentManager) throw new Error("运行时管理器尚未就绪");
    await synchronizeProxy();
    const baseComponent = runtimeManager.componentFor(id);
    const modelComponent = componentManager.snapshot().items.find((item) => item.id === id);
    if (actionName === "snapshot") return snapshot();
    if (actionName === "cancel") {
      runtimeManager.cancel(id);
      componentManager.cancel(id);
      return snapshot();
    }
    if (actionName === "install-all") {
      stopServicesForUpdate();
      await runtimeManager.installAll();
      await componentManager.downloadAll();
      onboardingUpdate({ baseInstalledAt: new Date().toISOString() });
      scheduleVoiceBackgroundDownload();
      return snapshot();
    }
    if (actionName === "repair") {
      stopServicesForUpdate();
      await runtimeManager.repair();
      await componentManager.downloadAll();
      return snapshot();
    }
    if (actionName === "remove") {
      if (!modelComponent) throw new Error("只有可选模型与语音组件可以卸载");
      if (["asr-runtime", "asr", "asr-final", "vad", "punc"].includes(id)) stopService("asr");
      if (id === "qwen3-vllm-runtime") stopService("qwenTts");
      if (id === "tts" || id === "tts-runtime" || id.startsWith("gpt-sovits-")) stopService("tts");
      await componentManager.remove(id);
      return snapshot();
    }
    if (["install", "retry"].includes(actionName)) {
      if (baseComponent) {
        if (["python", "core-venv"].includes(id)) stopServicesForUpdate();
        await runtimeManager.install(id);
      } else if (modelComponent) {
        if (id === "qwen3-vllm-runtime") {
          const preflight = await refreshQwenRuntimePreflight();
          if (!preflight.eligible) throw new Error(preflight.message);
        }
        const hardware = evaluateHardwareAvailability(id, runtimeManager.snapshot().system);
        if (!hardware.eligible) throw new Error(hardware.message);
        await componentManager.download(id);
      } else {
        throw new Error(`未知运行时组件：${id}`);
      }
      return snapshot();
    }
    throw new Error("未知运行时操作");
  }

  async function componentAction(actionName, id = "") {
    const componentManager = getComponentManager();
    if (!componentManager) throw new Error("组件下载器尚未就绪");
    if (actionName === "snapshot") return componentManager.snapshot();
    if (actionName === "download") {
      if (id === "qwen3-vllm-runtime") {
        const preflight = await refreshQwenRuntimePreflight();
        if (!preflight.eligible) throw new Error(preflight.message);
      }
      return componentManager.download(id);
    }
    if (actionName === "download-all") return componentManager.downloadAll();
    if (actionName === "cancel") return componentManager.cancel(id);
    if (actionName === "remove") return action("remove", id);
    throw new Error("未知组件操作");
  }

  function registerIpc(ipcMain) {
    ipcMain.handle("launcher:component", async (_, { action: actionName, id } = {}) => componentAction(actionName, id));
    ipcMain.handle("runtime:action", async (_, { action: actionName, id } = {}) => action(actionName, id));
    ipcMain.handle("runtime:snapshot", async () => action("snapshot"));
    ipcMain.handle("runtime:install", async (_, { id } = {}) => action("install", id));
    ipcMain.handle("runtime:cancel", async (_, { id } = {}) => action("cancel", id));
    ipcMain.handle("runtime:retry", async (_, { id } = {}) => action("retry", id));
    ipcMain.handle("runtime:repair", async () => action("repair"));
    ipcMain.handle("runtime:source", async (_, { source = "china" } = {}) => {
      const value = source === "official" ? "official" : source === "china" ? "china" : "";
      if (!value) throw new Error("未知下载源");
      if (snapshot().active) throw new Error("下载或安装进行中，完成或取消后才能切换下载源");
      writeLauncherConfig({ ...readLauncherConfig(), downloadSource: value });
      return snapshot();
    });
    ipcMain.handle("runtime:proxy", async (_, { proxy = "" } = {}) => {
      const value = String(proxy || "").trim();
      if (value && !/^(https?|socks5):\/\//i.test(value)) throw new Error("代理地址必须以 http://、https:// 或 socks5:// 开头");
      writeLauncherConfig({ ...readLauncherConfig(), runtimeProxy: value });
      await synchronizeProxy();
      return { ok: true, proxy: value };
    });
  }

  return {
    action, componentAction, componentTarget, defaultComponentTarget, downloadSource,
    finalizeComponent, installComponent, registerIpc, snapshot, synchronizeProxy,
  };
}

module.exports = { createRuntimeController };

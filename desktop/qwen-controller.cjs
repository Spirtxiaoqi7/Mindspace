const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { evaluateQwenRuntimePreflight } = require("./qwen-runtime-policy.cjs");

function createQwenController({
  currentLayout,
  getEnvironmentRegistry,
  getRuntimeManager,
  getServiceSupervisor,
  isTcpPortOccupied,
  probe,
  service,
}) {
  let preflightCache = { expiresAt: 0, value: { eligible: false, code: "CHECKING", message: "正在检查 Qwen3 运行条件…" } };
  let preflightTask = null;

  function runtimeRoot() {
    const fallback = path.join(currentLayout().home, "environment", "qwen3-vllm");
    return getEnvironmentRegistry()?.resolveTarget(
      { id: "qwen3-vllm-runtime", name: "Qwen3 实时语音运行时", required: ["ready.json"] },
      fallback,
    ) || fallback;
  }

  function launcherCandidates() {
    const configured = process.env.MINDSPACE_QWEN3_WSL_LAUNCHER;
    return [
      configured,
      path.join(runtimeRoot(), "start-qwen3-tts.sh"),
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

  async function refreshPreflight() {
    if (preflightTask) return preflightTask;
    preflightTask = (async () => {
      const base = getRuntimeManager()?.snapshot() || { system: {} };
      const system = base.system || {};
      const distro = process.env.MINDSPACE_QWEN3_WSL_DISTRO || "MindspaceVLLM";
      const wsl = await runCommand("where.exe", ["wsl.exe"], 3_000);
      const wslExecutable = wsl.status === 0 ? String(wsl.stdout || "").split(/\r?\n/).find(Boolean) : "";
      let installed = [];
      let wslGpuAvailable = false;
      let vramMiB = 0;
      let availableVramMiB = 0;
      const hostGpu = await runCommand("nvidia-smi.exe", ["--query-gpu=memory.free", "--format=csv,noheader,nounits"], 4_000);
      const freeValues = String(hostGpu.stdout || "").match(/\d+/g) || [];
      availableVramMiB = Math.max(0, ...freeValues.map(Number));
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
      const candidates = launcherCandidates();
      const marker = path.join(runtimeRoot(), "ready.json");
      let modelSourceReady = fs.existsSync(marker);
      const localModel = path.join(currentLayout().home, "experimental", "qwen3-tts", "models", "Qwen3-TTS-12Hz-1.7B-CustomVoice");
      const localWeight = path.join(localModel, "model.safetensors");
      if (!modelSourceReady && candidates.length && fs.existsSync(path.join(localModel, "config.json")) && fs.existsSync(localWeight)) {
        try { modelSourceReady = fs.statSync(localWeight).size >= 3 * 1024 ** 3; } catch {}
      }
      if (!modelSourceReady && wslExecutable && installed.includes(distro) && candidates.length) {
        try {
          const launcherText = fs.readFileSync(candidates[0], "utf8");
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
        } catch {
          // A malformed external launcher is treated as not ready.
        }
      }
      const health = await probe(service);
      const baseResult = evaluateQwenRuntimePreflight({
        system,
        wslAvailable: Boolean(wslExecutable),
        distroAvailable: installed.includes(distro),
        wslGpuAvailable,
        vramMiB,
        availableVramMiB,
        port: service.port,
        portConflict: !health.online && await isTcpPortOccupied(service.port),
      });
      const value = baseResult.eligible && !modelSourceReady
        ? { eligible: false, code: "QWEN_MODEL_REQUIRED", message: "未发现完整的本地 Qwen3 模型与启动脚本；此安装包不会自动下载 WSL、vLLM 或大模型。" }
        : { ...baseResult, distro, vramMiB, availableVramMiB, modelReady: modelSourceReady };
      preflightCache = { expiresAt: Date.now() + 15_000, value };
      return value;
    })();
    try {
      return await preflightTask;
    } catch (error) {
      const value = { eligible: false, code: "PREFLIGHT_FAILED", message: `Qwen3 条件检查失败：${String(error.message || error)}` };
      preflightCache = { expiresAt: Date.now() + 8_000, value };
      return value;
    } finally {
      preflightTask = null;
    }
  }

  function preflight() {
    if (preflightCache.expiresAt <= Date.now() && !preflightTask) void refreshPreflight();
    return preflightCache.value;
  }

  function activeSupervisor() {
    const supervisor = getServiceSupervisor();
    if (!supervisor) throw new Error("服务监督器尚未初始化");
    return supervisor;
  }

  function supervisorState() {
    return activeSupervisor().qwenSupervisorState();
  }

  function stopExternalSupervisor() {
    return activeSupervisor().stopExternalQwenSupervisor();
  }

  async function withStartingStatus(report) {
    const supervisor = activeSupervisor();
    const child = supervisor.child("qwenTts");
    const managed = Boolean(child && child.exitCode === null && !child.killed);
    const external = managed ? { running: false, pid: "" } : await supervisorState();
    if (!managed && !external.running) return report;
    const startedAt = supervisor.launchTime("qwenTts") || Date.now();
    return {
      ...report,
      starting: true,
      detail: {
        ...report.detail,
        provider: "qwen3-vllm",
        phase: "model_loading",
        managed,
        supervisor_pid: external.pid,
        started_at: new Date(startedAt).toISOString(),
        elapsed_ms: Date.now() - startedAt,
      },
    };
  }

  return {
    preflight,
    refreshPreflight,
    runCommand,
    runtimeRoot,
    stopExternalSupervisor,
    supervisorState,
    withStartingStatus,
  };
}

module.exports = { createQwenController };

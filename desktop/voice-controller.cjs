const fs = require("node:fs");
const path = require("node:path");
const { GPT_SOVITS_VOICES } = require("./gpt-sovits-catalog.cjs");
const { normalizeVoicePreference, voicePreferenceFromProvider, voiceInstallPlan } = require("./onboarding-policy.cjs");

function createVoiceController(dependencies) {
  const {
    evaluateHardwareAvailability, getComponentManager, getOnboardingSnapshot, getRuntimeManager,
    getServiceSupervisor, isQuitting, onboardingUpdate, patchCoreSettings, probe, readJson,
    readLauncherConfig, recordServiceEvent, refreshQwenRuntimePreflight, rootPath, runtimeAction,
    runtimeDataRoot, runtimeSnapshot, scheduleStartupHealthRecheck, services, startService,
    stopExternalQwenSupervisor, stopService, waitForServiceOffline,
  } = dependencies;
  let transition = { state: "idle", target: "", error: "", startedAt: "" };
  let transitionTask = null;
  let backgroundTask = null;
  let backgroundState = { state: "idle", currentId: "", currentName: "", message: "", error: "" };
  let backgroundGeneration = 0;
  let observedProvider = "";
  let reconcileTask = null;

  function configuredProvider(_root) {
    try {
      const settings = JSON.parse(fs.readFileSync(path.join(runtimeDataRoot(), "config", "settings.json"), "utf8"));
      const provider = String(settings?.audio?.tts_provider || "").toLowerCase();
      return ["browser", "cosyvoice", "gpt-sovits", "qwen3-vllm", "siliconflow"].includes(provider)
        ? provider : observedProvider || "browser";
    } catch { return observedProvider || "browser"; }
  }

  function configuredVoice() {
    const settings = readJson(path.join(runtimeDataRoot(), "config", "settings.json"), {});
    return String(settings?.audio?.tts_gpt_sovits_voice || "v4-changli");
  }

  function isLocalProvider(provider) {
    return ["cosyvoice", "gpt-sovits", "qwen3-vllm"].includes(String(provider || "").toLowerCase());
  }

  function serviceName(provider = configuredProvider(rootPath())) {
    return String(provider || "").toLowerCase() === "qwen3-vllm" ? "qwenTts" : "tts";
  }

  function configuredGptVoiceComponent() {
    const voiceId = configuredVoice();
    return GPT_SOVITS_VOICES.find((voice) => voice.id === voiceId)?.componentId
      || GPT_SOVITS_VOICES.find((voice) => voice.id === "v4-changli")?.componentId
      || "gpt-sovits-v4-changli";
  }

  async function syncProductSettings(patch) {
    let warning = "";
    try { await patchCoreSettings(patch); }
    catch (error) { warning = `核心服务未同步：${String(error.message || error)}`; }
    return warning;
  }

  async function applyPreference(preference) {
    const requested = String(preference || "").trim().toLowerCase();
    const selected = requested === "siliconflow" ? "siliconflow" : normalizeVoicePreference(requested);
    const audio = selected === "none"
      ? { tts_provider: "browser", auto_tts: false }
      : { tts_provider: selected, auto_tts: true, ...(selected === "gpt-sovits" ? { tts_gpt_sovits_voice: configuredVoice() } : {}) };
    return syncProductSettings({ audio });
  }

  async function stopProviderService(provider) {
    if (!isLocalProvider(provider)) return;
    const target = serviceName(provider);
    const supervisor = getServiceSupervisor();
    supervisor.setDesired(target, false);
    if (supervisor.hasChild(target)) {
      const stopped = await stopService(target);
      if (!stopped.ok) throw new Error(stopped.error || "本地语音服务未能安全退出");
    } else if (target === "qwenTts") {
      const stopped = await stopExternalQwenSupervisor();
      const offline = await waitForServiceOffline(target);
      if (!stopped && !offline) throw new Error("Qwen3 服务归属无法确认；未结束未知 WSL 进程");
    }
  }

  function voicePlanForPreference(preference) { return voiceInstallPlan(preference, configuredGptVoiceComponent()); }
  function voicePlanItems(preference) {
    const ids = new Set(voicePlanForPreference(preference));
    return (getComponentManager()?.snapshot().items || []).filter((item) => ids.has(item.id));
  }
  function voicePlanReady(preference) {
    const selected = normalizeVoicePreference(preference);
    if (selected === "none") return true;
    const plan = voicePlanForPreference(selected);
    const items = voicePlanItems(selected);
    return plan.length > 0 && items.length === plan.length && items.every((item) => item.ready);
  }

  function snapshot() {
    const current = configuredVoice();
    const components = getComponentManager()?.snapshot().items || [];
    return {
      provider: configuredProvider(rootPath()), current,
      items: GPT_SOVITS_VOICES.map((voice) => {
        const component = components.find((candidate) => candidate.id === voice.componentId);
        return {
          ...voice, ready: Boolean(component?.ready), status: component?.status || "idle",
          progress: component?.progress || 0, downloadedBytes: component?.downloadedBytes || 0,
          totalBytes: component?.totalBytes || voice.estimatedBytes || 0, speedBps: component?.speedBps || 0,
          message: component?.message || "", error: component?.error || "",
        };
      }),
    };
  }

  async function ensureSelectedService() {
    if (transitionTask) return transitionTask;
    const target = serviceName();
    const inactive = target === "qwenTts" ? "tts" : "qwenTts";
    transitionTask = (async () => {
      transition = { state: "stopping", target, error: "", startedAt: new Date().toISOString() };
      const supervisor = getServiceSupervisor();
      supervisor.setDesired(inactive, false);
      if (supervisor.child(inactive)) {
        const stopped = await stopService(inactive);
        if (!stopped.ok || !(await waitForServiceOffline(inactive))) {
          const error = "旧 TTS 引擎未在 9 秒内退出；为避免两个本地模型同时占用显存，已取消切换。";
          transition = { state: "failed", target, error, startedAt: transition.startedAt };
          return { ok: false, error };
        }
      } else if ((await probe(services[inactive])).online) {
        const error = "检测到旧 TTS 引擎不是由当前 Launcher 启动。为避免误杀或双占显存，请先在原启动器中关闭它后再切换。";
        transition = { state: "failed", target, error, startedAt: transition.startedAt };
        return { ok: false, error };
      }
      if (isQuitting()) return { ok: false, cancelled: true, error: "应用正在退出，已取消 TTS 切换" };
      transition = { state: "starting", target, error: "", startedAt: transition.startedAt };
      const result = await startService(target);
      if (result.ok) scheduleStartupHealthRecheck(target);
      transition = result.ok
        ? { state: "ready", target, error: "", startedAt: transition.startedAt }
        : { state: "failed", target, error: result.error || "TTS 启动失败", startedAt: transition.startedAt };
      return result;
    })();
    try { return await transitionTask; }
    finally { transitionTask = null; }
  }

  async function selectProvider(preference, { startIfReady = true, requestDownload = true } = {}) {
    const requested = String(preference || "").trim().toLowerCase();
    const selected = requested === "siliconflow" ? "siliconflow" : normalizeVoicePreference(requested);
    if (selected === "qwen3-vllm") {
      const preflight = await refreshQwenRuntimePreflight();
      if (!preflight.eligible) throw new Error(preflight.message);
    }
    if (!["none", "siliconflow", "qwen3-vllm"].includes(selected)) {
      const hardware = evaluateHardwareAvailability(selected, getRuntimeManager()?.snapshot().system || {});
      if (!hardware.eligible) throw new Error(hardware.message);
    }
    const previousProvider = configuredProvider();
    backgroundGeneration += 1;
    if (backgroundTask && backgroundState.currentId) getComponentManager()?.cancel(backgroundState.currentId);
    const previousService = isLocalProvider(previousProvider) ? serviceName(previousProvider) : "";
    const supervisor = getServiceSupervisor();
    const wasActive = Boolean(previousService && (supervisor.hasDesired(previousService) || supervisor.hasChild(previousService) || (await probe(services[previousService])).online));
    onboardingUpdate({
      voicePreference: selected === "siliconflow" ? "none" : selected, voiceSelectionConfirmed: true,
      voiceDownloadRequested: requestDownload && isLocalProvider(selected), voiceReadyAt: "",
      voiceReadyAcknowledgedAt: ["none", "siliconflow"].includes(selected) ? new Date().toISOString() : "",
    });
    const warning = await applyPreference(selected);
    const nextProvider = selected === "none" ? "browser" : selected;
    observedProvider = nextProvider;
    if (isLocalProvider(previousProvider) && previousProvider !== nextProvider) await stopProviderService(previousProvider);
    if (selected === "none" || selected === "siliconflow") {
      await stopProviderService("gpt-sovits");
      await stopProviderService("qwen3-vllm");
      backgroundState = { state: "idle", currentId: "", currentName: "", message: selected === "siliconflow" ? "已切换为云端声音，不占用本地显存" : "声音已关闭", error: "" };
      return { ok: true, warning, ready: true, started: false, onboarding: getOnboardingSnapshot(), ...snapshot() };
    }
    const ready = voicePlanReady(selected);
    if (!ready) {
      backgroundState = { state: requestDownload ? "queued" : "idle", currentId: "", currentName: "", message: requestDownload ? "新声音已设为当前，正在等待后台安装" : "已记录声音选择，基础环境完成后再下载", error: "" };
      if (requestDownload) scheduleBackgroundDownload();
      return { ok: true, warning, ready: false, queued: requestDownload, started: false, onboarding: getOnboardingSnapshot(), ...snapshot() };
    }
    onboardingUpdate({ voiceReadyAt: new Date().toISOString(), voiceReadyAcknowledgedAt: new Date().toISOString() });
    if (!startIfReady && !wasActive) return { ok: true, warning, ready: true, started: false, onboarding: getOnboardingSnapshot(), ...snapshot() };
    const targetService = serviceName(selected);
    if (previousProvider !== selected && supervisor.hasChild(targetService)) {
      const stopped = await stopService(targetService);
      if (!stopped.ok || !(await waitForServiceOffline(targetService))) throw new Error(stopped.error || "旧声音服务未能安全退出");
    }
    supervisor.setDesired(targetService);
    const started = await ensureSelectedService();
    return { ok: started.ok, error: started.error, warning, ready: true, started: started.ok, onboarding: getOnboardingSnapshot(), ...snapshot() };
  }

  function scheduleBackgroundDownload() {
    const runtimeManager = getRuntimeManager();
    const componentManager = getComponentManager();
    if (backgroundTask || !runtimeManager || !componentManager) return backgroundTask;
    const onboarding = readLauncherConfig().onboarding || {};
    const preference = normalizeVoicePreference(onboarding.voicePreference);
    if (!onboarding.voiceDownloadRequested || preference === "none") return null;
    if (!runtimeSnapshot().ready) {
      backgroundState = { state: "queued", currentId: "", currentName: "", message: "等待基础环境完成后自动继续", error: "" };
      return null;
    }
    if (voicePlanReady(preference)) {
      backgroundState = { state: "ready", currentId: "", currentName: "", message: "声音组件已就绪", error: "" };
      return null;
    }
    const plan = voicePlanForPreference(preference);
    const generation = backgroundGeneration;
    const task = (async () => {
      try {
        backgroundState = { state: "downloading", currentId: "", currentName: "", message: "声音组件已进入后台下载队列", error: "" };
        for (const id of plan) {
          if (generation !== backgroundGeneration) return;
          const current = componentManager.snapshot().items.find((item) => item.id === id);
          if (!current || current.ready) continue;
          if (id === "qwen3-vllm-runtime") {
            const preflight = await refreshQwenRuntimePreflight();
            if (!preflight.eligible) throw new Error(preflight.message);
          }
          const hardware = evaluateHardwareAvailability(id, runtimeManager.snapshot().system);
          if (!hardware.eligible) throw new Error(hardware.message);
          backgroundState = { state: "downloading", currentId: id, currentName: current.name, message: `正在后台准备 ${current.name}`, error: "" };
          await componentManager.download(id);
        }
        if (generation !== backgroundGeneration) return;
        if (!voicePlanReady(preference)) throw new Error("声音组件安装结束，但完整性检查尚未通过");
        const warning = await applyPreference(preference);
        onboardingUpdate({ voiceReadyAt: new Date().toISOString(), voiceReadyAcknowledgedAt: "" });
        backgroundState = { state: "ready", currentId: "", currentName: "", message: warning || "声音组件已就绪；文字对话无需等待", error: "" };
      } catch (error) {
        if (generation !== backgroundGeneration) return;
        backgroundState = { state: "error", currentId: backgroundState.currentId, currentName: backgroundState.currentName, message: "声音组件后台安装未完成；文字对话不受影响", error: String(error.message || error) };
      } finally {
        if (backgroundTask === task) backgroundTask = null;
        if (generation !== backgroundGeneration) setTimeout(() => { scheduleBackgroundDownload(); }, 0).unref?.();
      }
    })();
    backgroundTask = task;
    return backgroundTask;
  }

  async function reconcileSelected() {
    if (isQuitting() || reconcileTask) return reconcileTask;
    const task = (async () => {
      const provider = configuredProvider();
      if (!observedProvider) observedProvider = provider;
      const supervisor = getServiceSupervisor();
      if (provider !== observedProvider) {
        const previousProvider = observedProvider;
        observedProvider = provider;
        const preference = voicePreferenceFromProvider(provider);
        const ready = preference === "none" || voicePlanReady(preference);
        backgroundGeneration += 1;
        if (backgroundTask && backgroundState.currentId) getComponentManager()?.cancel(backgroundState.currentId);
        onboardingUpdate({ voicePreference: preference, voiceSelectionConfirmed: true, voiceDownloadRequested: preference !== "none" && !ready, voiceReadyAt: ready && preference !== "none" ? new Date().toISOString() : "", voiceReadyAcknowledgedAt: ready ? new Date().toISOString() : "" });
        if (isLocalProvider(previousProvider)) await stopProviderService(previousProvider);
        if (!isLocalProvider(provider)) {
          backgroundState = { state: "idle", currentId: "", currentName: "", message: "当前未使用本地 TTS", error: "" };
          return { ok: true, local: false };
        }
        if (!ready) {
          backgroundState = { state: "queued", currentId: "", currentName: "", message: "应用内已切换声音，正在等待启动器补齐组件", error: "" };
          scheduleBackgroundDownload();
          return { ok: true, queued: true };
        }
        const selectedService = serviceName(provider);
        supervisor.setDesired(selectedService);
        const switched = await ensureSelectedService();
        if (!switched.ok && !switched.cancelled) recordServiceEvent("service.tts_provider_switch_failed", { previous_provider: previousProvider, provider, service: selectedService, error: switched.error || "unknown" });
        return switched;
      }
      if (!isLocalProvider(provider)) return { ok: true, local: false };
      const selected = serviceName(provider);
      if (!supervisor.hasDesired(selected)) return { ok: true, idle: true };
      if (!supervisor.hasChild(selected) && (await probe(services[selected])).online) return { ok: true, alreadyRunning: true };
      if (transition.state === "failed" && transition.target === selected) {
        const failedAt = Date.parse(transition.startedAt || "") || Date.now();
        if (Date.now() - failedAt < 20_000) return { ok: false, coolingDown: true };
      }
      const result = await ensureSelectedService();
      if (!result.ok && !result.cancelled) recordServiceEvent("service.tts_provider_switch_failed", { provider, service: selected, error: result.error || "unknown" });
      return result;
    })();
    reconcileTask = task;
    try { return await task; }
    finally { if (reconcileTask === task) reconcileTask = null; }
  }

  async function selectVoice(id) {
    const voice = GPT_SOVITS_VOICES.find((candidate) => candidate.id === id);
    if (!voice) throw new Error("未知 GPT-SoVITS 音色");
    if (!getRuntimeManager()?.snapshot().system.nvidia) throw new Error("GPT-SoVITS 本地推理需要兼容的 NVIDIA 显卡与驱动");
    const component = getComponentManager()?.snapshot().items.find((candidate) => candidate.id === voice.componentId);
    if (!component?.ready) throw new Error(`${voice.label} 尚未下载，请先点击“单独下载”`);
    observedProvider = "gpt-sovits";
    onboardingUpdate({ voicePreference: "gpt-sovits", voiceSelectionConfirmed: true, voiceDownloadRequested: false, voiceReadyAt: new Date().toISOString(), voiceReadyAcknowledgedAt: new Date().toISOString() });
    const warning = await syncProductSettings({ audio: { tts_provider: "gpt-sovits", tts_gpt_sovits_voice: voice.id } });
    const started = await ensureSelectedService();
    return { ok: started.ok, error: started.error, warning, ...snapshot() };
  }

  async function installVoice(id) {
    const voice = GPT_SOVITS_VOICES.find((candidate) => candidate.id === id);
    if (!voice) throw new Error("未知 GPT-SoVITS 音色");
    if (!getRuntimeManager()?.snapshot().system.nvidia) throw new Error("GPT-SoVITS 本地推理需要兼容的 NVIDIA 显卡与驱动");
    const component = getComponentManager()?.snapshot().items.find((candidate) => candidate.id === voice.componentId);
    await runtimeAction(component?.status === "error" || component?.partial ? "retry" : "install", voice.componentId);
    return snapshot();
  }

  function retryBackground() {
    backgroundState = { state: "queued", currentId: "", currentName: "", message: "正在重新加入后台队列", error: "" };
    scheduleBackgroundDownload();
  }

  function registerIpc(ipcMain) {
    ipcMain.handle("launcher:voice", async (_, { action, id } = {}) => {
      if (action === "snapshot") return snapshot();
      if (action === "install") return installVoice(id);
      if (action === "select") return selectVoice(id);
      if (action === "provider") return selectProvider(id, { startIfReady: true, requestDownload: true });
      throw new Error("未知音色操作");
    });
  }

  return {
    applyPreference, backgroundSnapshot: () => backgroundState, configuredGptVoiceComponent,
    configuredProvider, configuredVoice, ensureSelectedService,
    initializeObservedProvider: () => { observedProvider = configuredProvider(); }, installVoice,
    isLocalProvider, reconcileSelected, registerIpc,
    resetTransition: () => { transition = { state: "idle", target: "", error: "", startedAt: "" }; },
    retryBackground, scheduleBackgroundDownload, selectProvider, selectVoice, serviceName, snapshot,
    transitionSnapshot: () => transition, voices: GPT_SOVITS_VOICES,
  };
}

module.exports = { createVoiceController };

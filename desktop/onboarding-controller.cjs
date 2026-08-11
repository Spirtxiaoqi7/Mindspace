const { ONBOARDING_VERSION, deriveOnboardingSnapshot, normalizeVoicePreference, voicePreferenceFromProvider } = require("./onboarding-policy.cjs");

function createOnboardingController({
  configuredLlm, fetch, getComponentManager, getSettingsController, getVoiceController,
  normalizeLlmInput, readLauncherConfig, runtimeAction, runtimeSnapshot, writeLauncherConfig,
}) {
  function snapshot() {
    const launcherConfig = readLauncherConfig();
    const voice = getVoiceController();
    const providerPreference = voicePreferenceFromProvider(voice.configuredProvider());
    return deriveOnboardingSnapshot({
      runtime: runtimeSnapshot(), llm: configuredLlm(),
      launcherConfig: { ...launcherConfig, onboarding: { ...(launcherConfig.onboarding || {}), voicePreference: providerPreference } },
      componentItems: getComponentManager()?.snapshot().items || [],
      voiceComponentId: voice.configuredGptVoiceComponent(), voiceBackground: voice.backgroundSnapshot(),
    });
  }

  function update(patch) {
    const config = readLauncherConfig();
    const onboarding = { version: ONBOARDING_VERSION, ...(config.onboarding || {}), ...patch };
    if (snapshot().baseReady && snapshot().llmReady && !onboarding.completedAt) onboarding.completedAt = new Date().toISOString();
    writeLauncherConfig({ ...config, onboarding });
    return onboarding;
  }

  async function testLlmConfiguration(payload = {}) {
    const llm = normalizeLlmInput(payload);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20_000);
    try {
      const response = await fetch(`${llm.base_url}/chat/completions`, {
        method: "POST", headers: { "Content-Type": "application/json", ...(llm.api_key ? { Authorization: `Bearer ${llm.api_key}` } : {}) },
        body: JSON.stringify({ model: llm.model, messages: [{ role: "user", content: "请只回复：好" }], max_tokens: 2, stream: false }), signal: controller.signal,
      });
      if (!response.ok) {
        const codes = { 401: "API Key 无效或没有权限", 402: "账户余额不足", 404: "API 地址或模型名称不存在", 429: "服务请求过于频繁，请稍后重试" };
        throw new Error(codes[response.status] || `模型服务返回 HTTP ${response.status}`);
      }
      return { ok: true, llm };
    } catch (error) {
      if (controller.signal.aborted) throw new Error("连接模型服务超时，请检查网络或 API 地址");
      throw error;
    } finally { clearTimeout(timer); }
  }

  async function saveLlmConfiguration(payload = {}) {
    const tested = await testLlmConfiguration(payload);
    const saved = await getSettingsController().save({ llm: tested.llm });
    if (!saved.ok) throw new Error(saved.error || "设置保存失败");
    const current = snapshot();
    update({ llmConfiguredAt: new Date().toISOString(), ...(current.baseReady ? { completedAt: new Date().toISOString() } : {}) });
    return { ok: true, warning: "", onboarding: snapshot() };
  }

  function registerIpc(ipcMain) {
    ipcMain.handle("launcher:onboarding", async (_, { action, payload = {} } = {}) => {
      const voice = getVoiceController();
      if (action === "snapshot") return snapshot();
      if (action === "select-voice") {
        const preference = normalizeVoicePreference(payload.preference);
        const before = snapshot();
        await voice.selectProvider(preference, { startIfReady: before.complete, requestDownload: before.complete });
        return snapshot();
      }
      if (action === "install-base") {
        const current = readLauncherConfig().onboarding || {};
        update({ voiceDownloadRequested: normalizeVoicePreference(current.voicePreference) !== "none" });
        await runtimeAction("install-all");
        return snapshot();
      }
      if (action === "test-llm") { await testLlmConfiguration(payload); return { ok: true, message: "模型连接成功" }; }
      if (action === "save-llm") return saveLlmConfiguration(payload);
      if (action === "retry-voice") {
        update({ voiceDownloadRequested: true, voiceReadyAcknowledgedAt: "" });
        voice.retryBackground();
        return snapshot();
      }
      if (action === "acknowledge-voice") {
        const preference = snapshot().voicePreference;
        await voice.applyPreference(preference);
        if (payload.restart) {
          const result = await voice.ensureSelectedService();
          if (!result.ok) throw new Error(result.error || "声音服务启动失败");
        }
        update({ voiceReadyAcknowledgedAt: new Date().toISOString() });
        return { ok: true, onboarding: snapshot() };
      }
      if (action === "finish") {
        const current = snapshot();
        if (!current.complete) throw new Error("基础环境与 LLM 尚未全部就绪");
        update({ completedAt: new Date().toISOString() });
        return snapshot();
      }
      throw new Error("未知首次配置操作");
    });
  }

  return { registerIpc, snapshot, update };
}

module.exports = { createOnboardingController };

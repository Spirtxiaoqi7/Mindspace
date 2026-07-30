const ONBOARDING_VERSION = 2;

const VOICE_PREFERENCES = new Set([
  "none",
  "gpt-sovits",
  "cosyvoice",
  "qwen3-vllm",
]);

const LLM_PRESETS = Object.freeze({
  deepseek: {
    id: "deepseek",
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-v4-flash",
    keyUrl: "https://platform.deepseek.com/api_keys",
    docsUrl: "https://api-docs.deepseek.com/",
  },
  siliconflow: {
    id: "siliconflow",
    label: "SiliconFlow",
    baseUrl: "https://api.siliconflow.cn/v1",
    model: "deepseek-ai/DeepSeek-V3.2",
    keyUrl: "https://cloud.siliconflow.cn/account/ak",
    docsUrl: "https://docs.siliconflow.cn/cn/userguide/quickstart",
  },
  custom: {
    id: "custom",
    label: "自定义兼容接口",
    baseUrl: "",
    model: "",
    keyUrl: "",
    docsUrl: "",
  },
});

function normalizeVoicePreference(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return VOICE_PREFERENCES.has(normalized) ? normalized : "none";
}

function voicePreferenceFromProvider(provider) {
  return normalizeVoicePreference(provider);
}

function isLoopbackUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["localhost", "127.0.0.1", "::1"].includes(url.hostname);
  } catch {
    return false;
  }
}

function llmConfigurationReady(llm = {}) {
  const mode = String(llm.mode || "openai").toLowerCase();
  const baseUrl = String(llm.base_url || "").trim();
  const model = String(llm.model || "").trim();
  const hasCredential = Boolean(String(llm.api_key || "").trim());
  return mode === "openai"
    && /^https?:\/\//i.test(baseUrl)
    && Boolean(model)
    && (hasCredential || isLoopbackUrl(baseUrl));
}

function voiceInstallPlan(preference, voiceComponentId = "gpt-sovits-v4-changli") {
  switch (normalizeVoicePreference(preference)) {
    case "gpt-sovits":
      return ["gpt-sovits-runtime", voiceComponentId];
    case "cosyvoice":
      return ["tts-runtime", "tts"];
    case "qwen3-vllm":
      return ["qwen3-vllm-runtime"];
    default:
      return [];
  }
}

function averageProgress(items) {
  if (!items.length) return 100;
  return items.reduce((sum, item) => sum + (item.ready ? 100 : Number(item.progress || 0)), 0) / items.length;
}

function deriveOnboardingSnapshot({
  runtime = {},
  llm = {},
  launcherConfig = {},
  componentItems = [],
  voiceComponentId = "gpt-sovits-v4-changli",
  voiceBackground = {},
} = {}) {
  const onboarding = launcherConfig.onboarding || {};
  const preference = normalizeVoicePreference(onboarding.voicePreference);
  const plan = voiceInstallPlan(preference, voiceComponentId);
  const planItems = plan.map((id) => componentItems.find((item) => item.id === id) || {
    id,
    ready: false,
    progress: 0,
    status: "idle",
    message: "等待下载",
  });
  const baseReady = Boolean(runtime.ready);
  const llmReady = llmConfigurationReady(llm);
  const voiceReady = preference === "none" || (planItems.length > 0 && planItems.every((item) => item.ready));
  const voiceRequested = Boolean(onboarding.voiceDownloadRequested);
  const voiceNeedsNotice = preference !== "none"
    && voiceRequested
    && voiceReady
    && !onboarding.voiceReadyAcknowledgedAt;
  const complete = baseReady && llmReady;
  const step = !onboarding.voiceSelectionConfirmed
    ? "voice"
    : !baseReady
      ? "install"
      : !llmReady
        ? "llm"
        : "ready";
  const activePlanItem = planItems.find((item) => [
    "resolving", "checking", "downloading", "verifying", "installing",
  ].includes(item.status));

  return {
    version: ONBOARDING_VERSION,
    showWizard: !complete,
    complete,
    completedAt: complete ? String(onboarding.completedAt || "") : "",
    step,
    baseReady,
    llmReady,
    voicePreference: preference,
    voiceSelectionConfirmed: Boolean(onboarding.voiceSelectionConfirmed),
    voiceRequested,
    voiceReady,
    voiceNeedsNotice,
    voice: {
      state: voiceBackground.state || (
        voiceReady ? "ready" : voiceRequested ? activePlanItem ? "downloading" : "queued" : "idle"
      ),
      progress: averageProgress(planItems),
      currentId: activePlanItem?.id || voiceBackground.currentId || "",
      currentName: activePlanItem?.name || voiceBackground.currentName || "",
      message: voiceBackground.message || activePlanItem?.message || (
        voiceReady ? "声音组件已就绪" : voiceRequested ? "等待基础环境完成" : "尚未开始下载"
      ),
      error: voiceBackground.error || planItems.find((item) => item.error)?.error || "",
      plan,
    },
    llm: {
      mode: String(llm.mode || "openai"),
      baseUrl: String(llm.base_url || LLM_PRESETS.deepseek.baseUrl),
      model: String(llm.model || LLM_PRESETS.deepseek.model),
      credentialsConfigured: Boolean(String(llm.api_key || "").trim()),
      localEndpoint: isLoopbackUrl(llm.base_url),
    },
    presets: Object.values(LLM_PRESETS),
  };
}

module.exports = {
  LLM_PRESETS,
  ONBOARDING_VERSION,
  deriveOnboardingSnapshot,
  isLoopbackUrl,
  llmConfigurationReady,
  normalizeVoicePreference,
  voicePreferenceFromProvider,
  voiceInstallPlan,
};

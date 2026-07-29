const assert = require("node:assert/strict");
const test = require("node:test");

const {
  deriveOnboardingSnapshot,
  llmConfigurationReady,
  voicePreferenceFromProvider,
  voiceInstallPlan,
} = require("./onboarding-policy.cjs");

test("text-only onboarding completes without any voice component", () => {
  const snapshot = deriveOnboardingSnapshot({
    runtime: { ready: true },
    llm: {
      mode: "openai",
      base_url: "https://api.deepseek.com",
      model: "deepseek-v4-flash",
      api_key: "configured",
    },
    launcherConfig: {
      onboarding: {
        voiceSelectionConfirmed: true,
        voicePreference: "none",
      },
    },
  });
  assert.equal(snapshot.complete, true);
  assert.equal(snapshot.voiceReady, true);
  assert.deepEqual(snapshot.voice.plan, []);
  assert.equal(snapshot.showWizard, false);
});

test("remote LLM needs a key while a loopback compatible endpoint does not", () => {
  assert.equal(llmConfigurationReady({
    mode: "openai",
    base_url: "https://api.deepseek.com",
    model: "deepseek-v4-flash",
  }), false);
  assert.equal(llmConfigurationReady({
    mode: "openai",
    base_url: "http://127.0.0.1:8000/v1",
    model: "local-model",
  }), true);
});

test("voice installation plans keep optional engines separate from base readiness", () => {
  assert.deepEqual(voiceInstallPlan("none"), []);
  assert.deepEqual(voiceInstallPlan("cosyvoice"), ["tts-runtime", "tts"]);
  assert.deepEqual(voiceInstallPlan("qwen3-vllm"), ["qwen3-vllm-runtime"]);
  assert.deepEqual(
    voiceInstallPlan("gpt-sovits", "gpt-sovits-v4-changli"),
    ["gpt-sovits-runtime", "gpt-sovits-v4-changli"],
  );
});

test("launcher voice preference follows the product TTS provider", () => {
  assert.equal(voicePreferenceFromProvider("gpt-sovits"), "gpt-sovits");
  assert.equal(voicePreferenceFromProvider("cosyvoice"), "cosyvoice");
  assert.equal(voicePreferenceFromProvider("qwen3-vllm"), "qwen3-vllm");
  assert.equal(voicePreferenceFromProvider("browser"), "none");
  assert.equal(voicePreferenceFromProvider("siliconflow"), "none");
});

test("completed base and LLM do not wait for a queued optional voice", () => {
  const snapshot = deriveOnboardingSnapshot({
    runtime: { ready: true },
    llm: {
      mode: "openai",
      base_url: "https://api.deepseek.com",
      model: "deepseek-v4-flash",
      api_key: "configured",
    },
    launcherConfig: {
      onboarding: {
        voiceSelectionConfirmed: true,
        voicePreference: "cosyvoice",
        voiceDownloadRequested: true,
      },
    },
    componentItems: [
      { id: "tts-runtime", ready: true, progress: 100, status: "ready" },
      { id: "tts", ready: false, progress: 45, status: "downloading", name: "CosyVoice 模型" },
    ],
  });
  assert.equal(snapshot.complete, true);
  assert.equal(snapshot.voiceReady, false);
  assert.equal(snapshot.voice.state, "downloading");
  assert.equal(snapshot.showWizard, false);
});

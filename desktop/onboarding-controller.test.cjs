const assert = require("node:assert/strict");
const test = require("node:test");

const { createOnboardingController, createOpenAiCompatibleFetch } = require("./onboarding-controller.cjs");

function fixture({ fetch, onCompleted = async () => ({ ok: true }) } = {}) {
  let config = { onboarding: { voiceSelectionConfirmed: true } };
  let llm = {};
  let handler;
  const controller = createOnboardingController({
    configuredLlm: () => llm,
    fetch,
    getComponentManager: () => ({ snapshot: () => ({ items: [] }) }),
    getSettingsController: () => ({
      save: async ({ llm: next }) => {
        llm = { ...next };
        return { ok: true };
      },
    }),
    getVoiceController: () => ({
      backgroundSnapshot: () => ({}), configuredGptVoiceComponent: () => "gpt-sovits-v4-changli",
      configuredProvider: () => "none", selectProvider: async () => {}, applyPreference: async () => {},
      ensureSelectedService: async () => ({ ok: true }), retryBackground: () => {},
    }),
    normalizeLlmInput: (payload) => ({ mode: "openai", ...payload }),
    onCompleted,
    readLauncherConfig: () => config,
    runtimeAction: async () => ({ ok: true }),
    runtimeSnapshot: () => ({ ready: true }),
    writeLauncherConfig: (next) => { config = next; },
  });
  controller.registerIpc({ handle: (_, callback) => { handler = callback; } });
  return { invoke: (action, payload) => handler({}, { action, payload }), controller };
}

test("external OpenAI-compatible endpoints bypass Electron net.fetch while loopback keeps it", async () => {
  const calls = [];
  const fetch = createOpenAiCompatibleFetch({
    externalFetch: async (input) => { calls.push(["external", input]); return { ok: true }; },
    localFetch: async (input) => { calls.push(["local", input]); return { ok: true }; },
  });

  await fetch("https://api.deepseek.com/chat/completions");
  await fetch("http://127.0.0.1:8765/api/v1/settings");

  assert.deepEqual(calls, [
    ["external", "https://api.deepseek.com/chat/completions"],
    ["local", "http://127.0.0.1:8765/api/v1/settings"],
  ]);
});

test("desktop onboarding save starts Core and opens the main interface after a successful external probe", async () => {
  const events = [];
  const { invoke } = fixture({
    fetch: createOpenAiCompatibleFetch({
      externalFetch: async () => { events.push("external-probe"); return { ok: true, status: 200 }; },
      localFetch: async () => { throw new Error("external endpoint used Electron net.fetch"); },
    }),
    onCompleted: async () => {
      events.push("core-start");
      events.push("main-open");
      return { ok: true };
    },
  });

  const result = await invoke("save-llm", {
    base_url: "https://api.deepseek.com", api_key: "test-key", model: "deepseek-chat",
  });

  assert.equal(result.ok, true);
  assert.equal(result.warning, "");
  assert.equal(result.onboarding.complete, true);
  assert.equal(result.onboarding.showWizard, false);
  assert.deepEqual(events, ["external-probe", "core-start", "main-open"]);
});

test("model connection failures retain a user-facing mapping", async () => {
  const { invoke } = fixture({
    fetch: async () => {
      const error = new Error("net::ERR_CONNECTION_REFUSED");
      error.code = "ECONNREFUSED";
      throw error;
    },
  });

  await assert.rejects(
    () => invoke("test-llm", { base_url: "https://api.deepseek.com", api_key: "test-key", model: "deepseek-chat" }),
    /无法连接模型服务/,
  );
});

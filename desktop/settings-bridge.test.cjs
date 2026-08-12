const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createSecretStore, createSettingsSaveCoordinator, enhanceCredentialStatus } = require("./secret-store.cjs");

function secureStorage() {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (value) => Buffer.from(`protected:${value}`, "utf8"),
    decryptString: (value) => value.toString("utf8").replace(/^protected:/, ""),
  };
}

function fixture(context) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-settings-bridge-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const secretFile = path.join(root, "state", "secrets.json");
  const publicFile = path.join(root, "data", "config", "settings.json");
  const store = createSecretStore({ file: secretFile, safeStorage: secureStorage() });
  const received = [];
  const patchCore = async (patch) => {
    received.push(structuredClone(patch));
    const publicSettings = structuredClone(patch);
    delete publicSettings.secret_operations;
    if (publicSettings.llm) delete publicSettings.llm.api_key;
    if (publicSettings.audio) {
      delete publicSettings.audio.asr_api_key;
      delete publicSettings.audio.tts_siliconflow_api_key;
    }
    fs.mkdirSync(path.dirname(publicFile), { recursive: true });
    fs.writeFileSync(publicFile, JSON.stringify(publicSettings), "utf8");
    return { success: true, settings: publicSettings };
  };
  return { root, secretFile, publicFile, store, received, patchCore };
}

test("desktop save encrypts secrets, applies them to Core, and survives store restart", async (context) => {
  const state = fixture(context);
  const coordinator = createSettingsSaveCoordinator({ secretStore: state.store, patchCore: state.patchCore });

  const result = await coordinator.save({
    llm: { model: "deepseek-chat", api_key: "llm-secret" },
    audio: { asr_api_key: "asr-secret", tts_siliconflow_api_key: "tts-secret" },
  });

  assert.equal(result.ok, true);
  assert.equal(state.received[0].llm.api_key, "llm-secret");
  const publicText = fs.readFileSync(state.publicFile, "utf8");
  assert.equal(publicText.includes("api_key"), false);
  assert.equal(publicText.includes("llm-secret"), false);
  const encryptedText = fs.readFileSync(state.secretFile, "utf8");
  assert.equal(encryptedText.includes("llm-secret"), false);
  const restarted = createSecretStore({ file: state.secretFile, safeStorage: secureStorage() });
  assert.equal(restarted.get("llm_api_key"), "llm-secret");
  assert.equal(restarted.get("asr_api_key"), "asr-secret");
  assert.equal(restarted.get("tts_siliconflow_api_key"), "tts-secret");
});

test("a Core rejection rolls encrypted credentials back to their exact prior state", async (context) => {
  const state = fixture(context);
  state.store.set("llm_api_key", "old-secret");
  const before = fs.readFileSync(state.secretFile, "utf8");
  const coordinator = createSettingsSaveCoordinator({
    secretStore: state.store,
    patchCore: async () => { const error = new Error("Core rejected public model"); error.status = 422; throw error; },
  });

  const result = await coordinator.save({ llm: { api_key: "new-secret" } });

  assert.equal(result.ok, false);
  assert.equal(result.phase, "core");
  assert.equal(state.store.get("llm_api_key"), "old-secret");
  assert.equal(fs.readFileSync(state.secretFile, "utf8"), before);
});

test("a credential file scoped to another installation is never read", async (context) => {
  const state = fixture(context);
  const first = createSecretStore({ file: state.secretFile, safeStorage: secureStorage(), scopeRoot: path.join(state.root, "first") });
  first.set("llm_api_key", "first-install-secret");
  const second = createSecretStore({ file: state.secretFile, safeStorage: secureStorage(), scopeRoot: path.join(state.root, "second") });

  assert.equal(second.get("llm_api_key"), "");
  assert.equal(second.status("llm_api_key").source, "secure_storage_scope_mismatch");
  assert.equal(fs.readFileSync(state.secretFile, "utf8").includes("first-install-secret"), false);
});

test("Core failure leaves the prior encrypted secret unchanged", async (context) => {
  const state = fixture(context);
  state.store.set("llm_api_key", "old-secret");
  const coordinator = createSettingsSaveCoordinator({
    secretStore: state.store,
    patchCore: async () => { const error = new Error("Core rejected public model"); error.status = 422; throw error; },
  });

  const result = await coordinator.save({ llm: { api_key: "new-secret" } });

  assert.equal(result.ok, false);
  assert.equal(result.phase, "core");
  assert.equal(result.core_applied, false);
  assert.equal(state.store.get("llm_api_key"), "old-secret");
  assert.equal(fs.readFileSync(state.secretFile, "utf8").includes("new-secret"), false);
});

test("secure-store failure prevents Core from receiving a replacement secret", async (context) => {
  const state = fixture(context);
  state.store.set("llm_api_key", "old-secret");
  const failingStore = createSecretStore({
    file: state.secretFile,
    safeStorage: {
      ...secureStorage(),
      encryptString: () => { throw new Error("injected safeStorage failure"); },
    },
  });
  const coordinator = createSettingsSaveCoordinator({ secretStore: failingStore, patchCore: state.patchCore });

  const result = await coordinator.save({ llm: { api_key: "new-secret", model: "new-model" } });

  assert.equal(result.ok, false);
  assert.equal(result.phase, "secret_store");
  assert.equal(result.core_applied, false);
  assert.equal(result.secret_persisted, false);
  assert.equal(result.retryable, true);
  assert.equal(state.received.length, 0);
  assert.equal(state.store.get("llm_api_key"), "old-secret");
  assert.equal(fs.readFileSync(state.secretFile, "utf8").includes("new-secret"), false);
});

test("explicit clear is distinct from an omitted or empty secret", async (context) => {
  const state = fixture(context);
  state.store.set("llm_api_key", "old-secret");
  const coordinator = createSettingsSaveCoordinator({ secretStore: state.store, patchCore: state.patchCore });

  await coordinator.save({ llm: { api_key: "" } });
  assert.equal(state.store.get("llm_api_key"), "old-secret");

  const cleared = await coordinator.save({ secret_operations: { llm_api_key: "clear" } });
  assert.equal(cleared.ok, true);
  assert.equal(state.received.at(-1).secret_operations.llm_api_key, "clear");
  assert.equal(state.store.get("llm_api_key"), "");
  assert.equal(fs.readFileSync(state.secretFile, "utf8").includes("old-secret"), false);
});

test("desktop GET reports secure persistence immediately after save", async (context) => {
  const state = fixture(context);
  const coordinator = createSettingsSaveCoordinator({ secretStore: state.store, patchCore: state.patchCore });
  const saved = await coordinator.save({ llm: { api_key: "saved-secret", model: "deepseek-chat" } });

  const fetched = enhanceCredentialStatus(saved.payload, state.store);

  assert.equal(fetched.settings.llm.credentials_configured, true);
  assert.equal(fetched.settings.llm.credentials_persisted, true);
  assert.equal(fetched.settings.llm.credentials_persistence, "secure_storage");
  assert.equal(fetched.settings.llm.credentials_source, "secure_storage");
  assert.equal(JSON.stringify(fetched).includes("saved-secret"), false);
});

test("desktop GET after simulated restart derives status from the same encrypted store", async (context) => {
  const state = fixture(context);
  state.store.set("llm_api_key", "restart-secret");
  const restarted = createSecretStore({ file: state.secretFile, safeStorage: secureStorage() });

  const fetched = enhanceCredentialStatus({ llm: {}, audio: {} }, restarted);

  assert.equal(fetched.llm.credentials_configured, true);
  assert.equal(fetched.llm.credentials_persisted, true);
  assert.equal(fetched.llm.credentials_source, "secure_storage");
  assert.equal(JSON.stringify(fetched).includes("restart-secret"), false);
});

test("desktop GET reports not persisted after explicit clear", async (context) => {
  const state = fixture(context);
  state.store.set("llm_api_key", "clear-secret");
  const coordinator = createSettingsSaveCoordinator({ secretStore: state.store, patchCore: state.patchCore });
  await coordinator.save({ secret_operations: { llm_api_key: "clear" } });

  const fetched = enhanceCredentialStatus({ llm: {}, audio: {} }, state.store);

  assert.equal(fetched.llm.credentials_configured, false);
  assert.equal(fetched.llm.credentials_persisted, false);
  assert.equal(fetched.llm.credentials_persistence, "secure_storage");
});

test("desktop GET distinguishes an encrypted secret when safeStorage is unavailable", async (context) => {
  const state = fixture(context);
  state.store.set("llm_api_key", "unavailable-secret");
  const unavailable = createSecretStore({
    file: state.secretFile,
    safeStorage: { isEncryptionAvailable: () => false },
  });

  const fetched = enhanceCredentialStatus({ llm: {}, audio: {} }, unavailable);

  assert.equal(fetched.llm.credentials_configured, false);
  assert.equal(fetched.llm.credentials_persisted, true);
  assert.equal(fetched.llm.credentials_available, false);
  assert.equal(fetched.llm.credentials_source, "secure_storage_unavailable");
  assert.equal(JSON.stringify(fetched).includes("unavailable-secret"), false);
});

test("desktop GET does not report secure storage available merely because it is empty", async (context) => {
  const state = fixture(context);
  const unavailable = createSecretStore({
    file: state.secretFile,
    safeStorage: { isEncryptionAvailable: () => false },
  });

  const fetched = enhanceCredentialStatus({ llm: {}, audio: {} }, unavailable);

  assert.equal(fetched.llm.credentials_configured, false);
  assert.equal(fetched.llm.credentials_persisted, false);
  assert.equal(fetched.llm.credentials_available, false);
  assert.equal(fetched.llm.credentials_source, "secure_storage_unavailable");
});

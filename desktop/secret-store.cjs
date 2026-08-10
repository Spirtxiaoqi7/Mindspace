const fs = require("node:fs");
const path = require("node:path");

const SETTINGS_SECRET_FIELDS = Object.freeze([
  ["llm", "api_key", "llm_api_key"],
  ["audio", "asr_api_key", "asr_api_key"],
  ["audio", "tts_siliconflow_api_key", "tts_siliconflow_api_key"],
]);

function writeJsonAtomic(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.${Date.now()}.tmp`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
    fs.renameSync(temporary, file);
  } catch (error) {
    fs.rmSync(temporary, { force: true });
    throw error;
  }
}

function createSecretStore({ file, safeStorage }) {
  function assertAvailable() {
    if (!safeStorage?.isEncryptionAvailable?.()) throw new Error("Windows secure storage is unavailable; API credentials were not saved");
  }
  function readDocument() {
    try {
      const value = JSON.parse(fs.readFileSync(file, "utf8"));
      return value.schema_version === "1.0.0" ? value : { schema_version: "1.0.0", secrets: {} };
    } catch { return { schema_version: "1.0.0", secrets: {} }; }
  }
  function get(name) {
    const encoded = readDocument().secrets?.[name];
    if (!encoded) return "";
    assertAvailable();
    return safeStorage.decryptString(Buffer.from(encoded, "base64"));
  }
  function status(name) {
    if (!SETTINGS_SECRET_FIELDS.some((entry) => entry[2] === name)) throw new Error(`Unsupported secret field: ${name}`);
    const encoded = readDocument().secrets?.[name];
    const available = Boolean(safeStorage?.isEncryptionAvailable?.());
    if (!encoded) return {
      configured: false,
      persisted: false,
      source: available ? "secure_storage" : "secure_storage_unavailable",
      available,
    };
    if (!available) {
      return { configured: false, persisted: true, source: "secure_storage_unavailable", available: false };
    }
    try {
      const configured = Boolean(String(safeStorage.decryptString(Buffer.from(encoded, "base64")) || ""));
      return { configured, persisted: true, source: configured ? "secure_storage" : "secure_storage_empty", available: true };
    } catch {
      return { configured: false, persisted: true, source: "secure_storage_error", available: false };
    }
  }
  function set(name, value) {
    apply({ [name]: String(value || "") || null });
  }
  function apply(changes) {
    assertAvailable();
    const document = readDocument();
    document.secrets ||= {};
    for (const [name, value] of Object.entries(changes || {})) {
      if (!SETTINGS_SECRET_FIELDS.some((entry) => entry[2] === name)) throw new Error(`Unsupported secret field: ${name}`);
      const normalized = value == null ? "" : String(value).trim();
      if (normalized) document.secrets[name] = safeStorage.encryptString(normalized).toString("base64");
      else delete document.secrets[name];
    }
    writeJsonAtomic(file, document);
  }
  function migrateProductConfig(settingsFile) {
    if (!fs.existsSync(settingsFile)) return { migrated: [] };
    const config = JSON.parse(fs.readFileSync(settingsFile, "utf8"));
    const migrations = SETTINGS_SECRET_FIELDS;
    const migrated = [];
    for (const [section, field, secretName] of migrations) {
      const plaintext = String(config?.[section]?.[field] || "");
      if (!plaintext) continue;
      set(secretName, plaintext);
      delete config[section][field];
      migrated.push(`${section}.${field}`);
    }
    if (migrated.length) writeJsonAtomic(settingsFile, config);
    return { migrated };
  }
  return { apply, get, migrateProductConfig, set, status };
}

function enhanceCredentialStatus(payload, secretStore) {
  const enhanced = structuredClone(payload || {});
  const settings = enhanced.settings && typeof enhanced.settings === "object" ? enhanced.settings : enhanced;
  for (const [section, _field, secretName] of SETTINGS_SECRET_FIELDS) {
    const target = settings[section];
    if (!target || typeof target !== "object") continue;
    const prefix = secretName === "llm_api_key"
      ? "credentials"
      : secretName === "asr_api_key" ? "asr_credentials" : "tts_siliconflow_credentials";
    const status = secretStore.status(secretName);
    target[`${prefix}_configured`] = status.configured;
    target[`${prefix}_source`] = status.source;
    target[`${prefix}_persisted`] = status.persisted;
    target[`${prefix}_persistence`] = "secure_storage";
    target[`${prefix}_available`] = status.available;
  }
  return enhanced;
}

function prepareSettingsSave(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("Settings update must be an object");
  const serialized = JSON.stringify(payload);
  if (Buffer.byteLength(serialized, "utf8") > 1024 * 1024) throw new Error("Settings update is too large");
  const corePatch = JSON.parse(serialized);
  const rawOperations = corePatch.secret_operations;
  if (rawOperations != null && (typeof rawOperations !== "object" || Array.isArray(rawOperations))) {
    throw new Error("secret_operations must be an object");
  }
  const operations = rawOperations || {};
  const knownNames = new Set(SETTINGS_SECRET_FIELDS.map((entry) => entry[2]));
  for (const [name, operation] of Object.entries(operations)) {
    if (!knownNames.has(name)) throw new Error(`Unsupported secret operation: ${name}`);
    if (!['keep', 'clear'].includes(String(operation))) throw new Error(`Invalid secret operation for ${name}`);
  }
  const secretChanges = {};
  for (const [section, field, secretName] of SETTINGS_SECRET_FIELDS) {
    const sectionPatch = corePatch[section];
    if (sectionPatch && typeof sectionPatch === "object" && !Array.isArray(sectionPatch)
        && Object.prototype.hasOwnProperty.call(sectionPatch, field)) {
      const value = String(sectionPatch[field] || "").trim();
      if (value) secretChanges[secretName] = value;
      else delete sectionPatch[field];
    }
    if (operations[secretName] === "clear") {
      secretChanges[secretName] = null;
      if (sectionPatch && typeof sectionPatch === "object") delete sectionPatch[field];
    }
  }
  return { corePatch, secretChanges };
}

function createSettingsSaveCoordinator({ secretStore, patchCore }) {
  if (!secretStore?.apply || typeof patchCore !== "function") throw new Error("Settings save coordinator dependencies are invalid");
  return {
    async save(payload) {
      let prepared;
      try {
        prepared = prepareSettingsSave(payload);
      } catch (error) {
        return { ok: false, status: 400, phase: "validation", core_applied: false, secret_persisted: false, error: String(error.message || error) };
      }
      let corePayload;
      try {
        corePayload = await patchCore(prepared.corePatch);
      } catch (error) {
        return { ok: false, status: Number(error.status || 502), phase: "core", core_applied: false, secret_persisted: false, error: String(error.message || error) };
      }
      const hasSecretChanges = Object.keys(prepared.secretChanges).length > 0;
      if (hasSecretChanges) {
        try {
          secretStore.apply(prepared.secretChanges);
        } catch (error) {
          return {
            ok: false,
            status: 500,
            phase: "secret_store",
            core_applied: true,
            secret_persisted: false,
            retryable: true,
            error: `Core 已应用设置，但 Windows 安全存储失败；旧密钥仍保留，请重试：${String(error.message || error)}`,
          };
        }
      }
      return {
        ok: true,
        status: 200,
        phase: "complete",
        core_applied: true,
        secret_persisted: hasSecretChanges ? true : null,
        payload: corePayload,
      };
    },
  };
}

module.exports = {
  SETTINGS_SECRET_FIELDS,
  createSecretStore,
  createSettingsSaveCoordinator,
  enhanceCredentialStatus,
  prepareSettingsSave,
  writeJsonAtomic,
};

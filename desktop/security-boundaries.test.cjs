const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { classifyExternalUrl } = require("./external-navigation.cjs");
const { createSecretStore } = require("./secret-store.cjs");

test("external navigation rejects dangerous protocols and confirms unknown HTTPS hosts", () => {
  assert.equal(classifyExternalUrl("javascript:alert(1)").action, "deny");
  assert.equal(classifyExternalUrl("file:///C:/Windows/System32/cmd.exe").action, "deny");
  assert.equal(classifyExternalUrl("https://user:pass@example.com/").action, "deny");
  assert.equal(classifyExternalUrl("https://platform.deepseek.com/usage").action, "allow");
  assert.equal(classifyExternalUrl("https://example.com/news").action, "confirm");
});

test("legacy plaintext API keys migrate into the encrypted secret store", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-secrets-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const settings = path.join(root, "settings.json");
  const secrets = path.join(root, "secrets.json");
  fs.writeFileSync(settings, JSON.stringify({ llm: { model: "deepseek-chat", api_key: "secret-value" }, audio: {} }));
  const safeStorage = {
    isEncryptionAvailable: () => true,
    encryptString: (value) => Buffer.from(`protected:${value}`, "utf8"),
    decryptString: (value) => value.toString("utf8").replace(/^protected:/, ""),
  };
  const store = createSecretStore({ file: secrets, safeStorage });
  assert.deepEqual(store.migrateProductConfig(settings).migrated, ["llm.api_key"]);
  assert.equal(JSON.parse(fs.readFileSync(settings, "utf8")).llm.api_key, undefined);
  assert.equal(fs.readFileSync(secrets, "utf8").includes("secret-value"), false);
  assert.equal(store.get("llm_api_key"), "secret-value");
});

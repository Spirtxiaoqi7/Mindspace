const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { environmentForPorts, loadServicePorts } = require("./service-ports.cjs");

test("service port overrides are applied to every consumer from one registry", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-ports-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const file = path.join(root, "ports.json");
  fs.writeFileSync(file, JSON.stringify({ schema_version: "1.0.0", host: "127.0.0.1", services: { core: 8765, asr: 8766, tts: 5055, qwen: 8091 } }));
  const registry = loadServicePorts({ configPath: file, environment: { MINDSPACE_PORT: "18765", MINDSPACE_ASR_PORT: "18766", MINDSPACE_TTS_PORT: "15055", MINDSPACE_QWEN3_PORT: "18091" } });
  assert.equal(registry.services.core.health, "http://127.0.0.1:18765/api/v1/health");
  assert.deepEqual(environmentForPorts(registry), { MINDSPACE_PORT: "18765", MINDSPACE_ASR_PORT: "18766", MINDSPACE_TTS_PORT: "15055", MINDSPACE_QWEN3_PORT: "18091" });
});

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  SERVICE_START_ORDER,
  isFatalStartFailure,
  isStaleCore,
  productEntryState,
  serviceRestartDelay,
  shouldWaitForAsrBeforeLocalTts,
} = require("./service-policy.cjs");

test("core starts before optional local voice services", () => {
  assert.deepEqual(SERVICE_START_ORDER, ["api", "asr", "tts"]);
  assert.equal(isFatalStartFailure("api"), true);
  assert.equal(isFatalStartFailure("asr"), false);
  assert.equal(isFatalStartFailure("tts"), false);
});

test("TTS starts independently of ASR readiness", () => {
  assert.equal(shouldWaitForAsrBeforeLocalTts("gpt-sovits", true, { online: false }), false);
  assert.equal(shouldWaitForAsrBeforeLocalTts("cosyvoice", false, { online: true, detail: { ready: false } }), false);
  assert.equal(shouldWaitForAsrBeforeLocalTts("qwen3-vllm", false, { online: false }), false);
  assert.equal(shouldWaitForAsrBeforeLocalTts("siliconflow", true, { online: false }), false);
});

test("product entry depends on Core and degrades explicitly to text-only mode", () => {
  assert.deepEqual(productEntryState({ coreOnline: false, asrOnline: false }), {
    canEnter: false,
    mode: "text-only",
    notice: "本次启动未启用语音功能（VAD/ASR 未启动），可以正常进行文字对话。",
  });
  assert.deepEqual(productEntryState({ coreOnline: true, asrOnline: false }), {
    canEnter: true,
    mode: "text-only",
    notice: "本次启动未启用语音功能（VAD/ASR 未启动），可以正常进行文字对话。",
  });
  assert.deepEqual(productEntryState({ coreOnline: true, asrOnline: true }), {
    canEnter: true,
    mode: "voice-capable",
    notice: "",
  });
});

test("a running core from an older application is stale", () => {
  assert.equal(isStaleCore({ version: "0.4.4" }, "0.4.5"), true);
  assert.equal(isStaleCore({ version: "0.4.5" }, "0.4.5"), false);
  assert.equal(isStaleCore({}, "0.4.5"), false);
});

test("crashed services use bounded restart backoff", () => {
  assert.equal(serviceRestartDelay(1), 1000);
  assert.equal(serviceRestartDelay(2), 2500);
  assert.equal(serviceRestartDelay(3), 5000);
  assert.equal(serviceRestartDelay(4), null);
});

test("ASR startup trusts the installer marker instead of repeating CUDA imports", () => {
  const supervisor = fs.readFileSync(path.join(__dirname, "service-supervisor.cjs"), "utf8");
  assert.match(supervisor, /asrReadyMarker/);
  assert.doesNotMatch(supervisor, /runProcessCheck\(asrPython/);
  assert.doesNotMatch(supervisor, /fs\.rmSync\(asrReadyMarker/);
});

test("bulk startup starts local TTS without awaiting ASR cold load", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const bulkStart = main.match(/async function allServices\(action\) \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(bulkStart, /scheduleStartupHealthRecheck\(name\)/);
  assert.doesNotMatch(bulkStart, /await waitForServiceReady\("asr", 90_000\)/);
  assert.doesNotMatch(bulkStart, /startLocalTtsAfterAsr/);
});

test("default launcher startup requests Core only", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const startup = main.match(/async function startDefaultCore\(\) \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(startup, /startService\("api"\)/);
  assert.doesNotMatch(startup, /allServices\("start"\)/);
  assert.doesNotMatch(startup, /startService\("asr"\)/);
  assert.doesNotMatch(startup, /ensureSelectedTtsService/);
});

test("the product grants microphone access only to its loopback Core origin", () => {
  const windows = fs.readFileSync(path.join(__dirname, "product-windows.cjs"), "utf8");
  assert.match(windows, /configureProductMediaPermissions\(win\.webContents\.session\)/);
  assert.match(windows, /services\.api\.origin/);
  assert.match(windows, /permission === "media"/);
  assert.match(windows, /details\.mediaType === "audio"/);
  assert.match(windows, /details\.mediaType === "unknown"/);
});

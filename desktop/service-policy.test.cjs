const assert = require("node:assert/strict");
const test = require("node:test");
const { SERVICE_START_ORDER, isFatalStartFailure, isStaleCore, shouldWaitForAsrBeforeLocalTts } = require("./service-policy.cjs");

test("core starts before optional local voice services", () => {
  assert.deepEqual(SERVICE_START_ORDER, ["api", "asr", "tts"]);
  assert.equal(isFatalStartFailure("api"), true);
  assert.equal(isFatalStartFailure("asr"), false);
  assert.equal(isFatalStartFailure("tts"), false);
});

test("local TTS waits for ASR readiness while CUDA models are loading", () => {
  assert.equal(shouldWaitForAsrBeforeLocalTts("gpt-sovits", true, { online: false }), true);
  assert.equal(shouldWaitForAsrBeforeLocalTts("cosyvoice", false, { online: true, detail: { ready: false } }), true);
  assert.equal(shouldWaitForAsrBeforeLocalTts("gpt-sovits", false, { online: true, detail: { ready: true } }), false);
  assert.equal(shouldWaitForAsrBeforeLocalTts("siliconflow", true, { online: false }), false);
});

test("a running core from an older application is stale", () => {
  assert.equal(isStaleCore({ version: "0.4.4" }, "0.4.5"), true);
  assert.equal(isStaleCore({ version: "0.4.5" }, "0.4.5"), false);
  assert.equal(isStaleCore({}, "0.4.5"), false);
});

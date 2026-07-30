const assert = require("node:assert/strict");
const test = require("node:test");
const { GIB, evaluateHardwareAvailability, requirementKey } = require("./hardware-policy.cjs");

test("local voice requirements map to the correct service family", () => {
  assert.equal(requirementKey("asr-final"), "asr");
  assert.equal(requirementKey("gpt-sovits-v4-yinlin"), "gpt-sovits");
  assert.equal(requirementKey("tts-runtime"), "cosyvoice");
  assert.equal(requirementKey("qwen3-vllm-runtime"), "qwen3");
  assert.equal(requirementKey("embedding"), "");
});

test("hardware policy blocks only the unsupported local service", () => {
  const noGpu = evaluateHardwareAvailability("asr", { nvidia: false, memoryTotalBytes: 64 * GIB });
  assert.equal(noGpu.code, "NVIDIA_REQUIRED");
  const lowRam = evaluateHardwareAvailability("qwen3-vllm-runtime", {
    nvidia: true, memoryTotalBytes: 16 * GIB, vramTotalMiB: 24 * 1024,
  });
  assert.equal(lowRam.code, "RAM_INSUFFICIENT");
  const lowVram = evaluateHardwareAvailability("gpt-sovits-runtime", {
    nvidia: true, memoryTotalBytes: 32 * GIB, vramTotalMiB: 4 * 1024,
  });
  assert.equal(lowVram.code, "VRAM_INSUFFICIENT");
  assert.equal(evaluateHardwareAvailability("embedding", {}).eligible, true);
});

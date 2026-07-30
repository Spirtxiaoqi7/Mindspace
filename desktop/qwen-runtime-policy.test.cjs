const test = require("node:test");
const assert = require("node:assert/strict");
const { evaluateQwenRuntimePreflight } = require("./qwen-runtime-policy.cjs");

const ready = { system: { nvidia: true, memoryTotalBytes: 32 * 1024 ** 3 }, wslAvailable: true, distroAvailable: true, wslGpuAvailable: true, vramMiB: 16 * 1024 };

test("Qwen preflight refuses unsupported machines before any install", () => {
  assert.equal(evaluateQwenRuntimePreflight({ ...ready, system: { nvidia: false } }).code, "NVIDIA_REQUIRED");
  assert.equal(evaluateQwenRuntimePreflight({ ...ready, system: { nvidia: true, memoryTotalBytes: 16 * 1024 ** 3 } }).code, "RAM_INSUFFICIENT");
  assert.equal(evaluateQwenRuntimePreflight({ ...ready, wslAvailable: false }).code, "WSL2_REQUIRED");
  assert.equal(evaluateQwenRuntimePreflight({ ...ready, distroAvailable: false }).code, "WSL_DISTRO_REQUIRED");
  assert.equal(evaluateQwenRuntimePreflight({ ...ready, wslGpuAvailable: false }).code, "WSL_GPU_REQUIRED");
  assert.equal(evaluateQwenRuntimePreflight({ ...ready, vramMiB: 8_000 }).code, "VRAM_INSUFFICIENT");
});

test("Qwen preflight accepts only a clear managed runtime path", () => {
  assert.equal(evaluateQwenRuntimePreflight(ready).eligible, true);
  assert.equal(evaluateQwenRuntimePreflight({ ...ready, portConflict: true }).code, "QWEN_PORT_CONFLICT");
});

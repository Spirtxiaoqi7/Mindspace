const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
const productWindows = fs.readFileSync(path.join(__dirname, "product-windows.cjs"), "utf8");

test("hardware acceleration is disabled before Electron becomes ready", () => {
  const disabledAt = main.indexOf("app.disableHardwareAcceleration()");
  const readyAt = main.indexOf("app.whenReady()");

  assert.ok(disabledAt >= 0);
  assert.ok(readyAt > disabledAt);
});

test("desktop records and recovers renderer and GPU process failures", () => {
  assert.match(main, /app\.on\("render-process-gone"/);
  assert.match(main, /app\.on\("child-process-gone"/);
  assert.match(main, /details\.type === "GPU"/);
  assert.match(main, /recoverProductWindow\("gpu-process-gone"/);
  assert.match(main, /mindspace-stability\.log/);
});

test("chat window has a bounded load timeout and background throttling", () => {
  assert.match(productWindows, /timeoutMs: 15_000/);
  assert.match(productWindows, /backgroundThrottling: true/);
  assert.doesNotMatch(productWindows, /backgroundThrottling: false/);
  assert.match(productWindows, /backdrop-filter: none !important/);
});

const assert = require("node:assert/strict");
const test = require("node:test");

const { appendTail, runProcessCheck } = require("./process-check.cjs");

test("process checks do not block the launcher event loop", async () => {
  let timerFired = false;
  const timer = setTimeout(() => {
    timerFired = true;
  }, 10);
  const check = runProcessCheck(
    process.execPath,
    ["-e", "setTimeout(() => process.exit(0), 80)"],
    { timeoutMs: 1_000 },
  );

  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(timerFired, true);
  assert.equal((await check).status, 0);
  clearTimeout(timer);
});

test("process checks report a bounded timeout", async () => {
  const result = await runProcessCheck(
    process.execPath,
    ["-e", "setTimeout(() => process.exit(0), 5000)"],
    { timeoutMs: 30 },
  );

  assert.equal(result.status, null);
  assert.equal(result.timedOut, true);
  assert.match(result.error.message, /timed out/);
});

test("process check diagnostics retain only the useful tail", () => {
  assert.equal(appendTail("1234", "5678", 5), "45678");
});

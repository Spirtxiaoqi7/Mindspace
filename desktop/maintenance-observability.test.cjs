const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("maintenance work records real completion state and the launcher exposes it", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const renderer = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
  assert.match(main, /const maintenanceJobs = new Map\(\)/);
  assert.match(main, /function maintenanceSnapshot\(\)/);
  assert.match(main, /status: "succeeded"/);
  assert.match(main, /status: "failed"/);
  assert.match(main, /exitCode/);
  assert.match(main, /action === "snapshot"/);
  assert.match(main, /runMaintenanceRepair/);
  assert.match(main, /result\?\.ok === false/);
  assert.match(main, /for \(const name of \["asr", "tts", "qwenTts", "api"\]\) \{[\s\S]*try \{/);
  assert.match(renderer, /完成后会显示真实结果/);
  assert.match(renderer, /activeMaintenanceJob/);
  assert.match(renderer, /try \{\s*const result = await window\.launcher\.all\("stop"\)[\s\S]*finally \{ setBusy\(""\); \}/);
});

test("path and diagnostic opening returns the operating system error instead of false success", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const renderer = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
  assert.match(main, /const error = await shell\.openPath\(target\)/);
  assert.match(main, /无法打开目录/);
  assert.match(main, /诊断报告已生成，但无法打开目录/);
  assert.match(renderer, /async function openLauncherPath/);
  assert.match(renderer, /result\.ok \? `\$\{label\}已打开`/);
});

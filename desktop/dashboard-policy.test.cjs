const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("launcher dashboard groups components instead of flattening the homepage", () => {
  const source = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
  for (const panel of ["base", "capabilities", "downloads", "maintenance"]) {
    assert.match(source, new RegExp(`expanded\\.${panel}`));
  }
  assert.match(source, /failedItems\.some[\s\S]*setExpanded/);
  assert.match(source, /runtime\.pipeline/);
  assert.match(source, /导出诊断报告/);
  assert.doesNotMatch(source, /runtime\.items\.map\(\(item\) =>/);
});

test("character voices use grouped dropdowns and separate download from activation", () => {
  const source = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  assert.match(source, /作品分类/);
  assert.match(source, /人物音色/);
  assert.match(source, /单独下载/);
  assert.match(source, /设为当前/);
  assert.match(source, /voice-download-progress/);
  assert.match(source, /speedBps/);
  assert.match(source, /item\.category !== "voice"/);
  assert.doesNotMatch(source, /className="voice-grid"/);
  assert.match(main, /action === "install"/);
  assert.match(main, /尚未下载，请先点击“单独下载”/);
});

test("diagnostics are redacted and exposed through a dedicated IPC contract", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const preload = fs.readFileSync(path.join(__dirname, "preload.cjs"), "utf8");
  assert.match(main, /function redactDiagnosticText/);
  assert.match(main, /\[REDACTED\]/);
  assert.match(main, /runtime:diagnostics/);
  assert.match(preload, /diagnostics: \(\) => ipcRenderer\.invoke\("runtime:diagnostics"\)/);
});

test("product version is consistent across launcher, core, web and announcements", () => {
  const root = path.resolve(__dirname, "..");
  const desktop = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8"));
  const frontend = JSON.parse(fs.readFileSync(path.join(root, "frontend", "package.json"), "utf8"));
  const project = fs.readFileSync(path.join(root, "pyproject.toml"), "utf8");
  const appVersion = fs.readFileSync(path.join(root, "src", "mindspace_graph", "version.py"), "utf8");
  const history = JSON.parse(fs.readFileSync(path.join(root, "docs", "release-history.json"), "utf8"));
  const projectVersion = project.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
  const coreVersion = appVersion.match(/APP_VERSION\s*=\s*"([^"]+)"/)?.[1];
  assert.ok(projectVersion);
  assert.equal(desktop.version, projectVersion);
  assert.equal(frontend.version, projectVersion);
  assert.equal(coreVersion, projectVersion);
  assert.equal(history[0].version, projectVersion);
});

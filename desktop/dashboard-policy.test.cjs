const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("launcher dashboard groups components instead of flattening the homepage", () => {
  const source = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
  const styles = fs.readFileSync(path.join(__dirname, "src", "styles.css"), "utf8");
  for (const panel of ["base", "capabilities", "downloads", "maintenance"]) {
    assert.match(source, new RegExp(`expanded\\.${panel}`));
  }
  assert.match(source, /failedItems\.some[\s\S]*setExpanded/);
  assert.match(source, /runtime\.pipeline/);
  assert.match(source, /asrComponentIds/);
  assert.match(source, /service-install-progress/);
  assert.match(source, /asrInstallProgress/);
  assert.match(styles, /\.service-install-progress/);
  assert.match(source, /导出诊断报告/);
  assert.doesNotMatch(source, /runtime\.items\.map\(\(item\) =>/);
});

test("character voices use grouped dropdowns and separate download from activation", () => {
  const source = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const voice = fs.readFileSync(path.join(__dirname, "voice-controller.cjs"), "utf8");
  assert.match(source, /作品分类/);
  assert.match(source, /人物音色/);
  assert.match(source, /单独下载/);
  assert.match(source, /设为当前/);
  assert.match(source, /voice-download-progress/);
  assert.match(source, /speedBps/);
  assert.match(source, /item\.category !== "voice"/);
  assert.doesNotMatch(source, /className="voice-grid"/);
  assert.match(voice, /action === "install"/);
  assert.match(voice, /尚未下载，请先点击“单独下载”/);
  assert.match(main, /createVoiceController/);
});

test("voice providers stay switchable after onboarding and the wizard can go back", () => {
  const source = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const voice = fs.readFileSync(path.join(__dirname, "voice-controller.cjs"), "utf8");
  assert.match(source, /dashboardVoiceOptions/);
  for (const provider of ["gpt-sovits", "cosyvoice", "qwen3-vllm", "siliconflow"]) {
    assert.match(source, new RegExp(provider));
  }
  assert.match(source, /返回上一步/);
  assert.match(source, /之前填写和保存的状态都已保留/);
  assert.match(voice, /action === "provider"/);
  assert.match(voice, /previousProvider !== selected && supervisor\.hasChild\(targetService\)/);
  assert.match(voice, /return observedProvider \|\| "browser"/);
  assert.match(main, /createVoiceController/);
});

test("launcher snapshot exposes local voice discovery without an implicit install action", () => {
  const source = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  assert.match(main, /discoverLocalResources/);
  assert.match(main, /localResources/);
  assert.match(source, /本地语音资源发现/);
  assert.match(source, /发现可接入/);
  assert.match(source, /不兼容/);
  assert.match(source, /不会扫描磁盘、下载、复制或移动文件/);
  assert.doesNotMatch(source, /localResources[\s\S]{0,1000}runtimeAction\("install"/);
});

test("local resource attachment is explicit and preserves a no-download boundary", () => {
  const source = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const preload = fs.readFileSync(path.join(__dirname, "preload.cjs"), "utf8");
  assert.match(main, /launcher:local-resource/);
  assert.match(main, /showOpenDialog/);
  assert.match(preload, /localResource:/);
  assert.match(source, /选择并登记/);
  assert.match(source, /选择并迁入/);
  assert.match(source, /不能接入/);
});

test("diagnostics are redacted and exposed through a dedicated IPC contract", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const diagnostics = fs.readFileSync(path.join(__dirname, "diagnostics-controller.cjs"), "utf8");
  const preload = fs.readFileSync(path.join(__dirname, "preload.cjs"), "utf8");
  assert.match(diagnostics, /function redactDiagnosticText/);
  assert.match(diagnostics, /\[REDACTED\]/);
  assert.match(diagnostics, /\\\.install\\\.log/);
  assert.match(diagnostics, /diagnosticLogs\.add/);
  assert.match(main, /runtime:diagnostics/);
  assert.match(main, /createDiagnosticsController/);
  assert.match(preload, /diagnostics: \(\) => ipcRenderer\.invoke\("runtime:diagnostics"\)/);
});

test("Core, web, Launcher and announcements share the release version", () => {
  const root = path.resolve(__dirname, "..");
  const desktop = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8"));
  const frontend = JSON.parse(fs.readFileSync(path.join(root, "frontend", "package.json"), "utf8"));
  const project = fs.readFileSync(path.join(root, "pyproject.toml"), "utf8");
  const appVersion = fs.readFileSync(path.join(root, "src", "mindspace_graph", "version.py"), "utf8");
  const history = JSON.parse(fs.readFileSync(path.join(root, "docs", "release-history.json"), "utf8"));
  const projectVersion = project.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
  const coreVersion = appVersion.match(/APP_VERSION\s*=\s*"([^"]+)"/)?.[1];
  assert.ok(projectVersion);
  assert.match(desktop.version, /^\d+\.\d+\.\d+$/);
  assert.equal(frontend.version, projectVersion);
  assert.equal(coreVersion, projectVersion);
  assert.equal(history[0].version, projectVersion);
  assert.equal(desktop.version, projectVersion);
});

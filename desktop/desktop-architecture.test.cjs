const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("desktop assembly imports every helper used while creating host controllers", () => {
  const source = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  assert.match(source, /const \{ createOnboardingController, createOpenAiCompatibleFetch \} = require\("\.\/onboarding-controller\.cjs"\)/);
  assert.match(source, /fetch: createOpenAiCompatibleFetch\(/);
});

const desktop = __dirname;
const productionFiles = fs.readdirSync(desktop).filter((name) => name.endsWith(".cjs") && !name.endsWith(".test.cjs"));
const sources = Object.fromEntries(productionFiles.map((name) => [name, fs.readFileSync(path.join(desktop, name), "utf8")]));

test("main is an assembly root and each desktop controller has one explicit constructor", () => {
  const mainLines = sources["main.cjs"].split(/\r?\n/).length;
  assert.ok(mainLines < 2200, `main.cjs is still too large: ${mainLines} lines`);
  for (const [file, factory] of [
    ["service-supervisor.cjs", "createServiceSupervisor"],
    ["settings-controller.cjs", "createSettingsController"],
    ["product-windows.cjs", "createProductWindows"],
    ["update-controller.cjs", "createUpdateController"],
  ]) {
    assert.match(sources["main.cjs"], new RegExp(`require\\("\\./${file.replace(".", "\\.")}"\\)`));
    assert.match(sources[file], new RegExp(`function ${factory}\\(`));
    assert.match(sources[file], new RegExp(`module\\.exports = \\{ ${factory} \\}`));
  }
});

test("IPC channels are unique and preserve the preload contract", () => {
  const handlers = [];
  for (const [file, source] of Object.entries(sources)) {
    for (const match of source.matchAll(/ipcMain\.handle\("([^"]+)"/g)) handlers.push({ channel: match[1], file });
  }
  const duplicates = handlers.filter((item, index) => handlers.findIndex((candidate) => candidate.channel === item.channel) !== index);
  assert.deepEqual(duplicates, []);
  const channels = new Set(handlers.map((item) => item.channel));
  for (const channel of [
    "launcher:snapshot", "launcher:service", "launcher:all", "launcher:open", "launcher:external",
    "launcher:settings-save", "launcher:settings-get", "launcher:update", "launcher:component", "launcher:voice",
    "launcher:onboarding", "runtime:action", "runtime:snapshot", "runtime:install", "runtime:cancel",
    "runtime:retry", "runtime:repair", "runtime:diagnostics", "runtime:source", "runtime:proxy",
    "companion:snapshot", "companion:action",
  ]) assert.equal(channels.has(channel), true, `missing IPC channel ${channel}`);
  assert.match(sources["preload.cjs"], /launcher:settings-save/);
  assert.match(sources["preload.cjs"], /launcher:settings-get/);
});

test("service registry and external navigation remain single authorities", () => {
  const serviceMaps = Object.entries(sources).filter(([, source]) =>
    /const services = \{\s*api:\s*\{[\s\S]{0,1000}?\bscript\s*:/.test(source),
  );
  assert.deepEqual(serviceMaps.map(([file]) => file), ["main.cjs"]);
  const externalCalls = Object.entries(sources).filter(([, source]) => /shell\.openExternal\(/.test(source));
  assert.deepEqual(externalCalls.map(([file]) => file), ["product-windows.cjs"]);
  assert.match(sources["product-windows.cjs"], /require\("\.\/external-navigation\.cjs"\)/);
  assert.doesNotMatch(sources["service-supervisor.cjs"], /8765|8766|5055|8091/);
  assert.match(sources["service-supervisor.cjs"], /await runCommand\("wsl\.exe"/);
});

test("local production requires are acyclic and every new controller is called", () => {
  const graph = new Map(productionFiles.map((name) => [name, []]));
  for (const [file, source] of Object.entries(sources)) {
    for (const match of source.matchAll(/require\("\.\/([^"/]+\.cjs)"\)/g)) {
      if (graph.has(match[1])) graph.get(file).push(match[1]);
    }
  }
  const visiting = new Set();
  const visited = new Set();
  function visit(file) {
    if (visiting.has(file)) throw new Error(`circular require at ${file}`);
    if (visited.has(file)) return;
    visiting.add(file);
    for (const dependency of graph.get(file)) visit(dependency);
    visiting.delete(file);
    visited.add(file);
  }
  for (const file of productionFiles) visit(file);
  for (const call of ["initializeServiceSupervisor()", "initializeSettingsController()", "initializeProductWindows()", "initializeUpdateManager()"]) {
    assert.ok(sources["main.cjs"].split(call).length >= 3, `${call} is declared but not invoked`);
  }
});

test("GPU service shutdown is graceful and never terminates an entire WSL distro", () => {
  const supervisor = sources["service-supervisor.cjs"];
  const stopScript = fs.readFileSync(path.resolve(__dirname, "..", "scripts", "stop-services.ps1"), "utf8");
  const main = sources["main.cjs"];
  assert.doesNotMatch(supervisor, /\["--terminate",\s*distro\]/);
  assert.doesNotMatch(stopScript, /wsl\.exe\s+--terminate/);
  assert.match(stopScript, /X-Mindspace-Service-Token/);
  assert.match(stopScript, /\/shutdown/);
  assert.doesNotMatch(main, /new Set\(\[\.\.\.children\.keys\(\),\s*"tts",\s*"qwenTts"\]\)/);
});

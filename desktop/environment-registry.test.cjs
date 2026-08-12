const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { ASR_MODULES, createEnvironmentRegistry } = require("./environment-registry.cjs");

test("a complete bounded ASR candidate is claimed by rebuilding its missing marker", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-environment-registry-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const paths = {
    home: root,
    state: path.join(root, "state"),
    models: path.join(root, "models"),
    environment: path.join(root, "environment"),
  };
  for (const directory of Object.values(paths)) fs.mkdirSync(directory, { recursive: true });
  const existing = path.join(root, ".venv-asr");
  fs.mkdirSync(path.join(existing, "Scripts"), { recursive: true });
  fs.mkdirSync(path.join(existing, "Lib", "site-packages"), { recursive: true });
  fs.writeFileSync(path.join(existing, "Scripts", "python.exe"), "fixture");
  for (const moduleName of ASR_MODULES) fs.mkdirSync(path.join(existing, "Lib", "site-packages", moduleName));

  const registry = createEnvironmentRegistry({ paths, environment: {} });
  const report = registry.inspectTarget({ id: "asr-runtime", name: "ASR", required: [] }, path.join(paths.environment, "venvs", "asr-cuda"));
  assert.equal(report.ready, true);
  assert.equal(report.selectedSource, "legacy-home");
  const marker = JSON.parse(fs.readFileSync(path.join(existing, ".mindspace-asr-ready.json"), "utf8"));
  assert.equal(marker.ready, true);
  assert.equal(marker.adopted, true);
  const record = JSON.parse(fs.readFileSync(path.join(paths.state, "environment-registry.json"), "utf8"));
  assert.equal(record.environments["asr-runtime"].path, existing);
});

test("single-file Python modules satisfy ASR environment discovery", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-environment-registry-module-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const paths = { home: root, state: path.join(root, "state"), models: path.join(root, "models"), environment: path.join(root, "environment") };
  for (const directory of Object.values(paths)) fs.mkdirSync(directory, { recursive: true });
  const existing = path.join(paths.environment, "venvs", "asr-cuda");
  const sitePackages = path.join(existing, "Lib", "site-packages");
  fs.mkdirSync(path.join(existing, "Scripts"), { recursive: true });
  fs.mkdirSync(sitePackages, { recursive: true });
  fs.writeFileSync(path.join(existing, "Scripts", "python.exe"), "fixture");
  for (const moduleName of ASR_MODULES.filter((name) => name !== "sounddevice")) fs.mkdirSync(path.join(sitePackages, moduleName));
  fs.writeFileSync(path.join(sitePackages, "sounddevice.py"), "# module");
  const report = createEnvironmentRegistry({ paths, environment: {} }).inspectTarget({ id: "asr-runtime" }, existing);
  assert.equal(report.ready, true);
});

function createPaths(root) {
  const paths = { home: path.join(root, "Mindspace"), state: path.join(root, "Mindspace", "environment", "state"), models: path.join(root, "Mindspace", "models"), environment: path.join(root, "Mindspace", "environment") };
  for (const directory of Object.values(paths)) fs.mkdirSync(directory, { recursive: true });
  return paths;
}

test("TTS model discovery reuses only an explicit migrated Home", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-tts-discovery-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const paths = createPaths(root);
  const legacy = path.join(root, "PreviousMindspace");
  const model = path.join(legacy, "models", "tts", "Fun-CosyVoice3-0.5B-2512");
  fs.mkdirSync(model, { recursive: true });
  for (const file of ["cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt"]) fs.writeFileSync(path.join(model, file), "fixture");
  fs.writeFileSync(path.join(paths.state, "storage-migration.json"), JSON.stringify({ source: legacy }));
  const component = { id: "tts", name: "CosyVoice", version: "3-0.5B-2512", required: ["cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt"] };
  const target = path.join(paths.models, "tts", "Fun-CosyVoice3-0.5B-2512");
  const report = createEnvironmentRegistry({ paths, environment: {} }).inspectTarget(component, target);
  assert.equal(report.ready, true);
  assert.equal(report.resourceState, "ready");
  assert.equal(report.selectedSource, "legacy-home");
  assert.equal(report.path, model);
});

test("Qwen3 source reports attachable only for the supported complete CustomVoice model", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-qwen-attach-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const paths = createPaths(root);
  const legacy = path.join(root, "PreviousMindspace");
  const source = path.join(legacy, "experimental", "qwen3-tts");
  const model = path.join(source, "models", "Qwen3-TTS-12Hz-1.7B-CustomVoice");
  fs.mkdirSync(model, { recursive: true });
  fs.mkdirSync(path.join(legacy, "experimental", "vllm-omni"), { recursive: true });
  fs.writeFileSync(path.join(model, "config.json"), "{}");
  fs.writeFileSync(path.join(model, "model.safetensors"), "");
  fs.truncateSync(path.join(model, "model.safetensors"), 3 * 1024 ** 3);
  fs.writeFileSync(path.join(legacy, "experimental", "vllm-omni", "start-qwen3-tts.sh"), "#!/bin/bash");
  fs.writeFileSync(path.join(paths.state, "storage-migration.json"), JSON.stringify({ source: legacy }));
  const component = { id: "qwen3-vllm-runtime", name: "Qwen3", required: ["ready.json"] };
  const target = path.join(paths.environment, "qwen3-vllm");
  const registry = createEnvironmentRegistry({ paths, environment: {} });
  const report = registry.inspectTarget(component, target);
  assert.equal(report.ready, false);
  assert.equal(report.resourceState, "attachable");
  assert.match(report.missing[0], /无需下载模型/);
  assert.equal(registry.discoverLocalResources([component], () => target)[0].state, "attachable");
});

test("Qwen3 unsupported local model is reported as incompatible rather than missing", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-qwen-incompatible-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const paths = createPaths(root);
  const legacy = path.join(root, "PreviousMindspace");
  fs.mkdirSync(path.join(legacy, "experimental", "qwen3-tts", "models", "Qwen3-TTS-12Hz-0.6B-CustomVoice"), { recursive: true });
  fs.writeFileSync(path.join(paths.state, "storage-migration.json"), JSON.stringify({ source: legacy }));
  const component = { id: "qwen3-vllm-runtime", name: "Qwen3", required: ["ready.json"] };
  const report = createEnvironmentRegistry({ paths, environment: {} }).inspectTarget(component, path.join(paths.environment, "qwen3-vllm"));
  assert.equal(report.ready, false);
  assert.equal(report.resourceState, "incompatible");
  assert.match(report.version, /0\.6B/);
});

test("CosyVoice can be registered or migrated through a verified staging copy", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-tts-attach-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const paths = createPaths(root);
  const source = path.join(root, "External", "Fun-CosyVoice3-0.5B-2512");
  fs.mkdirSync(source, { recursive: true });
  for (const file of ["cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt"]) fs.writeFileSync(path.join(source, file), "fixture");
  const component = { id: "tts", name: "CosyVoice", version: "3", required: ["cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt"] };
  const target = path.join(paths.models, "tts", "Fun-CosyVoice3-0.5B-2512");
  const registry = createEnvironmentRegistry({ paths, environment: {} });
  const registered = registry.attachSelectedResource(component, source, target, "register");
  assert.equal(registered.action, "registered");
  assert.equal(registered.report.root, source);
  assert.equal(fs.existsSync(target), false);
  const migrated = registry.attachSelectedResource(component, source, target, "migrate");
  assert.equal(migrated.action, "migrated");
  assert.equal(fs.existsSync(path.join(source, "cosyvoice3.yaml")), true);
  assert.equal(fs.existsSync(path.join(target, "cosyvoice3.yaml")), true);
  assert.equal(JSON.parse(fs.readFileSync(path.join(paths.state, "environment-registry.json"), "utf8")).environments.tts.path, target);
});

test("partial Qwen3 and GPT-SoVITS directories cannot be attached", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-resource-reject-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const paths = createPaths(root);
  const registry = createEnvironmentRegistry({ paths, environment: {} });
  const qwen = { id: "qwen3-vllm-runtime", name: "Qwen3", required: ["ready.json"] };
  const gpt = { id: "gpt-sovits-runtime", name: "GPT-SoVITS", required: ["Scripts/python.exe", "ready.json"] };
  const partial = path.join(root, "partial");
  fs.mkdirSync(partial, { recursive: true });
  assert.throws(() => registry.attachSelectedResource(qwen, partial, path.join(paths.environment, "qwen3-vllm")), /无法接入/);
  assert.throws(() => registry.attachSelectedResource(gpt, partial, path.join(paths.environment, "venvs", "gpt-sovits")), /无法接入/);
});

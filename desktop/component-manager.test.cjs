const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  DEFAULT_COMPONENTS,
  classifyError,
  createComponentManager,
  reportReady,
  resolveFiles,
  safeFile,
} = require("./component-manager.cjs");
const { GPT_SOVITS_COMPONENTS, GPT_SOVITS_VOICES } = require("./gpt-sovits-catalog.cjs");

test("model downloads classify network, checksum and disk failures", () => {
  assert.equal(classifyError(new Error("下载 x 失败：HTTP 404"), "downloading").code, "HTTP_404");
  assert.equal(classifyError(new Error("x SHA-256 校验失败"), "verifying").code, "CHECKSUM_MISMATCH");
  assert.equal(classifyError(new Error("磁盘空间不足"), "resolving").code, "DISK_FULL");
});

test("ModelScope embedding mirror excludes unrelated ONNX files", async () => {
  const component = DEFAULT_COMPONENTS.find((item) => item.id === "embedding");
  const fetchImpl = async () => ({
    ok: true,
    json: async () => ({
      Data: {
        Files: [
          { Type: "blob", Path: "config.json", Size: 10, Sha256: "a".repeat(64) },
          { Type: "blob", Path: "pytorch_model.bin", Size: 20, Sha256: "b".repeat(64) },
          { Type: "blob", Path: "onnx/model.onnx", Size: 30, Sha256: "c".repeat(64) },
        ],
      },
    }),
  });
  const files = await resolveFiles(component, fetchImpl, new AbortController().signal);
  assert.deepEqual(files.map((file) => file.path), ["config.json", "pytorch_model.bin"]);
  assert.match(files[1].url, /modelscope\.cn/);
});

test("download source selection is explicit for static files and repository providers", async () => {
  const staticComponent = {
    provider: "static",
    files: [{
      path: "model.bin", size: 4, sha256: "a".repeat(64), url: "https://china.invalid/model.bin",
      urls: { china: "https://china.invalid/model.bin", official: "https://official.invalid/model.bin" },
    }],
  };
  const chinaFiles = await resolveFiles(staticComponent, async () => {}, new AbortController().signal, "china");
  const officialFiles = await resolveFiles(staticComponent, async () => {}, new AbortController().signal, "official");
  assert.equal(chinaFiles[0].url, "https://china.invalid/model.bin");
  assert.equal(officialFiles[0].url, "https://official.invalid/model.bin");

  const embedding = DEFAULT_COMPONENTS.find((item) => item.id === "embedding");
  let requested = "";
  await resolveFiles(embedding, async (url) => {
    requested = url;
    return { ok: true, json: async () => [] };
  }, new AbortController().signal, "official");
  assert.match(requested, /huggingface\.co\/api\/models\/shibing624\/text2vec-base-chinese/);
});

test("CosyVoice model and runtime use the domestic shared-runtime chain", () => {
  const model = DEFAULT_COMPONENTS.find((item) => item.id === "tts");
  const runtime = DEFAULT_COMPONENTS.find((item) => item.id === "tts-runtime");
  assert.equal(model.provider, "modelscope");
  assert.equal(model.repo, "FunAudioLLM/Fun-CosyVoice3-0.5B-2512");
  assert.equal(model.optional, true);
  assert.equal(runtime.provider, "installer");
  assert.equal(runtime.installScript, "scripts/prepare-tts.ps1");
  assert.deepEqual(runtime.required, ["ready.json"]);
  assert.equal(runtime.displayEstimatedBytes, false);
  assert.match(runtime.description, /增量安装缺失依赖/);

  const installer = fs.readFileSync(path.resolve(__dirname, "..", runtime.installScript), "utf8");
  assert.match(installer, /setuptools<81/);
  assert.match(installer, /--no-build-isolation-package openai-whisper/);
  assert.match(installer, /TTS_STAGE=reuse/);
});

test("emotion models are absent while the capability is dormant", () => {
  const component = DEFAULT_COMPONENTS.find((item) => item.id === "sensevoice");
  assert.equal(component, undefined);
});

test("Fun-ASR Nano is an optional checksum-verified final-pass component", () => {
  const component = DEFAULT_COMPONENTS.find((item) => item.id === "asr-final");
  assert.equal(component.repo, "FunAudioLLM/Fun-ASR-Nano-2512");
  assert.equal(component.target, "assets/models/asr/Fun-ASR-Nano-2512");
  assert.equal(component.optional, true);
  assert.equal(component.hardware, "nvidia");
  assert.ok(component.required.includes("Qwen3-0.6B/tokenizer.json"));
  assert.ok(component.minimumWeightBytes >= 2_000_000_000);
});

test("GPT-SoVITS voices are isolated, versioned and dependency ordered", async (context) => {
  assert.equal(GPT_SOVITS_VOICES.length, 48);
  assert.equal(GPT_SOVITS_VOICES.filter((voice) => voice.family === "v4").length, 38);
  assert.equal(GPT_SOVITS_VOICES.filter((voice) => voice.family === "v2ProPlus").length, 10);
  assert.equal(new Set(GPT_SOVITS_VOICES.map((voice) => voice.id)).size, GPT_SOVITS_VOICES.length);
  assert.equal(GPT_SOVITS_VOICES[0].id, "v4-elysia-2026");
  assert.equal(GPT_SOVITS_VOICES[0].releaseYear, 2026);
  assert.ok(GPT_SOVITS_VOICES.filter((voice) => voice.franchise === "崩铁").every((voice) => voice.family === "v2ProPlus"));
  assert.ok(GPT_SOVITS_VOICES.filter((voice) => voice.franchise !== "崩铁").every((voice) => voice.family === "v4"));
  const changli = GPT_SOVITS_COMPONENTS.find((item) => item.id === "gpt-sovits-v4-changli");
  const elysia = GPT_SOVITS_COMPONENTS.find((item) => item.id === "gpt-sovits-v4-elysia-2026");
  const runtime = GPT_SOVITS_COMPONENTS.find((item) => item.id === "gpt-sovits-runtime");
  const base = GPT_SOVITS_COMPONENTS.find((item) => item.id === "gpt-sovits-v4-base");
  const preprocessor = base.files.find((file) => file.path.endsWith("preprocessor_config.json"));
  const g2pw = base.files.find((file) => file.path.endsWith("G2PWModel.zip"));
  assert.equal(preprocessor.sha256, "dcd684124d06722947939d41ea6ae58dbf10968c60a11a29f23ddc602c64a29b");
  assert.match(preprocessor.urls.china, /modelscope\.cn/);
  assert.match(preprocessor.urls.official, /huggingface\.co\/lj1995\/GPT-SoVITS/);
  assert.match(g2pw.urls.china, /GPT-SoVITS-Pretrained\/resolve\/master\/G2PWModel\.zip$/);
  assert.match(g2pw.urls.official, /huggingface\.co\/XXXXRT\/GPT-SoVITS-Pretrained\/resolve\/main\/G2PWModel\.zip$/);
  assert.match(changli.files[0].url, /modelscope\.cn\/models\/aihobbyist\/GPT-SoVITS_Model_Collection\/resolve\/[0-9a-f]+/);
  assert.deepEqual(changli.dependencies, ["gpt-sovits-runtime"]);
  assert.equal(changli.category, "voice");
  assert.equal(elysia.files.length, 2);
  assert.match(elysia.files[1].url, /huggingface\.co\/AyerElysia\/elysia-gpt-sovits-lora-v4/);
  assert.equal(elysia.files[1].sha256, "e1c20121c09961fdfdaa90db050eb91ac061bdac13f44c8fab5ee16fcdc78472");
  assert.ok(elysia.required.includes("GPT_SoVITS/pretrained_models/s1v3.ckpt"));
  assert.equal(elysia.archives[0].encoding, "gbk");
  assert.equal(elysia.archives[1].type, "tar.gz");
  assert.deepEqual(runtime.dependencies, ["asr-runtime", "gpt-sovits-v4-base", "gpt-sovits-ffmpeg"]);
  assert.equal(runtime.target, ".venv-gpt-sovits");

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-component-dependencies-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const order = [];
  const catalog = [
    { id: "base", name: "Base", description: "fixture", provider: "static", target: "base", required: ["base.bin"], estimatedBytes: 4, files: [{ path: "base.bin", size: 4, sha256: "", url: "https://fixture/base" }] },
    { id: "voice", name: "Voice", description: "fixture", provider: "static", target: "voice", required: ["voice.bin"], estimatedBytes: 5, dependencies: ["base"], files: [{ path: "voice.bin", size: 5, sha256: "", url: "https://fixture/voice" }] },
  ];
  const manager = createComponentManager({
    rootPath: () => root,
    catalog,
    fetch: async (url) => {
      order.push(url.endsWith("/base") ? "base" : "voice");
      return new Response(Buffer.from(url.endsWith("/base") ? "base" : "voice"), { status: 200 });
    },
  });
  const result = await manager.download("voice");
  assert.deepEqual(order, ["base", "voice"]);
  assert.equal(result.items.every((item) => item.ready), true);
  assert.equal(result.items[1].files, undefined);
});

test("ASR runtime requires a completed dependency verification marker", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-asr-readiness-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const component = DEFAULT_COMPONENTS.find((item) => item.id === "asr-runtime");
  const target = path.join(root, ".venv-asr");
  fs.mkdirSync(path.join(target, "Scripts"), { recursive: true });
  fs.writeFileSync(path.join(target, "Scripts", "python.exe"), "fixture");

  assert.equal(reportReady(root, component).ready, false);
  assert.equal(reportReady(root, component).partial, true);
  assert.deepEqual(reportReady(root, component).missing, [".mindspace-asr-ready.json"]);

  fs.writeFileSync(path.join(target, ".mindspace-asr-ready.json"), '{"ready":true}');
  assert.equal(reportReady(root, component).ready, true);

  const installer = fs.readFileSync(path.resolve(__dirname, "..", component.installScript), "utf8");
  assert.match(installer, /torch\.cuda\.is_available/);
  assert.match(installer, /\.mindspace-asr-ready\.json/);
});

test("component downloader resumes, verifies and atomically installs a file", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-component-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const payload = crypto.randomBytes(1024 * 1024);
  let rangeSeen = false;
  const server = http.createServer((request, response) => {
    const match = /^bytes=(\d+)-$/.exec(request.headers.range || "");
    const offset = match ? Number(match[1]) : 0;
    rangeSeen ||= offset > 0;
    response.writeHead(offset ? 206 : 200, { "Content-Length": payload.length - offset, "Content-Type": "application/octet-stream" });
    response.end(payload.subarray(offset));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => server.close());
  const port = server.address().port;
  const targetRoot = path.join(root, "assets", "test");
  fs.mkdirSync(targetRoot, { recursive: true });
  fs.writeFileSync(path.join(targetRoot, "model.bin.partial"), payload.subarray(0, 128 * 1024));
  const catalog = [{
    id: "test", name: "Test", description: "fixture", provider: "static", target: "assets/test",
    required: ["model.bin"], estimatedBytes: payload.length,
    files: [{ path: "model.bin", size: payload.length, sha256: crypto.createHash("sha256").update(payload).digest("hex"), url: `http://127.0.0.1:${port}/model.bin` }],
  }];
  const manager = createComponentManager({ rootPath: () => root, catalog });
  const result = await manager.download("test");
  assert.equal(rangeSeen, true);
  assert.equal(result.items[0].ready, true);
  assert.equal(result.items[0].progress, 100);
  assert.deepEqual(fs.readFileSync(path.join(targetRoot, "model.bin")), payload);
  assert.equal(fs.existsSync(path.join(targetRoot, "model.bin.partial")), false);
});

test("installer components become ready through the runtime installer callback", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-runtime-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const catalog = [{
    id: "runtime",
    name: "Runtime",
    description: "fixture",
    provider: "installer",
    target: ".venv-runtime",
    required: ["Scripts/python.exe"],
    estimatedBytes: 1024,
  }];
  let progressSeen = false;
  const manager = createComponentManager({
    rootPath: () => root,
    catalog,
    installComponent: async (component, _signal, onProgress) => {
      onProgress(60, "installing");
      progressSeen = true;
      const target = path.join(root, component.target, "Scripts");
      fs.mkdirSync(target, { recursive: true });
      fs.writeFileSync(path.join(target, "python.exe"), "fixture");
    },
  });
  const result = await manager.download("runtime");
  assert.equal(progressSeen, true);
  assert.equal(result.items[0].ready, true);
  assert.equal(result.items[0].status, "ready");
});

test("component paths cannot escape their target directory", () => {
  const root = path.resolve(os.tmpdir(), "mindspace-safe-root");
  assert.throws(() => safeFile(root, "../outside.bin"), /不安全路径/);
  assert.equal(safeFile(root, "nested/model.bin"), path.join(root, "nested", "model.bin"));
});

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { canonical } = require("./update-manager.cjs");
const { classifyError, createRuntimeManager, safeTarget, verifyRuntimeManifest } = require("./runtime-manager.cjs");

test("runtime failures expose stable diagnostic codes", () => {
  assert.equal(classifyError(new Error("下载失败：HTTP 404"), "downloading").code, "HTTP_404");
  assert.equal(classifyError(new Error("SHA-256 校验失败"), "verifying").code, "CHECKSUM_MISMATCH");
  assert.equal(classifyError(Object.assign(new Error("write failed"), { code: "ENOSPC" }), "installing").code, "DISK_FULL");
});

function signedManifest(component) {
  const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
  const unsigned = {
    schema_version: "1.0.0", runtime_version: "test", platform: "win32", arch: "x64",
    components: [component],
  };
  const value = crypto.sign(null, Buffer.from(canonical(unsigned)), privateKey).toString("base64");
  return { manifest: { ...unsigned, signature: { algorithm: "ed25519", value } }, publicKey };
}

test("runtime manifests require a valid Ed25519 signature", () => {
  const component = { id: "tool", name: "Tool", description: "fixture", version: "1", kind: "archive", required: true, dependencies: [], size: 1, sha256: "a".repeat(64), executable: "tool.exe", urls: ["https://example.com/tool.zip"] };
  const { manifest, publicKey } = signedManifest(component);
  assert.equal(verifyRuntimeManifest(manifest, publicKey).runtime_version, "test");
  assert.throws(() => verifyRuntimeManifest({ ...manifest, runtime_version: "tampered" }, publicKey), /签名/);
});

test("archive runtimes resume, verify, extract and use only private executables", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-runtime-manager-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const payload = crypto.randomBytes(512 * 1024);
  let rangeSeen = false;
  const server = http.createServer((request, response) => {
    const match = /^bytes=(\d+)-$/.exec(request.headers.range || "");
    const offset = match ? Number(match[1]) : 0;
    rangeSeen ||= offset > 0;
    response.writeHead(offset ? 206 : 200, { "Content-Length": payload.length - offset });
    response.end(payload.subarray(offset));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => server.close());
  const port = server.address().port;
  const component = {
    id: "tool", name: "Tool", description: "fixture", version: "1.0.0", kind: "archive", required: true,
    dependencies: [], size: payload.length, sha256: crypto.createHash("sha256").update(payload).digest("hex"),
    executable: "tool.exe", probe: ["--version"], urls: [`http://127.0.0.1:${port}/tool.zip`],
  };
  const { manifest, publicKey } = signedManifest(component);
  const manifestPath = path.join(root, "manifest.json");
  const publicKeyPath = path.join(root, "public.pem");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest));
  fs.writeFileSync(publicKeyPath, publicKey.export({ type: "spki", format: "pem" }));
  const paths = {};
  for (const name of ["home", "environment", "tools", "python", "venvs", "cache", "state", "models", "data", "downloads", "logs"]) {
    paths[name] = name === "home" ? root : path.join(root, name);
    fs.mkdirSync(paths[name], { recursive: true });
  }
  const partialRoot = path.join(paths.downloads, "runtime");
  fs.mkdirSync(partialRoot, { recursive: true });
  fs.writeFileSync(path.join(partialRoot, "tool-1.0.0.zip.partial"), payload.subarray(0, 64 * 1024));
  const probes = [];
  const manager = createRuntimeManager({
    paths, corePath: () => root, manifestPath, publicKeyPath, fetch: global.fetch,
    osRelease: () => "10.0.22621",
    extract: async (_archive, { dir }) => fs.writeFileSync(path.join(dir, "tool.exe"), "fixture"),
    spawnSync: (executable, args) => {
      if (executable === "nvidia-smi.exe") return { status: 1, stdout: "", stderr: "" };
      probes.push({ executable, args });
      return { status: 0, stdout: "tool 1.0.0", stderr: "" };
    },
  });
  const result = await manager.install("tool");
  assert.equal(rangeSeen, true);
  assert.equal(result.ready, true);
  assert.match(result.items[0].executable, /environment|tools/i);
  assert.equal(probes.some((probe) => probe.executable === "tool.exe"), false);
});

test("runtime targets cannot escape the private environment", () => {
  assert.throws(() => safeTarget("C:\\Mindspace\\environment", "..", "outside"), /越界/);
});

test("packaged runtimes deploy bundled tools without touching the network", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-bundled-runtime-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const bundledRoot = path.join(root, "bundled");
  fs.mkdirSync(path.join(bundledRoot, "tool", "1.0.0"), { recursive: true });
  fs.writeFileSync(path.join(bundledRoot, "tool", "1.0.0", "tool.exe"), "fixture");
  const component = {
    id: "tool", name: "Tool", description: "fixture", version: "1.0.0", kind: "archive",
    bundled: "tool/1.0.0", required: true, dependencies: [], size: 1,
    sha256: "a".repeat(64), executable: "tool.exe", urls: ["https://example.invalid/tool.zip"],
  };
  const { manifest, publicKey } = signedManifest(component);
  const manifestPath = path.join(root, "manifest.json");
  const publicKeyPath = path.join(root, "public.pem");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest));
  fs.writeFileSync(publicKeyPath, publicKey.export({ type: "spki", format: "pem" }));
  const paths = {};
  for (const name of ["home", "environment", "tools", "python", "venvs", "cache", "state", "models", "data", "downloads", "logs"]) {
    paths[name] = name === "home" ? root : path.join(root, name);
    fs.mkdirSync(paths[name], { recursive: true });
  }
  const stalePartial = path.join(paths.downloads, "runtime", "tool-1.0.0.zip.partial");
  fs.mkdirSync(path.dirname(stalePartial), { recursive: true });
  fs.writeFileSync(stalePartial, "stale");
  let fetched = false;
  const manager = createRuntimeManager({
    paths, corePath: () => root, manifestPath, publicKeyPath, bundledRoot,
    fetch: async () => { fetched = true; throw new Error("network must not be used"); },
    extract: async () => {}, osRelease: () => "10.0.22621",
    spawnSync: (executable) => executable === "nvidia-smi.exe"
      ? { status: 1, stdout: "", stderr: "" }
      : { status: 0, stdout: "tool 1.0.0", stderr: "" },
  });
  const before = manager.snapshot().items[0];
  assert.equal(before.bundled, true);
  assert.equal(before.downloadRequired, false);
  assert.match(before.message, /无需联网/);
  const result = await manager.install("tool");
  assert.equal(result.ready, true);
  assert.equal(fetched, false);
  assert.equal(fs.existsSync(stalePartial), false);
  assert.equal(result.items[0].executable.startsWith(paths.tools), true);
});

test("a valid bundled runtime is adopted without overwriting files when its marker is missing", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-runtime-adopt-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const bundledRoot = path.join(root, "bundled");
  fs.mkdirSync(path.join(bundledRoot, "tool", "1.0.0"), { recursive: true });
  fs.writeFileSync(path.join(bundledRoot, "tool", "1.0.0", "tool.exe"), "new-bundle");
  const component = {
    id: "tool", name: "Tool", description: "fixture", version: "1.0.0", kind: "archive",
    bundled: "tool/1.0.0", required: true, dependencies: [], size: 1,
    sha256: "a".repeat(64), executable: "tool.exe", probe: ["--version"], urls: ["https://example.invalid/tool.zip"],
  };
  const { manifest, publicKey } = signedManifest(component);
  const manifestPath = path.join(root, "manifest.json");
  const publicKeyPath = path.join(root, "public.pem");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest));
  fs.writeFileSync(publicKeyPath, publicKey.export({ type: "spki", format: "pem" }));
  const paths = {};
  for (const name of ["home", "environment", "tools", "python", "venvs", "cache", "state", "models", "data", "downloads", "logs"]) {
    paths[name] = name === "home" ? root : path.join(root, name);
    fs.mkdirSync(paths[name], { recursive: true });
  }
  const installed = path.join(paths.tools, "tool", "1.0.0", "tool.exe");
  fs.mkdirSync(path.dirname(installed), { recursive: true });
  fs.writeFileSync(installed, "existing-in-use-runtime");
  let fetched = false;
  const manager = createRuntimeManager({
    paths, corePath: () => root, manifestPath, publicKeyPath, bundledRoot,
    fetch: async () => { fetched = true; throw new Error("network must not be used"); },
    extract: async () => {}, osRelease: () => "10.0.22621",
    spawnSync: (executable) => executable === "nvidia-smi.exe"
      ? { status: 1, stdout: "", stderr: "" }
      : { status: 0, stdout: "tool 1.0.0", stderr: "" },
  });
  const result = await manager.install("tool");
  assert.equal(result.ready, true);
  assert.equal(fetched, false);
  assert.equal(fs.readFileSync(installed, "utf8"), "existing-in-use-runtime");
  const marker = JSON.parse(fs.readFileSync(path.join(paths.state, "components", "tool.json"), "utf8"));
  assert.equal(marker.adopted, true);
});

test("runtime Python processes use the explicitly selected package index without silent fallback", () => {
  const source = fs.readFileSync(path.join(__dirname, "runtime-manager.cjs"), "utf8");
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  assert.match(source, /OFFICIAL_PYPI_INDEX = "https:\/\/pypi\.org\/simple\/"/);
  assert.match(source, /downloadSource === "official" \? OFFICIAL_PYPI_INDEX : DOMESTIC_PYPI_INDEX/);
  assert.match(source, /UV_DEFAULT_INDEX: packageIndex/);
  assert.match(source, /PIP_INDEX_URL: packageIndex/);
  assert.doesNotMatch(source, /正在回退官方源|正在回退默认 PyPI/);
  const initializer = main.slice(main.indexOf("function initializeRuntimeManager"), main.indexOf("function unifiedRuntimeSnapshot"));
  assert.match(initializer, /bundledRoot: app\.isPackaged/);
});

test("launcher creates its window only after update and runtime managers are ready", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  const startup = main.slice(main.indexOf("app.whenReady()"), main.indexOf("app.on(\"before-quit\""));
  const windowIndex = startup.indexOf("createWindow()");

  assert.ok(windowIndex > startup.indexOf("initializeUpdateManager()"));
  assert.ok(windowIndex > startup.indexOf("initializeRuntimeManager()"));
  assert.ok(windowIndex > startup.indexOf("initializeComponentManager()"));
});

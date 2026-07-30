const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { canonical } = require("./update-manager.cjs");
const {
  classifyError,
  createRuntimeManager,
  pythonRuntimeLooksComplete,
  renameWithRetry,
  safeTarget,
  sanitizeHostEnvironment,
  verifyRuntimeManifest,
} = require("./runtime-manager.cjs");

test("runtime promotion retries transient Windows file locks", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-runtime-promote-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "staging");
  const target = path.join(root, "ready");
  fs.mkdirSync(source);
  fs.writeFileSync(path.join(source, "tool.exe"), "fixture");
  const originalRename = fs.renameSync;
  let attempts = 0;
  fs.renameSync = (...arguments_) => {
    attempts += 1;
    if (attempts < 3) {
      const error = new Error("Defender retained the executable");
      error.code = "EPERM";
      throw error;
    }
    return originalRename(...arguments_);
  };
  context.after(() => { fs.renameSync = originalRename; });
  renameWithRetry(source, target);
  assert.equal(attempts, 3);
  assert.equal(fs.readFileSync(path.join(target, "tool.exe"), "utf8"), "fixture");
});

test("runtime failures expose stable diagnostic codes", () => {
  assert.equal(classifyError(new Error("下载失败：HTTP 404"), "downloading").code, "HTTP_404");
  assert.equal(classifyError(new Error("net::ERR_NAME_NOT_RESOLVED"), "downloading").code, "NETWORK_DNS");
  assert.equal(classifyError(new Error("SHA-256 校验失败"), "verifying").code, "CHECKSUM_MISMATCH");
  assert.equal(classifyError(Object.assign(new Error("write failed"), { code: "ENOSPC" }), "installing").code, "DISK_FULL");
  const pythonFailure = classifyError(
    new Error("Fatal Python error: init_fs_encoding: failed to get the Python codec of the filesystem encoding ModuleNotFoundError: No module named 'encodings'"),
    "installing",
  );
  assert.equal(pythonFailure.code, "PYTHON_RUNTIME_INVALID");
  assert.doesNotMatch(pythonFailure.message, /sys\.path|ModuleNotFoundError/);
  const installPathFailure = classifyError(
    new Error("failed to create file `C:\\Mindspace\\python\\Lib\\EXTERNALLY-MANAGED`: 系统找不到指定的路径。 (os error 3)"),
    "installing",
  );
  assert.equal(installPathFailure.code, "PYTHON_INSTALL_PATH");
  assert.doesNotMatch(installPathFailure.message, /EXTERNALLY-MANAGED|os error 3/);
});

test("private Python processes do not inherit host interpreter state", () => {
  const clean = sanitizeHostEnvironment({
    Path: "C:\\Windows\\System32",
    PYTHONHOME: "C:\\broken\\core",
    PythonPath: "C:\\broken\\src",
    PYTHONUSERBASE: "C:\\broken\\user",
    VIRTUAL_ENV: "C:\\broken\\.venv",
    CONDA_PREFIX: "C:\\broken\\conda",
    UV_PROJECT_ENVIRONMENT: "C:\\broken\\uv",
    KEEP_ME: "yes",
  });
  assert.equal(clean.Path, "C:\\Windows\\System32");
  assert.equal(clean.KEEP_ME, "yes");
  assert.equal(Object.keys(clean).some((key) => key.toUpperCase().startsWith("PYTHON")), false);
  assert.equal("VIRTUAL_ENV" in clean, false);
  assert.equal("CONDA_PREFIX" in clean, false);
  assert.equal("UV_PROJECT_ENVIRONMENT" in clean, false);
});

test("Python completeness requires the standard-library encoding landmarks", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-python-landmarks-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const executable = path.join(root, "python.exe");
  fs.writeFileSync(executable, "fixture");
  assert.equal(pythonRuntimeLooksComplete(executable), false);
  fs.mkdirSync(path.join(root, "Lib", "encodings"), { recursive: true });
  fs.writeFileSync(path.join(root, "Lib", "encodings", "__init__.py"), "");
  fs.writeFileSync(path.join(root, "Lib", "os.py"), "");
  assert.equal(pythonRuntimeLooksComplete(executable), true);
});

test("production runtime manifest uses the live control endpoint and remains signed", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "assets", "runtime-manifest.json"), "utf8"));
  const publicKey = fs.readFileSync(path.join(__dirname, "assets", "update-public-key.pem"), "utf8");
  const verified = verifyRuntimeManifest(manifest, publicKey);
  const serialized = JSON.stringify(verified);
  assert.match(serialized, /https:\/\/douyinqijun\.cn\/downloads\/mindspace\//);
  assert.doesNotMatch(serialized, /downloads\.douyinqijun\.cn/);
  for (const component of verified.components.filter((item) => item.kind === "archive")) {
    assert.match(component.urls[0], /^https:\/\/douyinqijun\.cn\/downloads\/mindspace\/runtime\//);
    assert.match(component.urls.at(-1), /^https:\/\/github\.com\//);
  }
});

test("domestic runtime archive failure falls back to the official URL", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-runtime-fallback-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const payload = crypto.randomBytes(64 * 1024);
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { "Content-Length": payload.length });
    response.end(payload);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => server.close());
  const component = {
    id: "tool", name: "Tool", description: "fixture", version: "1.0.0", kind: "archive",
    required: true, dependencies: [], size: payload.length,
    sha256: crypto.createHash("sha256").update(payload).digest("hex"),
    executable: "tool.exe", probe: ["--version"],
    sources: {
      china: ["http://127.0.0.1:1/dead.zip"],
      official: [`http://127.0.0.1:${server.address().port}/tool.zip`],
    },
    urls: ["http://127.0.0.1:1/dead.zip", `http://127.0.0.1:${server.address().port}/tool.zip`],
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
  const manager = createRuntimeManager({
    paths, corePath: () => root, manifestPath, publicKeyPath,
    getDownloadSource: () => "china", fetch: global.fetch,
    extract: async (_archive, { dir }) => fs.writeFileSync(path.join(dir, "tool.exe"), "fixture"),
    osRelease: () => "10.0.22621",
    spawnSync: (executable) => executable === "nvidia-smi.exe"
      ? { status: 1, stdout: "", stderr: "" }
      : { status: 0, stdout: "tool 1.0.0", stderr: "" },
  });
  const result = await manager.install("tool");
  assert.equal(result.ready, true);
  assert.equal(result.items[0].sourceFallback, true);
  assert.match(result.items[0].sourceHost, /^127\.0\.0\.1:/);
  const log = fs.readFileSync(path.join(paths.logs, "runtime-manager.jsonl"), "utf8");
  assert.match(log, /"event":"download\.fallback"/);
});

function signedManifest(component) {
  const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
  const unsigned = {
    schema_version: "1.0.0", runtime_version: "test", platform: "win32", arch: "x64",
    components: Array.isArray(component) ? component : [component],
  };
  const value = crypto.sign(null, Buffer.from(canonical(unsigned)), privateKey).toString("base64");
  return { manifest: { ...unsigned, signature: { algorithm: "ed25519", value } }, publicKey };
}

test("an incomplete Python runtime is rebuilt in isolation before atomic promotion", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-python-repair-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const uvExecutable = path.join(root, "tools", "uv", "1.0.0", "uv.exe");
  const pythonExecutable = path.join(root, "python", "cpython-3.11.15-windows-x86_64-none", "python.exe");
  fs.mkdirSync(path.dirname(uvExecutable), { recursive: true });
  fs.mkdirSync(path.dirname(pythonExecutable), { recursive: true });
  fs.writeFileSync(uvExecutable, "fixture");
  fs.writeFileSync(pythonExecutable, "fixture");
  const uvComponent = {
    id: "uv", name: "uv", description: "fixture", version: "1.0.0", kind: "archive",
    required: true, dependencies: [], size: 1, sha256: "a".repeat(64),
    executable: "uv.exe", probe: ["--version"], urls: ["https://example.com/uv.zip"],
  };
  const pythonComponent = {
    id: "python", name: "Python", description: "fixture", version: "3.11.15", kind: "python",
    required: true, dependencies: ["uv"],
  };
  const { manifest, publicKey } = signedManifest([uvComponent, pythonComponent]);
  const manifestPath = path.join(root, "manifest.json");
  const publicKeyPath = path.join(root, "public.pem");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest));
  fs.writeFileSync(publicKeyPath, publicKey.export({ type: "spki", format: "pem" }));
  const paths = {};
  for (const name of ["home", "environment", "tools", "python", "venvs", "cache", "state", "models", "data", "downloads", "logs"]) {
    paths[name] = name === "home" ? root : path.join(root, name);
    fs.mkdirSync(paths[name], { recursive: true });
  }
  const markers = path.join(paths.state, "components");
  fs.mkdirSync(markers, { recursive: true });
  fs.writeFileSync(path.join(markers, "uv.json"), JSON.stringify({
    id: "uv", version: "1.0.0", executable: uvExecutable,
  }));
  fs.writeFileSync(path.join(markers, "python.json"), JSON.stringify({
    id: "python", version: "3.11.15", executable: pythonExecutable,
  }));
  let installArguments = [];
  let installDirectory = "";
  const manager = createRuntimeManager({
    paths,
    corePath: () => root,
    manifestPath,
    publicKeyPath,
    getDownloadSource: () => "official",
    osRelease: () => "10.0.22621",
    spawnSync: (executable, arguments_, options = {}) => {
      if (executable === "nvidia-smi.exe") return { status: 1, stdout: "", stderr: "" };
      if (executable === uvExecutable && arguments_[0] === "python" && arguments_[1] === "find") {
        const installRoot = options.env?.UV_PYTHON_INSTALL_DIR || paths.python;
        return {
          status: 0,
          stdout: path.join(installRoot, "cpython-3.11.15-windows-x86_64-none", "python.exe"),
          stderr: "",
        };
      }
      if (arguments_[0] === "-c") return { status: 0, stdout: "mindspace-python-ready", stderr: "" };
      return { status: 0, stdout: "ok", stderr: "" };
    },
    spawn: (_executable, arguments_) => {
      installArguments = arguments_;
      installDirectory = arguments_[arguments_.indexOf("--install-dir") + 1];
      const stagedRoot = path.join(installDirectory, "cpython-3.11.15-windows-x86_64-none");
      fs.mkdirSync(path.join(stagedRoot, "Lib", "encodings"), { recursive: true });
      fs.writeFileSync(path.join(stagedRoot, "python.exe"), "replacement");
      fs.writeFileSync(path.join(stagedRoot, "Lib", "encodings", "__init__.py"), "");
      fs.writeFileSync(path.join(stagedRoot, "Lib", "os.py"), "");
      const child = new EventEmitter();
      child.pid = 1234;
      child.stdout = new EventEmitter();
      child.stderr = new EventEmitter();
      queueMicrotask(() => child.emit("exit", 0));
      return child;
    },
  });
  const result = await manager.install("python");
  assert.equal(result.items.find((item) => item.id === "python").ready, true);
  assert.equal(installArguments.includes("--reinstall"), false);
  assert.match(installDirectory, /\.staging-python-3\.11\.15-/);
  assert.notEqual(path.resolve(installDirectory), path.resolve(paths.python));
  assert.equal(fs.readFileSync(pythonExecutable, "utf8"), "replacement");
  assert.equal(pythonRuntimeLooksComplete(pythonExecutable), true);
  assert.equal(
    fs.readdirSync(paths.python).some((name) => name.startsWith(".staging-python-")),
    false,
  );
  const marker = JSON.parse(fs.readFileSync(path.join(markers, "python.json"), "utf8"));
  assert.equal(marker.python_validation, 1);
  assert.equal(marker.repaired, true);
  assert.equal(marker.isolated_install, true);
});

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

test("runtime Python processes prefer the selected source and expose bounded official fallback", () => {
  const source = fs.readFileSync(path.join(__dirname, "runtime-manager.cjs"), "utf8");
  const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  assert.match(source, /OFFICIAL_PYPI_INDEX = "https:\/\/pypi\.org\/simple\/"/);
  assert.match(source, /downloadSource === "official" \? OFFICIAL_PYPI_INDEX : DOMESTIC_PYPI_INDEX/);
  assert.match(source, /UV_DEFAULT_INDEX: packageIndex/);
  assert.match(source, /PIP_INDEX_URL: packageIndex/);
  assert.match(source, /首次安装未完成，正在使用全新临时目录和 Astral 官方源重试/);
  assert.match(source, /\.staging-python-/);
  assert.doesNotMatch(source, /baseArguments\.push\("--reinstall"\)/);
  assert.match(source, /阿里云 PyPI 不可用，正在切换 PyPI 官方源/);
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

test("desktop package embeds uv and CPython for offline first-run bootstrap", () => {
  const packageConfig = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8"));
  const resources = packageConfig.build?.extraResources || [];
  assert.equal(
    resources.some((entry) => (
      entry.from === "bootstrap/runtime-bundle/uv"
      && entry.to === "runtime/bundled/uv"
    )),
    true,
  );
  assert.equal(
    resources.some((entry) => (
      entry.from === "bootstrap/runtime-bundle/python"
      && entry.to === "runtime/bundled/python"
    )),
    true,
  );
});

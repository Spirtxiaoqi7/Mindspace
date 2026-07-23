const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const { canonical } = require("./update-manager.cjs");

const ACTIVE_STATUSES = new Set(["checking", "downloading", "verifying", "installing"]);
const DOMESTIC_PYPI_INDEX = "https://mirrors.aliyun.com/pypi/simple/";
const OFFICIAL_PYPI_INDEX = "https://pypi.org/simple/";

function normalizeDownloadSource(value) {
  return value === "official" ? "official" : "china";
}

function sha256(file) {
  const digest = crypto.createHash("sha256");
  const descriptor = fs.openSync(file, "r");
  const buffer = Buffer.allocUnsafe(4 * 1024 * 1024);
  try {
    let count;
    while ((count = fs.readSync(descriptor, buffer, 0, buffer.length, null)) > 0) {
      digest.update(buffer.subarray(0, count));
    }
  } finally { fs.closeSync(descriptor); }
  return digest.digest("hex");
}

function verifyRuntimeManifest(manifest, publicKey) {
  if (!manifest || manifest.schema_version !== "1.0.0") throw new Error("不支持的运行时清单版本");
  if (manifest.platform !== "win32" || manifest.arch !== "x64") throw new Error("运行时清单不适用于 Windows x64");
  if (!Array.isArray(manifest.components) || !manifest.components.length) throw new Error("运行时清单缺少组件");
  const ids = new Set();
  for (const component of manifest.components) {
    if (!/^[a-z0-9-]+$/.test(component.id || "") || ids.has(component.id)) throw new Error("运行时组件 ID 无效或重复");
    ids.add(component.id);
    if (!component.version || !["archive", "python", "venv"].includes(component.kind)) throw new Error(`运行时组件 ${component.id} 定义无效`);
    if (component.kind === "archive") {
      if (!Array.isArray(component.urls) || !component.urls.length) throw new Error(`${component.id} 缺少下载地址`);
      if (!Number.isSafeInteger(component.size) || component.size <= 0) throw new Error(`${component.id} 文件大小无效`);
      if (!/^[a-f0-9]{64}$/i.test(component.sha256 || "")) throw new Error(`${component.id} SHA-256 无效`);
    }
    for (const dependency of component.dependencies || []) {
      if (dependency === component.id) throw new Error(`${component.id} 不能依赖自身`);
    }
  }
  for (const component of manifest.components) {
    for (const dependency of component.dependencies || []) {
      if (!ids.has(dependency)) throw new Error(`${component.id} 引用了未知依赖 ${dependency}`);
    }
  }
  if (manifest.signature?.algorithm !== "ed25519" || !manifest.signature.value) throw new Error("运行时清单未签名");
  const unsigned = { ...manifest };
  delete unsigned.signature;
  const valid = crypto.verify(
    null,
    Buffer.from(canonical(unsigned)),
    publicKey,
    Buffer.from(manifest.signature.value, "base64"),
  );
  if (!valid) throw new Error("运行时清单签名无效");
  return manifest;
}

function safeTarget(root, ...parts) {
  const base = path.resolve(root);
  const target = path.resolve(base, ...parts);
  if (target !== base && !target.startsWith(`${base}${path.sep}`)) throw new Error(`运行时路径越界：${parts.join("/")}`);
  return target;
}

function readJson(file, fallback = null) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return fallback; }
}

function describeError(error) {
  const message = String(error?.message || error || "未知错误");
  const detail = [error?.cause?.code, error?.cause?.message].filter(Boolean).join(" · ");
  return detail && !message.includes(detail) ? `${message}（${detail}）` : message;
}

function classifyError(error, stage = "installing") {
  const message = describeError(error);
  const normalized = message.toLowerCase();
  const causeCode = String(error?.cause?.code || error?.code || "").toUpperCase();
  let code = "INSTALL_FAILED";
  if (["ENOTFOUND", "EAI_AGAIN"].includes(causeCode) || /dns|域名|解析/.test(normalized)) code = "NETWORK_DNS";
  else if (["ECONNRESET", "ETIMEDOUT", "ECONNREFUSED"].includes(causeCode) || /timeout|超时|connection|网络/.test(normalized)) code = "NETWORK_CONNECTION";
  else if (/tls|certificate|证书/.test(normalized)) code = "NETWORK_TLS";
  else if (/http\s*404/.test(normalized)) code = "HTTP_404";
  else if (/http\s*403/.test(normalized)) code = "HTTP_403";
  else if (/sha-?256|哈希|hash/.test(normalized)) code = "CHECKSUM_MISMATCH";
  else if (/下载不完整|大小超过|大小校验|size/.test(normalized)) code = "SIZE_MISMATCH";
  else if (causeCode === "ENOSPC" || /磁盘空间|enospc/.test(normalized)) code = "DISK_FULL";
  else if (["EACCES", "EPERM"].includes(causeCode) || /不可写|权限|拒绝访问/.test(normalized)) code = "PERMISSION_DENIED";
  else if (/解压|压缩包|archive|zip/.test(normalized)) code = "EXTRACT_FAILED";
  else if (/依赖/.test(normalized)) code = "DEPENDENCY_FAILED";
  else if (/probe|探针|import|导入/.test(normalized)) code = "PROBE_FAILED";
  return { code, stage, message };
}

function operationId(componentId) {
  return `${componentId}-${Date.now().toString(36)}-${crypto.randomBytes(3).toString("hex")}`;
}

function createRuntimeManager(options) {
  const paths = options.paths;
  const publicKey = fs.readFileSync(options.publicKeyPath, "utf8");
  let manifest = verifyRuntimeManifest(readJson(options.manifestPath), publicKey);
  const states = new Map();
  let active = "";
  let controller = null;
  let systemCache = null;
  let systemCacheAt = 0;

  const componentFor = (id) => manifest.components.find((item) => item.id === id);
  const markerPath = (id) => path.join(paths.state, "components", `${id}.json`);
  const stateFor = (id) => {
    if (!states.has(id)) states.set(id, {
      status: "idle", progress: 0, downloadedBytes: 0, totalBytes: 0,
      speedBps: 0, message: "等待安装", error: "", operationId: "",
      errorCode: "", errorStage: "", startedAt: "", updatedAt: "",
    });
    return states.get(id);
  };
  const writeLog = (event, details = {}) => {
    try {
      fs.mkdirSync(paths.logs, { recursive: true });
      fs.appendFileSync(path.join(paths.logs, "runtime-manager.jsonl"), `${JSON.stringify({ at: new Date().toISOString(), event, ...details })}\n`);
    } catch {}
  };
  const setState = (id, patch) => Object.assign(stateFor(id), patch, { updatedAt: new Date().toISOString() });

  function markerFor(component, allowPrevious = false) {
    const marker = readJson(markerPath(component.id));
    if (!marker || (!allowPrevious && marker.version !== component.version) || !marker.executable) return null;
    if (!fs.existsSync(marker.executable)) return null;
    return marker;
  }

  function componentSnapshot(component) {
    const currentMarker = markerFor(component);
    const marker = currentMarker || markerFor(component, true);
    const state = stateFor(component.id);
    const ready = Boolean(marker);
    const bundledPath = component.bundled && options.bundledRoot
      ? safeTarget(options.bundledRoot, component.bundled)
      : "";
    const bundled = Boolean(bundledPath && fs.existsSync(bundledPath));
    if (currentMarker && !ACTIVE_STATUSES.has(state.status)) {
      Object.assign(state, { status: "ready", progress: 100, message: "已安装并通过校验", error: "" });
    }
    return {
      id: component.id,
      name: component.name,
      description: component.description,
      version: component.version,
      kind: component.kind,
      dependencies: component.dependencies || [],
      category: "base",
      required: component.required !== false,
      optional: component.required === false,
      ready,
      bundled,
      downloadRequired: component.kind === "archive" && !bundled,
      executable: marker?.executable || "",
      updatePending: Boolean(marker && !currentMarker),
      ...state,
      message: !ready && !ACTIVE_STATUSES.has(state.status) && bundled
        ? "安装包已内置，部署无需联网"
        : state.message,
      totalBytes: state.totalBytes || component.size || 0,
    };
  }

  function detectSystem() {
    if (systemCache && Date.now() - systemCacheAt < 10_000) return systemCache;
    const release = options.osRelease ? options.osRelease() : require("node:os").release();
    const major = Number(String(release).split(".")[0] || 0);
    const gpuProbe = (options.spawnSync || spawnSync)("nvidia-smi.exe", ["--query-gpu=name,driver_version", "--format=csv,noheader"], {
      encoding: "utf8", windowsHide: true, timeout: 4000,
    });
    const gpu = gpuProbe.status === 0 ? String(gpuProbe.stdout || "").trim() : "";
    let freeBytes = 0;
    try {
      const disk = fs.statfsSync(paths.home);
      freeBytes = Number(disk.bavail) * Number(disk.bsize);
    } catch {}
    systemCache = {
      supported: process.platform === "win32" && process.arch === "x64" && major >= 10,
      platform: process.platform,
      arch: process.arch,
      windowsRelease: release,
      writable: (() => {
        const probe = path.join(paths.state, `.write-${process.pid}`);
        try { fs.mkdirSync(paths.state, { recursive: true }); fs.writeFileSync(probe, "ok"); fs.rmSync(probe); return true; } catch { return false; }
      })(),
      freeBytes,
      nvidia: Boolean(gpu),
      nvidiaDetail: gpu,
    };
    systemCacheAt = Date.now();
    return systemCache;
  }

  function snapshot() {
    const items = manifest.components.map(componentSnapshot);
    const requiredItems = items.filter((item) => item.required);
    const failed = requiredItems.find((item) => item.status === "error");
    const running = requiredItems.find((item) => item.id === active);
    const completed = requiredItems.filter((item) => item.ready).length;
    return {
      schemaVersion: manifest.schema_version,
      runtimeVersion: manifest.runtime_version,
      active,
      ready: items.filter((item) => item.required).every((item) => item.ready),
      downloadSource: normalizeDownloadSource(options.getDownloadSource?.()),
      system: detectSystem(),
      items,
      pipeline: {
        status: failed ? "error" : running ? "running" : completed === requiredItems.length ? "ready" : "idle",
        currentId: running?.id || failed?.id || requiredItems.find((item) => !item.ready)?.id || "",
        currentName: running?.name || failed?.name || requiredItems.find((item) => !item.ready)?.name || "",
        completed,
        total: requiredItems.length,
        progress: requiredItems.length ? requiredItems.reduce((sum, item) => sum + (item.ready ? 100 : item.progress || 0), 0) / requiredItems.length : 100,
        operationId: running?.operationId || failed?.operationId || "",
        errorCode: failed?.errorCode || "",
        error: failed?.error || "",
      },
    };
  }

  function privateEnvironment(extra = {}) {
    const downloadSource = normalizeDownloadSource(options.getDownloadSource?.());
    const packageIndex = downloadSource === "official" ? OFFICIAL_PYPI_INDEX : DOMESTIC_PYPI_INDEX;
    const toolMarkers = ["powershell", "git", "uv"].map(componentFor).filter(Boolean).map((item) => markerFor(item, true)).filter(Boolean);
    const toolFolders = toolMarkers.map((marker) => path.dirname(marker.executable));
    const gitComponent = componentFor("git");
    const git = gitComponent ? markerFor(gitComponent, true) : null;
    if (git) toolFolders.push(path.resolve(path.dirname(git.executable), "..", "bin"));
    return {
      ...process.env,
      PATH: [...toolFolders, process.env.SystemRoot ? path.join(process.env.SystemRoot, "System32") : ""].filter(Boolean).join(path.delimiter),
      MINDSPACE_HOME: paths.home,
      MINDSPACE_ENVIRONMENT: paths.environment,
      MINDSPACE_MODEL_ROOT: paths.models,
      MINDSPACE_DATA_ROOT: paths.data,
      MINDSPACE_RUNTIME_DIR: paths.data,
      MINDSPACE_PWSH: componentFor("powershell") ? markerFor(componentFor("powershell"), true)?.executable || "" : "",
      MINDSPACE_UV: componentFor("uv") ? markerFor(componentFor("uv"), true)?.executable || "" : "",
      UV_PYTHON_INSTALL_DIR: paths.python,
      UV_CACHE_DIR: path.join(paths.cache, "uv"),
      UV_MANAGED_PYTHON: "1",
      UV_DEFAULT_INDEX: packageIndex,
      PIP_CACHE_DIR: path.join(paths.cache, "pip"),
      PIP_INDEX_URL: packageIndex,
      MINDSPACE_DOWNLOAD_SOURCE: downloadSource,
      PIP_DISABLE_PIP_VERSION_CHECK: "1",
      PYTHONUTF8: "1",
      ...extra,
    };
  }

  async function fetchWithTimeout(url, init = {}, timeoutMs = 30_000) {
    const timeoutController = new AbortController();
    const relay = () => timeoutController.abort();
    controller?.signal.addEventListener("abort", relay, { once: true });
    const timer = setTimeout(() => timeoutController.abort(), timeoutMs);
    try { return await options.fetch(url, { ...init, signal: timeoutController.signal }); }
    finally {
      clearTimeout(timer);
      controller?.signal.removeEventListener("abort", relay);
    }
  }

  async function downloadArchive(component) {
    const downloadRoot = path.join(paths.downloads, "runtime");
    fs.mkdirSync(downloadRoot, { recursive: true });
    const finalPath = path.join(downloadRoot, `${component.id}-${component.version}.zip`);
    const partial = `${finalPath}.partial`;
    if (fs.existsSync(finalPath) && fs.statSync(finalPath).size === component.size && sha256(finalPath) === component.sha256.toLowerCase()) return finalPath;
    if (fs.existsSync(partial) && fs.statSync(partial).size > component.size) fs.rmSync(partial, { force: true });
    let lastError;
    const downloadSource = normalizeDownloadSource(options.getDownloadSource?.());
    const sourceUrls = component.sources?.[downloadSource]
      ? [].concat(component.sources[downloadSource])
      : [downloadSource === "official" ? component.urls.at(-1) : component.urls[0]];
    for (const [urlIndex, url] of sourceUrls.filter(Boolean).entries()) {
      const attempts = urlIndex < sourceUrls.length - 1 ? 1 : 3;
      for (let attempt = 1; attempt <= attempts; attempt += 1) {
        try {
          let offset = fs.existsSync(partial) ? fs.statSync(partial).size : 0;
          const headers = { "User-Agent": "Mindspace-Runtime/0.4.0" };
          if (offset) headers.Range = `bytes=${offset}-`;
          let response = await fetchWithTimeout(url, { headers, redirect: "follow" }, 15_000);
          if (offset && response.status !== 206) {
            fs.rmSync(partial, { force: true });
            offset = 0;
            response = await fetchWithTimeout(url, { headers: { "User-Agent": headers["User-Agent"] }, redirect: "follow" }, 15_000);
          }
          if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
          const output = fs.createWriteStream(partial, { flags: offset ? "a" : "w" });
          const transfer = { startedAt: Date.now(), bytes: 0 };
          let received = offset;
          try {
            for await (const chunk of response.body) {
              if (controller.signal.aborted) throw new Error("安装已取消");
              if (!output.write(chunk)) await new Promise((resolve) => output.once("drain", resolve));
              received += chunk.length;
              transfer.bytes += chunk.length;
              if (received > component.size) throw new Error("下载大小超过清单声明");
              const seconds = Math.max(0.25, (Date.now() - transfer.startedAt) / 1000);
              setState(component.id, {
                status: "downloading", progress: Math.min(88, received / component.size * 88),
                downloadedBytes: received, totalBytes: component.size,
                speedBps: transfer.bytes / seconds, message: `正在下载 ${component.name}`,
              });
            }
            await new Promise((resolve, reject) => output.end((error) => error ? reject(error) : resolve()));
          } catch (error) { output.destroy(); throw error; }
          if (received !== component.size) throw new Error(`下载不完整：${received}/${component.size}`);
          setState(component.id, { status: "verifying", progress: 90, message: "正在校验 SHA-256", speedBps: 0 });
          if (sha256(partial) !== component.sha256.toLowerCase()) {
            fs.rmSync(partial, { force: true });
            throw new Error("SHA-256 校验失败");
          }
          fs.rmSync(finalPath, { force: true });
          fs.renameSync(partial, finalPath);
          return finalPath;
        } catch (error) {
          if (controller.signal.aborted) throw error;
          lastError = error;
          writeLog("download.retry", { component: component.id, url, attempt, source: downloadSource, error: describeError(error) });
          if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, 500 * attempt));
        }
      }
    }
    throw lastError || new Error(`${component.name} 下载失败`);
  }

  function runProbe(executable, arguments_, environment, timeout = 30_000) {
    const result = (options.spawnSync || spawnSync)(executable, arguments_, {
      encoding: "utf8", windowsHide: true, timeout, env: environment,
    });
    if (result.status !== 0) throw new Error(String(result.stderr || result.stdout || `退出码 ${result.status}`).trim());
    return String(result.stdout || result.stderr || "").trim();
  }

  function adoptExisting(component, executable, probeArguments, environment, details = {}) {
    if (!fs.existsSync(executable)) return false;
    try {
      const probe = runProbe(executable, probeArguments, environment, 120_000);
      writeMarker(component, executable, { adopted: true, probe, ...details });
      return true;
    } catch {
      return false;
    }
  }

  function promoteStaging(staging, target) {
    if (!fs.existsSync(target)) {
      fs.renameSync(staging, target);
      return;
    }
    const previous = `${target}.previous-${process.pid}-${Date.now()}`;
    try {
      fs.renameSync(target, previous);
    } catch (error) {
      if (["EBUSY", "EPERM", "EACCES"].includes(error?.code)) {
        throw new Error("现有运行时仍被旧服务占用；请先停止本机服务后重试", { cause: error });
      }
      throw error;
    }
    try {
      fs.renameSync(staging, target);
    } catch (error) {
      try { fs.renameSync(previous, target); } catch {}
      throw error;
    }
    try { fs.rmSync(previous, { recursive: true, force: true }); } catch {}
  }

  function writeMarker(component, executable, details = {}) {
    const target = markerPath(component.id);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const temporary = `${target}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, `${JSON.stringify({
      id: component.id, version: component.version, executable,
      installed_at: new Date().toISOString(), ...details,
    }, null, 2)}\n`);
    fs.renameSync(temporary, target);
  }

  function writeCurrentPointer(parent, component, executable) {
    const target = path.join(parent, "current.json");
    const temporary = `${target}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, `${JSON.stringify({ version: component.version, executable, updated_at: new Date().toISOString() }, null, 2)}\n`);
    fs.renameSync(temporary, target);
  }

  function pruneVersionDirectories(parent, current) {
    const directories = fs.readdirSync(parent, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && !entry.name.startsWith(".staging-") && entry.name !== current)
      .map((entry) => ({ path: path.join(parent, entry.name), mtime: fs.statSync(path.join(parent, entry.name)).mtimeMs }))
      .sort((left, right) => right.mtime - left.mtime);
    for (const stale of directories.slice(1)) fs.rmSync(stale.path, { recursive: true, force: true });
  }

  async function installArchive(component) {
    const parent = safeTarget(paths.tools, component.id);
    const target = safeTarget(parent, component.version);
    const staging = safeTarget(parent, `.staging-${component.version}-${process.pid}-${Date.now()}`);
    fs.mkdirSync(parent, { recursive: true });
    const installedExecutable = safeTarget(target, component.executable);
    if (adoptExisting(component, installedExecutable, component.probe || ["--version"], privateEnvironment())) {
      writeCurrentPointer(parent, component, installedExecutable);
      return;
    }
    for (const entry of fs.readdirSync(parent, { withFileTypes: true })) {
      if (entry.isDirectory() && entry.name.startsWith(".staging-")) {
        fs.rmSync(safeTarget(parent, entry.name), { recursive: true, force: true });
      }
    }
    fs.rmSync(staging, { recursive: true, force: true });
    fs.mkdirSync(staging, { recursive: true });
    setState(component.id, { status: "installing", progress: 92, message: `正在解压 ${component.name}` });
    try {
      const bundled = component.bundled && options.bundledRoot ? safeTarget(options.bundledRoot, component.bundled) : "";
      if (bundled && fs.existsSync(bundled)) {
        setState(component.id, { status: "installing", progress: 75, message: `正在部署预置 ${component.name}` });
        fs.rmSync(path.join(paths.downloads, "runtime", `${component.id}-${component.version}.zip.partial`), { force: true });
        fs.cpSync(bundled, staging, { recursive: true, force: true });
      } else {
        const archive = await downloadArchive(component);
        await options.extract(archive, { dir: staging });
      }
      const executable = safeTarget(staging, component.executable);
      if (!fs.existsSync(executable)) throw new Error(`压缩包缺少 ${component.executable}`);
      const output = runProbe(executable, component.probe || ["--version"], privateEnvironment());
      promoteStaging(staging, target);
      const finalExecutable = safeTarget(target, component.executable);
      writeMarker(component, finalExecutable, { probe: output });
      writeCurrentPointer(parent, component, finalExecutable);
      pruneVersionDirectories(parent, component.version);
    } finally { fs.rmSync(staging, { recursive: true, force: true }); }
  }

  async function runStreaming(component, executable, arguments_, environment, progressStart, progressEnd) {
    await new Promise((resolve, reject) => {
      const child = (options.spawn || spawn)(executable, arguments_, {
        cwd: options.corePath(), env: environment, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
      });
      let output = "";
      let tick = progressStart;
      const observe = (chunk) => {
        output += chunk.toString("utf8");
        tick = Math.min(progressEnd - 1, tick + 0.4);
        setState(component.id, { status: "installing", progress: tick, message: output.trim().split(/\r?\n/).at(-1)?.slice(0, 160) || `正在安装 ${component.name}` });
      };
      child.stdout.on("data", observe); child.stderr.on("data", observe);
      const cancel = () => (options.spawnSync || spawnSync)("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
      controller.signal.addEventListener("abort", cancel, { once: true });
      child.once("error", reject);
      child.once("exit", (code) => {
        controller.signal.removeEventListener("abort", cancel);
        if (controller.signal.aborted) return reject(new Error("安装已取消"));
        if (code !== 0) return reject(new Error(output.trim().slice(-1200) || `${component.name} 安装失败（退出码 ${code}）`));
        resolve();
      });
    });
  }

  async function installPython(component) {
    const uv = markerFor(componentFor("uv"));
    if (!uv) throw new Error("私有 uv 尚未安装");
    fs.mkdirSync(paths.python, { recursive: true });
    const environment = privateEnvironment({ UV_MANAGED_PYTHON: "1" });
    setState(component.id, { status: "installing", progress: 5, message: `正在安装 Python ${component.version}` });
    const bundled = component.bundled && options.bundledRoot ? safeTarget(options.bundledRoot, component.bundled) : "";
    if (bundled && fs.existsSync(bundled)) {
      const staging = safeTarget(paths.python, `.staging-${component.version}-${process.pid}-${Date.now()}`);
      const target = safeTarget(paths.python, path.basename(bundled));
      const installedExecutable = path.join(target, "python.exe");
      if (adoptExisting(component, installedExecutable, ["-c", "import ensurepip, venv; print('mindspace-python-ready')"], environment, { bundled: true })) {
        writeCurrentPointer(paths.python, component, installedExecutable);
        return;
      }
      try {
        fs.cpSync(bundled, staging, { recursive: true, force: true });
        const executable = path.join(staging, "python.exe");
        runProbe(executable, ["--version"], environment);
        promoteStaging(staging, target);
        const finalExecutable = path.join(target, "python.exe");
        writeMarker(component, finalExecutable, { bundled: true });
        writeCurrentPointer(paths.python, component, finalExecutable);
        return;
      } finally { fs.rmSync(staging, { recursive: true, force: true }); }
    }
    const baseArguments = [
      "python", "install", component.version, "--install-dir", paths.python,
      "--cache-dir", path.join(paths.cache, "uv"), "--managed-python", "--no-bin", "--no-registry", "--system-certs",
    ];
    const downloadSource = normalizeDownloadSource(options.getDownloadSource?.());
    try {
      await runStreaming(component, uv.executable, downloadSource === "china" && component.mirror ? [...baseArguments, "--mirror", component.mirror] : baseArguments, environment, 5, 88);
    } catch (error) {
      if (controller.signal.aborted) throw error;
      throw new Error(`${downloadSource === "china" ? "国内 Python 镜像" : "Python 官方源"}安装失败：${describeError(error)}`);
    }
    const executable = runProbe(uv.executable, [
      "python", "find", component.version, "--managed-python", "--no-python-downloads",
    ], environment);
    runProbe(executable, ["--version"], environment);
    writeMarker(component, executable);
  }

  async function installVenv(component) {
    const uv = markerFor(componentFor("uv"));
    const python = markerFor(componentFor("python"));
    if (!uv || !python) throw new Error("私有 uv 或 Python 尚未安装");
    const parent = safeTarget(paths.venvs, component.environment);
    const target = safeTarget(parent, component.version);
    const staging = safeTarget(parent, `.staging-${component.version}-${process.pid}-${Date.now()}`);
    fs.mkdirSync(parent, { recursive: true });
    const installedExecutable = path.join(target, "Scripts", "python.exe");
    if (adoptExisting(
      component,
      installedExecutable,
      ["-c", "import fastapi, langgraph, sentence_transformers; print('mindspace-runtime-ready')"],
      privateEnvironment(),
    )) {
      writeCurrentPointer(parent, component, installedExecutable);
      return;
    }
    fs.rmSync(staging, { recursive: true, force: true });
    const downloadSource = normalizeDownloadSource(options.getDownloadSource?.());
    const packageIndex = downloadSource === "official" ? OFFICIAL_PYPI_INDEX : DOMESTIC_PYPI_INDEX;
    const environment = privateEnvironment({ UV_PROJECT_ENVIRONMENT: staging, UV_MANAGED_PYTHON: "1" });
    try {
      setState(component.id, { status: "installing", progress: 3, message: "正在创建私有虚拟环境与 pip" });
      await runStreaming(component, uv.executable, ["venv", staging, "--python", python.executable, "--seed", "--no-project"], environment, 3, 18);
      const syncArguments = [
        "sync", "--frozen", "--extra", "embeddings", "--project", options.corePath(),
        "--python", path.join(staging, "Scripts", "python.exe"), "--no-managed-python", "--system-certs",
      ];
      try {
        await runStreaming(component, uv.executable, [...syncArguments, "--default-index", packageIndex], environment, 18, 92);
      } catch (error) {
        if (controller.signal.aborted) throw error;
        throw new Error(`${downloadSource === "china" ? "阿里云 PyPI" : "PyPI 官方源"}安装失败：${describeError(error)}`);
      }
      const executable = path.join(staging, "Scripts", "python.exe");
      await runStreaming(component, uv.executable, [
        "pip", "install", "--python", executable, "pip", "--default-index",
        packageIndex, "--system-certs",
      ], environment, 92, 96);
      const probe = runProbe(executable, ["-c", "import fastapi, langgraph, sentence_transformers; print('mindspace-runtime-ready')"], environment, 120_000);
      runProbe(executable, ["-m", "pip", "--version"], environment);
      promoteStaging(staging, target);
      const finalExecutable = path.join(target, "Scripts", "python.exe");
      writeMarker(component, finalExecutable, { probe });
      writeCurrentPointer(parent, component, finalExecutable);
      pruneVersionDirectories(parent, component.version);
    } finally { fs.rmSync(staging, { recursive: true, force: true }); }
  }

  async function ensureDependencies(component) {
    for (const id of component.dependencies || []) {
      if (!markerFor(componentFor(id))) await install(id);
    }
  }

  async function install(id) {
    const component = componentFor(id);
    if (!component) throw new Error(`未知运行时组件：${id}`);
    if (active && active !== id) throw new Error(`正在安装 ${active}`);
    if (markerFor(component)) return snapshot();
    if (!active) {
      for (const dependency of component.dependencies || []) {
        const required = componentFor(dependency);
        if (!markerFor(required)) await install(dependency);
      }
    }
    active = id;
    controller = new AbortController();
    const currentOperation = operationId(id);
    setState(id, { status: "checking", progress: 0, downloadedBytes: 0, totalBytes: component.size || 0, speedBps: 0, message: "正在检查依赖", error: "", operationId: currentOperation, errorCode: "", errorStage: "", startedAt: new Date().toISOString() });
    writeLog("component.start", { component: id, version: component.version, operation_id: currentOperation, source: normalizeDownloadSource(options.getDownloadSource?.()) });
    try {
      if (component.kind === "archive") await installArchive(component);
      else if (component.kind === "python") await installPython(component);
      else if (component.kind === "venv") await installVenv(component);
      setState(id, { status: "ready", progress: 100, speedBps: 0, message: "已安装并通过校验", error: "" });
      writeLog("component.ready", { component: id, version: component.version, operation_id: currentOperation });
      return snapshot();
    } catch (error) {
      const cancelled = controller?.signal.aborted;
      const diagnosis = classifyError(error, stateFor(id).status || "installing");
      setState(id, { status: cancelled ? "cancelled" : "error", speedBps: 0, message: cancelled ? "安装已取消，可继续" : "安装失败", error: cancelled ? "" : diagnosis.message, errorCode: cancelled ? "CANCELLED" : diagnosis.code, errorStage: diagnosis.stage });
      writeLog(cancelled ? "component.cancelled" : "component.error", { component: id, operation_id: currentOperation, error_code: cancelled ? "CANCELLED" : diagnosis.code, stage: diagnosis.stage, error: diagnosis.message });
      throw error;
    } finally { active = ""; controller = null; }
  }

  async function installAll() {
    const system = detectSystem();
    if (!system.supported) throw new Error("Mindspace 正式版仅支持 Windows 10/11 x64");
    if (!system.writable) throw new Error(`应用目录不可写：${paths.home}`);
    for (const component of manifest.components.filter((item) => item.required !== false)) {
      if (!markerFor(component)) await install(component.id);
    }
    return snapshot();
  }

  async function repair() {
    for (const component of manifest.components) {
      const marker = markerFor(component);
      if (!marker) {
        if (component.required !== false) await install(component.id);
        continue;
      }
      try {
        if (component.kind === "archive") runProbe(marker.executable, component.probe || ["--version"], privateEnvironment());
        else if (component.kind === "python") runProbe(marker.executable, ["--version"], privateEnvironment());
        else runProbe(marker.executable, ["-c", "import fastapi, langgraph, sentence_transformers"], privateEnvironment(), 120_000);
      } catch {
        fs.rmSync(markerPath(component.id), { force: true });
        await install(component.id);
      }
    }
    return snapshot();
  }

  function cancel(id = "") {
    if (controller && (!id || active === id)) controller.abort();
    return snapshot();
  }

  async function refreshManifest(url) {
    if (!url) return snapshot();
    if (!/^https:\/\//i.test(url) && !/^http:\/\/(127\.0\.0\.1|localhost)(?::\d+)?\//i.test(url)) throw new Error("运行时清单必须使用 HTTPS");
    controller = new AbortController();
    try {
      const response = await fetchWithTimeout(url, { cache: "no-store" }, 15_000);
      if (!response.ok) throw new Error(`运行时清单请求失败：HTTP ${response.status}`);
      manifest = verifyRuntimeManifest(await response.json(), publicKey);
      return snapshot();
    } finally { controller = null; }
  }

  return {
    cancel, componentFor, install, installAll, privateEnvironment, refreshManifest,
    repair, snapshot, verifyRuntimeManifest,
  };
}

module.exports = { classifyError, createRuntimeManager, safeTarget, sha256, verifyRuntimeManifest };

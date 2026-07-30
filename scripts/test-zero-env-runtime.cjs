const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const extractZip = require("../desktop/node_modules/extract-zip");
const { createRuntimeManager } = require("../desktop/runtime-manager.cjs");
const {
  createComponentManager,
  DEFAULT_COMPONENTS,
} = require("../desktop/component-manager.cjs");

function requireArgument(index, name) {
  const value = process.argv[index];
  if (!value) throw new Error(`缺少参数：${name}`);
  return path.resolve(value);
}

function directoryBytes(root) {
  if (!fs.existsSync(root)) return 0;
  let total = 0;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) total += directoryBytes(target);
    else if (entry.isFile()) total += fs.statSync(target).size;
  }
  return total;
}

async function waitForCore(url, processHandle, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    if (processHandle.exitCode !== null) {
      throw new Error(`Core 提前退出，代码 ${processHandle.exitCode}`);
    }
    try {
      const response = await fetch(`${url}/api/v1/health`, { cache: "no-store" });
      if (response.ok) return response.json();
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = String(error?.message || error);
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`Core 健康检查超时：${lastError}`);
}

async function main() {
  const workspace = requireArgument(2, "workspace");
  const corePath = requireArgument(3, "corePath");
  const projectRoot = path.resolve(__dirname, "..");
  const port = Number(process.argv[4] || 9877);
  if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) {
    throw new Error(`端口无效：${port}`);
  }
  if (!fs.existsSync(path.join(corePath, "pyproject.toml"))) {
    throw new Error(`内置 Core 不完整：${corePath}`);
  }

  const paths = {
    home: workspace,
    application: path.join(workspace, "application"),
    core: corePath,
    environment: path.join(workspace, "environment"),
    tools: path.join(workspace, "environment", "tools"),
    python: path.join(workspace, "environment", "python"),
    venvs: path.join(workspace, "environment", "venvs"),
    cache: path.join(workspace, "environment", "cache"),
    state: path.join(workspace, "environment", "state"),
    models: path.join(workspace, "models"),
    data: path.join(workspace, "data"),
    downloads: path.join(workspace, "downloads"),
    logs: path.join(workspace, "logs"),
    backups: path.join(workspace, "backups"),
  };
  for (const target of Object.values(paths)) fs.mkdirSync(target, { recursive: true });

  const runtimeManager = createRuntimeManager({
    paths,
    corePath: () => corePath,
    manifestPath: path.join(projectRoot, "desktop", "assets", "runtime-manifest.json"),
    publicKeyPath: path.join(projectRoot, "desktop", "assets", "update-public-key.pem"),
    bundledRoot: path.join(workspace, "__intentionally_absent_bundled_runtime__"),
    fetch: (...arguments_) => fetch(...arguments_),
    extract: extractZip,
    getDownloadSource: () => "china",
  });

  const componentManager = createComponentManager({
    rootPath: () => corePath,
    fetch: (...arguments_) => fetch(...arguments_),
    logFile: path.join(paths.logs, "zero-env-model-download.log"),
    markerRoot: path.join(paths.state, "model-components"),
    resolveTarget: (component) => (
      component.id === "embedding"
        ? path.join(paths.models, "shibing624", "text2vec-base-chinese")
        : path.join(workspace, component.target)
    ),
    getDownloadSource: () => "china",
    catalog: DEFAULT_COMPONENTS,
  });

  const startedAt = new Date().toISOString();
  const timer = setInterval(() => {
    const runtime = runtimeManager.snapshot();
    const models = componentManager.snapshot();
    const active = runtime.active
      ? runtime.items.find((item) => item.id === runtime.active)
      : models.items.find((item) => item.id === models.active);
    if (active) {
      process.stdout.write(
        `${JSON.stringify({
          stage: "progress",
          id: active.id,
          status: active.status,
          progress: Math.round(Number(active.progress || 0) * 10) / 10,
          message: active.message,
        })}\n`,
      );
    }
  }, 5_000);

  let coreProcess = null;
  try {
    const runtimeStart = Date.now();
    await runtimeManager.installAll();
    const runtimeSeconds = (Date.now() - runtimeStart) / 1000;

    const modelStart = Date.now();
    await componentManager.downloadAll();
    const modelSeconds = (Date.now() - modelStart) / 1000;

    const runtimeSnapshot = runtimeManager.snapshot();
    const modelSnapshot = componentManager.snapshot();
    if (!runtimeSnapshot.ready) throw new Error("基础运行时安装完成后仍未就绪");
    const embedding = modelSnapshot.items.find((item) => item.id === "embedding");
    if (!embedding?.ready) throw new Error("中文向量模型安装完成后仍未就绪");

    const markerPath = path.join(paths.state, "components", "core-venv.json");
    const marker = JSON.parse(fs.readFileSync(markerPath, "utf8"));
    if (!fs.existsSync(marker.executable)) throw new Error("Core 私有 Python 不存在");
    const privateEnvironment = runtimeManager.privateEnvironment({
      PYTHONPATH: path.join(corePath, "src"),
      MINDSPACE_PORT: String(port),
      MINDSPACE_HOST: "127.0.0.1",
      MINDSPACE_DEBUG: "0",
      MINDSPACE_LLM_MODE: "deterministic",
      MINDSPACE_AUTO_TTS: "0",
    });
    const pollution = ["PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV"]
      .filter((key) => privateEnvironment[key]);
    if (pollution.length) throw new Error(`私有环境仍继承宿主变量：${pollution.join(", ")}`);

    const coreLog = fs.openSync(path.join(paths.logs, "zero-env-core.log"), "a");
    coreProcess = spawn(marker.executable, ["-m", "mindspace_graph.server"], {
      cwd: corePath,
      env: privateEnvironment,
      windowsHide: true,
      stdio: ["ignore", coreLog, coreLog],
    });
    const baseUrl = `http://127.0.0.1:${port}`;
    const health = await waitForCore(baseUrl, coreProcess);
    const rootResponse = await fetch(`${baseUrl}/`);
    const characterResponse = await fetch(`${baseUrl}/api/v1/characters`);
    if (!rootResponse.ok || !characterResponse.ok) {
      throw new Error(
        `Core 页面/API 不可用：root=${rootResponse.status} characters=${characterResponse.status}`,
      );
    }

    const report = {
      schema_version: "1.0.0",
      status: "passed",
      started_at: startedAt,
      completed_at: new Date().toISOString(),
      workspace,
      core_path: corePath,
      runtime_seconds: Math.round(runtimeSeconds * 1000) / 1000,
      embedding_seconds: Math.round(modelSeconds * 1000) / 1000,
      runtime_ready: runtimeSnapshot.ready,
      embedding_ready: embedding.ready,
      core_version: health.version,
      root_status: rootResponse.status,
      characters_status: characterResponse.status,
      private_python: marker.executable,
      inherited_environment_pollution: pollution,
      environment_bytes: directoryBytes(paths.environment),
      model_bytes: directoryBytes(paths.models),
      downloads_bytes: directoryBytes(paths.downloads),
      optional_voice_installed: false,
    };
    const reportPath = path.join(paths.logs, "zero-env-runtime-report.json");
    fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
    process.stdout.write(`${JSON.stringify({ ...report, report: reportPath })}\n`);
  } finally {
    clearInterval(timer);
    if (coreProcess && coreProcess.exitCode === null) {
      coreProcess.kill();
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
  }
}

main().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});

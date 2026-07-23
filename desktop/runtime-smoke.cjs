const { app, net } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const extractZip = require("extract-zip");
const { createRuntimeManager } = require("./runtime-manager.cjs");
const { createComponentManager, DEFAULT_COMPONENTS } = require("./component-manager.cjs");

app.whenReady().then(async () => {
  const project = path.resolve(__dirname, "..");
  const home = path.resolve(process.argv.at(-1));
  const paths = {
    home,
    application: path.join(home, "application"),
    core: path.join(home, "application", "core"),
    environment: path.join(home, "environment"),
    tools: path.join(home, "environment", "tools"),
    python: path.join(home, "environment", "python"),
    venvs: path.join(home, "environment", "venvs"),
    cache: path.join(home, "environment", "cache"),
    state: path.join(home, "environment", "state"),
    models: path.join(home, "models"),
    data: path.join(home, "data"),
    downloads: path.join(home, "downloads"),
    logs: path.join(home, "logs"),
  };
  for (const directory of Object.values(paths)) fs.mkdirSync(directory, { recursive: true });
  const manager = createRuntimeManager({
    paths,
    corePath: () => project,
    manifestPath: path.join(__dirname, "assets", "runtime-manifest.json"),
    publicKeyPath: path.join(__dirname, "assets", "update-public-key.pem"),
    bundledRoot: path.join(__dirname, "bootstrap", "runtime-bundle"),
    fetch: (...arguments_) => net.fetch(...arguments_),
    extract: extractZip,
  });
  const timer = setInterval(() => {
    const state = manager.snapshot();
    const active = state.items.find((item) => item.id === state.active);
    if (active) process.stdout.write(`${JSON.stringify({ id: active.id, status: active.status, progress: Number(active.progress.toFixed(1)), message: active.message })}\n`);
  }, 1000);
  try {
    const state = await manager.installAll();
    const models = createComponentManager({
      rootPath: () => project,
      catalog: DEFAULT_COMPONENTS.filter((component) => component.id === "embedding"),
      fetch: (...arguments_) => net.fetch(...arguments_),
      resolveTarget: () => path.join(paths.models, "shibing624", "text2vec-base-chinese"),
      markerRoot: path.join(paths.state, "components"),
      logFile: path.join(paths.logs, "runtime-smoke.jsonl"),
    });
    const modelState = await models.downloadAll();
    process.stdout.write(`${JSON.stringify({
      ready: state.ready && modelState.items.every((item) => item.ready),
      home,
      items: state.items.map(({ id, version, ready, executable }) => ({ id, version, ready, executable })),
      models: modelState.items.map(({ id, ready, path: modelPath }) => ({ id, ready, path: modelPath })),
    })}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
  } finally {
    clearInterval(timer);
    app.quit();
  }
});

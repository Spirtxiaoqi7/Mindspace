const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createUpdateController } = require("./update-controller.cjs");

test("automatic update failures persist a readable state and append an audit log", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-update-controller-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "pyproject.toml"), '[project]\nversion = "0.9.1"\n');
  const logs = path.join(root, "logs");
  let config = { updateUrl: "https://updates.example.invalid/catalog.json", updateChannel: "stable" };
  const controller = createUpdateController({
    app: { getVersion: () => "0.9.1", isPackaged: false },
    dirname: __dirname,
    fetch: async () => { throw new Error("network unavailable"); },
    rootPath: () => root,
    resolvePowerShell: () => "pwsh",
    currentLayout: () => ({ downloads: path.join(root, "downloads"), logs }),
    readConfig: () => config,
    writeConfig: (next) => { config = next; },
    stopServices: async () => {}, startServices: async () => {}, waitForHealth: async () => true,
    logRoot: () => logs,
  });
  await controller.checkConfiguredFeed();
  const state = controller.snapshot().automaticCheck;
  assert.deepEqual({ status: state.status, phase: state.phase, error: state.error }, { status: "error", phase: "check", error: "network unavailable" });
  assert.equal(config.automaticUpdateCheck.error, "network unavailable");
  assert.match(fs.readFileSync(path.join(logs, "update-controller.jsonl"), "utf8"), /network unavailable/);
});

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createDiagnosticsController } = require("./diagnostics-controller.cjs");

test("diagnostics include environment, maintenance, update and stability evidence with secrets redacted", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-diagnostics-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const logs = path.join(root, "logs");
  const userData = path.join(root, "userdata");
  fs.mkdirSync(logs, { recursive: true });
  fs.mkdirSync(userData, { recursive: true });
  for (const name of ["environment-registry.jsonl", "runtime-manager.jsonl", "maintenance-verify.log", "maintenance-repair.log", "update-controller.jsonl"]) {
    fs.writeFileSync(path.join(logs, name), `event=${name} token=secret-value\n`);
  }
  fs.writeFileSync(path.join(userData, "mindspace-stability.log"), "gpu event authorization: Bearer secret-token\n");
  const controller = createDiagnosticsController({
    app: { getVersion: () => "0.9.1", isPackaged: true, getPath: () => userData },
    currentLayout: () => ({ home: root }),
    downloadSource: () => "china",
    logRoot: () => logs,
    runtimeSnapshot: () => ({ system: { windowsRelease: "10" } }),
    writeJsonAtomic: (file, value) => fs.writeFileSync(file, JSON.stringify(value)),
  });
  const report = controller.createReport();
  for (const name of ["environment-registry.jsonl", "runtime-manager.jsonl", "maintenance-verify.log", "maintenance-repair.log", "update-controller.jsonl", "mindspace-stability.log"]) {
    assert.equal(fs.existsSync(path.join(report, name)), true, name);
    assert.doesNotMatch(fs.readFileSync(path.join(report, name), "utf8"), /secret-(?:value|token)/);
  }
});

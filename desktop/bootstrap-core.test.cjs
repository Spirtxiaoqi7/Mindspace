const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { ensureCoreRoot, resolveWorkspaceRoot } = require("./bootstrap-core.cjs");

test("packaged launcher uses a writable user workspace instead of the build-machine hint", (context) => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-user-data-"));
  context.after(() => fs.rmSync(userData, { recursive: true, force: true }));
  const app = { isPackaged: true, getPath: () => userData };
  const root = resolveWorkspaceRoot({
    app,
    configuredRoot: "",
    environmentRoot: "",
    hintedRoot: "A:\\RAG\\langgarph-rag",
    dirname: __dirname,
  });
  assert.equal(root, path.join(userData, "app"));
});

test("first launch expands the bundled core into the selected workspace", async (context) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-bootstrap-"));
  context.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const root = path.join(parent, "app");
  const archive = path.join(parent, "mindspace-core.zip");
  fs.writeFileSync(archive, "fixture");
  const extract = (_source, staging) => {
    const payload = path.join(staging, "payload");
    fs.mkdirSync(path.join(payload, "scripts"), { recursive: true });
    fs.writeFileSync(path.join(payload, "pyproject.toml"), "[project]\n");
    fs.writeFileSync(path.join(payload, "scripts", "start.ps1"), "Write-Output ready\n");
  };
  const result = await ensureCoreRoot({ root, archive, extract });
  assert.equal(result.created, true);
  assert.equal(fs.existsSync(path.join(root, "pyproject.toml")), true);
  assert.equal(fs.existsSync(path.join(root, "runtime", "bootstrap.json")), true);
});

test("newer bundled core upgrades code while preserving runtime data", async (context) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-upgrade-"));
  context.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const root = path.join(parent, "app");
  const archive = path.join(parent, "mindspace-core.zip");
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.mkdirSync(path.join(root, "runtime", "data"), { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), "version = \"0.3.2\"\n");
  fs.writeFileSync(path.join(root, "payload.json"), '{"version":"0.3.2"}\n');
  fs.writeFileSync(path.join(root, "scripts", "start.ps1"), "old\n");
  fs.writeFileSync(path.join(root, "runtime", "data", "session.json"), "keep\n");
  fs.writeFileSync(archive, "fixture");
  const extract = (_source, staging) => {
    const payload = path.join(staging, "payload");
    fs.mkdirSync(path.join(payload, "scripts"), { recursive: true });
    fs.writeFileSync(path.join(payload, "pyproject.toml"), "version = \"0.3.3\"\n");
    fs.writeFileSync(path.join(payload, "payload.json"), '{"version":"0.3.3"}\n');
    fs.writeFileSync(path.join(payload, "scripts", "start.ps1"), "new\n");
  };
  const result = await ensureCoreRoot({ root, archive, version: "0.3.3", extract });
  assert.equal(result.upgraded, true);
  assert.equal(fs.readFileSync(path.join(root, "scripts", "start.ps1"), "utf8"), "new\n");
  assert.equal(fs.readFileSync(path.join(root, "runtime", "data", "session.json"), "utf8"), "keep\n");
});

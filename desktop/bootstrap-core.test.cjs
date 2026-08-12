const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { bootstrapResidues, ensureCoreRoot, isCoreRoot, resolveWorkspaceRoot } = require("./bootstrap-core.cjs");

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

test("core root rejects a declared but missing project README", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-core-readme-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), '[project]\nreadme = "README.md"\n');
  fs.writeFileSync(path.join(root, "scripts", "start.ps1"), "Write-Output ready\n");
  assert.equal(isCoreRoot(root), false);
  fs.writeFileSync(path.join(root, "README.md"), "# Mindspace\n");
  assert.equal(isCoreRoot(root), true);
});

test("core root rejects any declared license metadata missing from the payload", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-core-license-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), '[project]\nlicense = { file = "LICENSE" }\n');
  fs.writeFileSync(path.join(root, "scripts", "start.ps1"), "Write-Output ready\n");
  assert.equal(isCoreRoot(root), false);
  fs.writeFileSync(path.join(root, "LICENSE"), "Mindspace license\n");
  assert.equal(isCoreRoot(root), true);
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
  assert.equal(fs.existsSync(path.join(root, ".mindspace-bootstrap.json")), true);
});

test("newer bundled core atomically replaces code so stale files disappear while external data stays unchanged", async (context) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-upgrade-"));
  context.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const root = path.join(parent, "app");
  const archive = path.join(parent, "mindspace-core.zip");
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  const dataRoot = path.join(parent, "data");
  fs.mkdirSync(dataRoot, { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), "version = \"0.3.2\"\n");
  fs.writeFileSync(path.join(root, "payload.json"), '{"version":"0.3.2"}\n');
  fs.writeFileSync(path.join(root, "scripts", "start.ps1"), "old\n");
  fs.writeFileSync(path.join(root, "stale.py"), "remove me\n");
  fs.writeFileSync(path.join(dataRoot, "session.json"), "keep\n");
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
  assert.equal(fs.existsSync(path.join(root, "stale.py")), false);
  assert.equal(fs.readFileSync(path.join(dataRoot, "session.json"), "utf8"), "keep\n");
  assert.equal(result.backup_cleaned, true);
  assert.deepEqual(bootstrapResidues(parent), []);
});

test("same-version bundled core replaces stale code when the package fingerprint changed", async (context) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-same-version-"));
  context.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const root = path.join(parent, "app");
  const archive = path.join(parent, "mindspace-core.zip");
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), "version = \"0.9.1\"\n");
  fs.writeFileSync(path.join(root, "payload.json"), '{"version":"0.9.1"}\n');
  fs.writeFileSync(path.join(root, "scripts", "start.ps1"), "stale\n");
  fs.writeFileSync(path.join(root, ".mindspace-bootstrap.json"), '{"archive_sha256":"old"}\n');
  fs.writeFileSync(archive, "new package bytes");
  const extract = (_source, staging) => {
    const payload = path.join(staging, "payload");
    fs.mkdirSync(path.join(payload, "scripts"), { recursive: true });
    fs.writeFileSync(path.join(payload, "pyproject.toml"), "version = \"0.9.1\"\n");
    fs.writeFileSync(path.join(payload, "payload.json"), '{"version":"0.9.1"}\n');
    fs.writeFileSync(path.join(payload, "scripts", "start.ps1"), "current\n");
  };

  const result = await ensureCoreRoot({ root, archive, version: "0.9.1", extract });
  assert.equal(result.upgraded, true);
  assert.equal(fs.readFileSync(path.join(root, "scripts", "start.ps1"), "utf8"), "current\n");
  const marker = JSON.parse(fs.readFileSync(path.join(root, ".mindspace-bootstrap.json"), "utf8"));
  assert.match(marker.archive_sha256, /^[a-f0-9]{64}$/);
});

test("failed post-switch validation restores the previous Core", async (context) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-rollback-"));
  context.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const root = path.join(parent, "app");
  const archive = path.join(parent, "mindspace-core.zip");
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), "old\n");
  fs.writeFileSync(path.join(root, "payload.json"), '{"version":"0.8.1"}\n');
  fs.writeFileSync(path.join(root, "scripts", "start.ps1"), "old\n");
  fs.writeFileSync(archive, "fixture");
  const extract = (_source, staging) => {
    const payload = path.join(staging, "payload");
    fs.mkdirSync(path.join(payload, "scripts"), { recursive: true });
    fs.writeFileSync(path.join(payload, "pyproject.toml"), "new\n");
    fs.writeFileSync(path.join(payload, "scripts", "start.ps1"), "new\n");
  };
  await assert.rejects(ensureCoreRoot({ root, archive, version: "0.8.2", extract, validate: () => false }), /校验失败/);
  assert.equal(fs.readFileSync(path.join(root, "scripts", "start.ps1"), "utf8"), "old\n");
  assert.deepEqual(bootstrapResidues(parent), []);
});

test("backup cleanup failure blocks startup without attempting a destructive rollback", async (context) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-cleanup-block-"));
  context.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const root = path.join(parent, "app");
  const archive = path.join(parent, "mindspace-core.zip");
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), "old\n");
  fs.writeFileSync(path.join(root, "payload.json"), '{"version":"0.8.1"}\n');
  fs.writeFileSync(path.join(root, "scripts", "start.ps1"), "old\n");
  fs.writeFileSync(path.join(root, "old-only.py"), "old-only\n");
  fs.writeFileSync(archive, "fixture");
  const extract = (_source, staging) => {
    const payload = path.join(staging, "payload");
    fs.mkdirSync(path.join(payload, "scripts"), { recursive: true });
    fs.writeFileSync(path.join(payload, "pyproject.toml"), "new\n");
    fs.writeFileSync(path.join(payload, "payload.json"), '{"version":"0.8.2"}\n');
    fs.writeFileSync(path.join(payload, "scripts", "start.ps1"), "new\n");
  };
  const remove = (target) => {
    if (path.basename(target).startsWith(".mindspace-core-backup-")) throw new Error("injected cleanup denial");
    fs.rmSync(target, { recursive: true, force: true });
  };

  await assert.rejects(
    ensureCoreRoot({ root, archive, version: "0.8.2", extract, remove }),
    /清理旧 Core 备份失败.*启动已阻塞/,
  );

  assert.equal(fs.readFileSync(path.join(root, "scripts", "start.ps1"), "utf8"), "new\n");
  assert.equal(fs.existsSync(path.join(root, "old-only.py")), false);
  const residues = bootstrapResidues(parent);
  assert.equal(residues.length, 1);
  assert.equal(path.basename(residues[0]).startsWith(".mindspace-core-backup-"), true);
  assert.equal(fs.readFileSync(path.join(residues[0], "old-only.py"), "utf8"), "old-only\n");
});

test("an invalid root never deletes an interrupted backup automatically", async (context) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-interrupted-backup-"));
  context.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const root = path.join(parent, "app");
  const backup = path.join(parent, ".mindspace-core-backup-interrupted");
  const archive = path.join(parent, "mindspace-core.zip");
  fs.mkdirSync(path.join(backup, "scripts"), { recursive: true });
  fs.writeFileSync(path.join(backup, "pyproject.toml"), "recoverable\n");
  fs.writeFileSync(path.join(backup, "scripts", "start.ps1"), "recoverable\n");
  fs.writeFileSync(archive, "fixture");

  await assert.rejects(
    ensureCoreRoot({ root, archive, version: "0.8.2", extract: () => {} }),
    /拒绝自动删除.*启动已阻塞/,
  );

  assert.equal(fs.readFileSync(path.join(backup, "scripts", "start.ps1"), "utf8"), "recoverable\n");
  assert.equal(fs.existsSync(root), false);
});

test("upgrade refuses a Core directory that still contains legacy user state", async (context) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-protected-"));
  context.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const root = path.join(parent, "app");
  const archive = path.join(parent, "mindspace-core.zip");
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.mkdirSync(path.join(root, "runtime", "data"), { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), "old\n");
  fs.writeFileSync(path.join(root, "payload.json"), '{"version":"0.8.1"}\n');
  fs.writeFileSync(path.join(root, "scripts", "start.ps1"), "old\n");
  fs.writeFileSync(path.join(root, "runtime", "data", "session.json"), "keep\n");
  fs.writeFileSync(archive, "fixture");
  await assert.rejects(ensureCoreRoot({ root, archive, version: "0.8.2", extract: () => {} }), /must be migrated/);
  assert.equal(fs.readFileSync(path.join(root, "runtime", "data", "session.json"), "utf8"), "keep\n");
});

test("a newer hot-updated Core uses current.json instead of a stale bootstrap payload version", async (context) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-hot-updated-"));
  context.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const root = path.join(parent, "app");
  const archive = path.join(parent, "mindspace-core.zip");
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.mkdirSync(path.join(root, "runtime", "data"), { recursive: true });
  fs.mkdirSync(path.join(root, "runtime", "updates"), { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), "version = \"0.8.4\"\n");
  fs.writeFileSync(path.join(root, "payload.json"), '{"version":"0.8.0"}\n');
  fs.writeFileSync(path.join(root, "runtime", "updates", "current.json"), '{"version":"0.8.4"}\n');
  fs.writeFileSync(path.join(root, "scripts", "start.ps1"), "updated\n");
  fs.writeFileSync(path.join(root, "runtime", "data", "session.json"), "keep\n");
  fs.writeFileSync(archive, "fixture");
  let extracted = false;

  const result = await ensureCoreRoot({
    root,
    archive,
    version: "0.8.3",
    extract: () => { extracted = true; },
  });

  assert.equal(result.upgraded, false);
  assert.equal(extracted, false);
  assert.equal(fs.readFileSync(path.join(root, "runtime", "data", "session.json"), "utf8"), "keep\n");
});

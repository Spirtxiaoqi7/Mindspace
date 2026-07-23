const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { canonical, compareVersions, createUpdateManager, rolloutEligible, safeUpdateUrl, verifyCatalog, verifyManifest } = require("./update-manager.cjs");

function signedManifest(overrides = {}) {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const unsigned = {
    schema_version: "1.0.0",
    channel: "stable",
    version: "0.3.1",
    minimum_launcher: "0.3.0",
    mandatory: false,
    published_at: new Date().toISOString(),
    release_notes: "test",
    package: { url: "http://127.0.0.1:9780/core.zip", sha256: "a".repeat(64), size: 42, format: "zip" },
    ...overrides,
  };
  const signature = crypto.sign(null, Buffer.from(canonical(unsigned)), privateKey).toString("base64");
  return { manifest: { ...unsigned, signature: { algorithm: "ed25519", value: signature } }, publicKey };
}

async function checkCatalogSelection(context, { minimumLauncher = "0.4.0", mandatoryLauncher = false } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-update-priority-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "pyproject.toml"), '[project]\nversion = "0.4.0"\n');
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const unsigned = {
    schema_version: "2.0.0", channel: "stable", release_id: `priority-${minimumLauncher}-${mandatoryLauncher}`, sequence: 51,
    published_at: new Date().toISOString(), rollout: { percentage: 100, salt: "priority" },
    launcher: { version: "0.4.1", feed_url: "https://downloads.example.com/launcher/stable/", mandatory: mandatoryLauncher },
    core: {
      version: "0.4.1", minimum_launcher: minimumLauncher, mandatory: false, release_notes: "priority",
      package: { url: "https://downloads.example.com/core/0.4.1.zip", sha256: "c".repeat(64), size: 100, format: "zip" },
    },
    release_history: [{ version: "0.4.1", published_at: "2026-07-20", title: "Test release", summary: ["signed history"] }],
  };
  const catalog = {
    ...unsigned,
    signature: { algorithm: "ed25519", value: crypto.sign(null, Buffer.from(canonical(unsigned)), privateKey).toString("base64") },
  };
  const publicKeyPath = path.join(root, "public.pem");
  fs.writeFileSync(publicKeyPath, publicKey.export({ type: "spki", format: "pem" }));
  let launcherChecks = 0;
  const launcherUpdater = {
    configure() {},
    async check() { launcherChecks += 1; return this.snapshot(); },
    snapshot() { return { currentVersion: "0.4.0", status: "idle" }; },
  };
  const manager = createUpdateManager({
    app: { getVersion: () => "0.4.0", getPath: () => root },
    rootPath: () => root,
    publicKeyPath,
    deviceId: "priority-test",
    launcherUpdater,
    readConfig: () => ({ updateUrl: "https://updates.example.com/catalog.json", updateChannel: "stable" }),
    writeConfig: () => {},
    fetch: async () => new Response(JSON.stringify(catalog), { status: 200, headers: { "Content-Type": "application/json" } }),
  });
  return { snapshot: await manager.check(), launcherChecks };
}

test("semantic versions are ordered for update decisions", () => {
  assert.equal(compareVersions("0.3.1", "0.3.0"), 1);
  assert.equal(compareVersions("0.3.0-beta.1", "0.3.0"), -1);
  assert.equal(compareVersions("1.0.0", "1.0.0"), 0);
});

test("signed manifests verify and tampering is rejected", () => {
  const { manifest, publicKey } = signedManifest();
  assert.equal(verifyManifest(manifest, publicKey, "stable").version, "0.3.1");
  assert.throws(() => verifyManifest({ ...manifest, version: "9.9.9" }, publicKey, "stable"), /签名/);
});

test("production update feeds require HTTPS", () => {
  assert.match(safeUpdateUrl("https://updates.example.com/manifest.json"), /^https:/);
  assert.match(safeUpdateUrl("http://127.0.0.1:9780/manifest.json"), /^http:/);
  assert.throws(() => safeUpdateUrl("http://updates.example.com/manifest.json"), /HTTPS/);
});

test("signed v2 catalog verifies and prevents tampering", () => {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const unsigned = {
    schema_version: "2.0.0", channel: "stable", release_id: "release-41", sequence: 41,
    published_at: new Date().toISOString(), rollout: { percentage: 20, salt: "r41" },
    launcher: { version: "0.4.1", feed_url: "https://downloads.example.com/launcher/stable/", mandatory: false },
    core: {
      version: "0.4.1", minimum_launcher: "0.4.0", mandatory: false, release_notes: "test",
      package: { url: "https://downloads.example.com/core/0.4.1.zip", sha256: "b".repeat(64), size: 100, format: "zip" },
    },
    release_history: [
      { version: "0.4.1", published_at: "2026-07-20", title: "test", summary: ["signed history"] },
    ],
  };
  const signature = crypto.sign(null, Buffer.from(canonical(unsigned)), privateKey).toString("base64");
  const catalog = { ...unsigned, signature: { algorithm: "ed25519", value: signature } };
  assert.equal(verifyCatalog(catalog, publicKey, "stable").sequence, 41);
  assert.equal(verifyCatalog(catalog, publicKey, "stable").release_history[0].summary[0], "signed history");
  assert.throws(() => verifyCatalog({ ...catalog, sequence: 40 }, publicKey, "stable"), /签名/);
});

test("rollout bucket is deterministic and mandatory boundaries are explicit", () => {
  assert.equal(rolloutEligible("device-a", "r1", { percentage: 0, salt: "x" }), false);
  assert.equal(rolloutEligible("device-a", "r1", { percentage: 100, salt: "x" }), true);
  assert.equal(
    rolloutEligible("device-a", "r1", { percentage: 25, salt: "x" }),
    rolloutEligible("device-a", "r1", { percentage: 25, salt: "x" }),
  );
});

test("an optional Launcher update does not block an available Core update", async (context) => {
  const result = await checkCatalogSelection(context);
  assert.equal(result.snapshot.updateKind, "core");
  assert.equal(result.snapshot.coreAvailable, true);
  assert.equal(result.snapshot.launcherAvailable, true);
  assert.equal(result.launcherChecks, 0);
});

test("Launcher is selected only when Core requires it or it is mandatory", async (context) => {
  const requiredByCore = await checkCatalogSelection(context, { minimumLauncher: "0.4.1" });
  assert.equal(requiredByCore.snapshot.updateKind, "launcher");
  assert.equal(requiredByCore.snapshot.mandatory, true);
  assert.equal(requiredByCore.launcherChecks, 1);

  const mandatory = await checkCatalogSelection(context, { mandatoryLauncher: true });
  assert.equal(mandatory.snapshot.updateKind, "launcher");
  assert.equal(mandatory.snapshot.mandatory, true);
  assert.equal(mandatory.launcherChecks, 1);
});

test("core updater resumes an existing partial download", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mindspace-update-resume-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const downloadRoot = path.join(root, "downloads");
  fs.mkdirSync(downloadRoot, { recursive: true });
  fs.writeFileSync(path.join(root, "pyproject.toml"), '[project]\nversion = "0.4.0"\n');
  const payload = Buffer.from("hello world");
  fs.writeFileSync(path.join(downloadRoot, "mindspace-core-0.4.1.zip.partial"), payload.subarray(0, 6));
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const unsigned = {
    schema_version: "1.0.0", channel: "stable", version: "0.4.1", minimum_launcher: "0.4.0",
    mandatory: false, published_at: new Date().toISOString(), release_notes: "resume",
    package: { url: "http://127.0.0.1/core.zip", sha256: crypto.createHash("sha256").update(payload).digest("hex"), size: payload.length, format: "zip" },
  };
  const manifest = { ...unsigned, signature: { algorithm: "ed25519", value: crypto.sign(null, Buffer.from(canonical(unsigned)), privateKey).toString("base64") } };
  const keyPath = path.join(root, "public.pem");
  fs.writeFileSync(keyPath, publicKey.export({ type: "spki", format: "pem" }));
  const requests = [];
  const manager = createUpdateManager({
    app: { getVersion: () => "0.4.0", getPath: () => root }, rootPath: () => root,
    publicKeyPath: keyPath, downloadRoot, deviceId: "test", readConfig: () => ({ updateUrl: "http://127.0.0.1/manifest.json", updateChannel: "stable" }), writeConfig: () => {},
    fetch: async (url, options = {}) => {
      requests.push({ url: String(url), range: options.headers?.Range || "" });
      return String(url).endsWith("manifest.json")
        ? new Response(JSON.stringify(manifest), { status: 200, headers: { "Content-Type": "application/json" } })
        : new Response(payload.subarray(6), { status: 206 });
    },
  });
  await manager.check();
  await manager.download();
  assert.equal(requests.at(-1).range, "bytes=6-");
  assert.deepEqual(fs.readFileSync(path.join(downloadRoot, "mindspace-core-0.4.1.zip")), payload);
});

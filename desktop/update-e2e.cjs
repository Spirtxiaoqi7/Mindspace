const fs = require("node:fs");
const path = require("node:path");
const { createUpdateManager } = require("./update-manager.cjs");

async function main() {
  const root = process.env.UPDATE_E2E_ROOT;
  const userData = process.env.UPDATE_E2E_USER;
  const project = process.env.UPDATE_E2E_PROJECT;
  const currentVersion = process.env.UPDATE_E2E_CURRENT || "0.3.0";
  const targetVersion = process.env.UPDATE_E2E_TARGET || "0.3.1";
  const launcherVersion = process.env.UPDATE_E2E_LAUNCHER || currentVersion;
  let config = { updateUrl: process.env.UPDATE_E2E_URL || "http://127.0.0.1:9780/manifest.json", updateChannel: "stable" };
  const manager = createUpdateManager({
    app: { getVersion: () => launcherVersion, getPath: () => userData },
    rootPath: () => root,
    resolvePowerShell: () => process.env.MINDSPACE_PWSH_TEST,
    publicKeyPath: path.join(project, "desktop", "assets", "update-public-key.pem"),
    readConfig: () => config,
    writeConfig: (next) => { config = next; },
    stopServicesForUpdate: async () => {},
    startServices: async () => {},
    waitForHealth: async () => true,
  });
  const checked = await manager.check();
  if (checked.status !== "available") throw new Error("update not detected");
  const downloaded = await manager.download();
  if (!downloaded.downloaded) throw new Error("download failed");
  const installed = await manager.install();
  if (installed.currentVersion !== targetVersion) throw new Error(`install version mismatch: ${installed.currentVersion}`);
  const rolled = await manager.rollback();
  if (rolled.currentVersion !== currentVersion) throw new Error(`rollback version mismatch: ${rolled.currentVersion}`);
  const packagePath = path.join(userData, "updates", "downloads", `mindspace-core-${targetVersion}.zip`);
  process.stdout.write(`${JSON.stringify({ check: checked.status, bytes: fs.statSync(packagePath).size, install: installed.status, rollback: rolled.status })}\n`);
}

main().catch((error) => { console.error(error); process.exitCode = 1; });

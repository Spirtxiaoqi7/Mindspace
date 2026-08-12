const fs = require("node:fs");
const path = require("node:path");
const { migrateLegacyLayout, reconcileLegacyModelPaths } = require("./app-paths.cjs");
const { bundledArchive, bundledVersion, ensureCoreRoot, resolveWorkspaceRoot } = require("./bootstrap-core.cjs");
const { cleanupMigratedSource, inspectStorageAlignment, migrateStorage } = require("./storage-location.cjs");

function createStorageController({
  app,
  currentLayout,
  dialog,
  dirname,
  getComponentManager,
  getLauncherSnapshot,
  getRuntimeManager,
  hintedRoot,
  initializeComponentManager,
  readLauncherConfig,
  setQuitting,
  stopServicesForUpdate,
  writeLauncherConfig,
}) {
  let storageMigration = { active: false, progress: 0, message: "", error: "" };
  let modelPathCheck = { checked: false, moved: [], conflicts: [] };
  let workspace = { ready: false, created: false, message: "正在准备用户工作区", error: "" };

  function rootPath() {
    if (app.isPackaged) return currentLayout().core;
    const configuredRoot = String(readLauncherConfig().root || "");
    const configuredDrive = configuredRoot ? path.parse(configuredRoot).root : "";
    const developmentRoot = path.resolve(dirname, "..");
    return resolveWorkspaceRoot({
      app,
      configuredRoot: configuredRoot && fs.existsSync(configuredDrive) ? configuredRoot : "",
      environmentRoot: process.env.MINDSPACE_ROOT || developmentRoot,
      hintedRoot: hintedRoot(),
      dirname,
    });
  }

  function persistRoot(root) {
    if (app.isPackaged) return;
    writeLauncherConfig({ ...readLauncherConfig(), root });
  }

  async function initializeWorkspace(root = rootPath()) {
    try {
      const result = await ensureCoreRoot({
        root,
        archive: bundledArchive(process.resourcesPath, dirname),
        version: bundledVersion(process.resourcesPath, dirname),
      });
      persistRoot(root);
      workspace = { ready: true, created: result.created, message: result.message, error: "" };
    } catch (error) {
      workspace = { ready: false, created: false, message: "用户工作区准备失败", error: String(error.message || error) };
    }
    return workspace;
  }

  async function prepareLegacyLayout() {
    modelPathCheck = reconcileLegacyModelPaths(currentLayout());
    await cleanupMigratedSource(currentLayout());
    const legacyConfig = readLauncherConfig();
    // Packaged installs import legacy data once in ensureAppPaths. Re-running
    // the old runtime-layout merger here would silently overlay AppData after
    // the fixed installation Home has already become authoritative.
    if (!app.isPackaged && process.env.MINDSPACE_SKIP_LEGACY_MIGRATION !== "1") {
      migrateLegacyLayout({
        paths: currentLayout(),
        legacyRoots: [legacyConfig.root, path.join(app.getPath("userData"), "app")],
        version: "0.4.0",
      });
    }
    return modelPathCheck;
  }

  function snapshot(home = currentLayout().home) {
    return {
      workspace,
      storage: {
        ...storageMigration,
        ...inspectStorageAlignment(app, process.env, home),
        modelPathCheck,
      },
    };
  }

  async function migrateToStorageTarget(target) {
    if (storageMigration.active) throw new Error("存储迁移正在进行");
    if (getRuntimeManager()?.snapshot().active || getComponentManager()?.snapshot().active) {
      throw new Error("请先等待或取消当前组件安装，再迁移存储位置");
    }
    storageMigration = { active: true, progress: 0, message: "正在准备跨盘迁移", error: "" };
    try {
      await stopServicesForUpdate();
      const migrated = await migrateStorage({
        app,
        sourceHome: currentLayout().home,
        targetHome: target,
        onProgress: (progress, message) => { storageMigration = { active: true, progress, message, error: "" }; },
      });
      storageMigration = { active: false, progress: 100, message: `已迁移到 ${migrated.target}，正在重启验证`, error: "" };
      setTimeout(() => { setQuitting(true); app.relaunch(); app.exit(0); }, 700);
      return {
        ...(await getLauncherSnapshot()),
        storage: { ...storageMigration, ...inspectStorageAlignment(app, process.env, migrated.target) },
      };
    } catch (error) {
      storageMigration = { active: false, progress: 0, message: "存储迁移失败，原位置保持不变", error: String(error.message || error) };
      throw error;
    }
  }

  function registerIpc(ipcMain) {
    ipcMain.handle("launcher:select-root", async () => {
      if (app.isPackaged) return getLauncherSnapshot();
      const result = await dialog.showOpenDialog({ properties: ["openDirectory"], defaultPath: rootPath() });
      if (!result.canceled && result.filePaths[0]) {
        await initializeWorkspace(result.filePaths[0]);
        if (workspace.ready) initializeComponentManager();
      }
      return getLauncherSnapshot();
    });
    ipcMain.handle("launcher:select-storage", async () => {
      const result = await dialog.showOpenDialog({
        title: "选择 Mindspace 存储位置",
        buttonLabel: "迁移到这里",
        properties: ["openDirectory", "createDirectory"],
        defaultPath: path.dirname(currentLayout().home),
      });
      if (result.canceled || !result.filePaths[0]) return getLauncherSnapshot();
      const selected = path.resolve(result.filePaths[0]);
      const selectedName = path.basename(selected).toLowerCase();
      const target = ["mindspace", "mindspacedata"].includes(selectedName) ? selected : path.join(selected, "MindspaceData");
      return migrateToStorageTarget(target);
    });
    ipcMain.handle("launcher:migrate-recommended-storage", async () => {
      const alignment = inspectStorageAlignment(app, process.env, currentLayout().home);
      if (!alignment.migrationRecommended || !alignment.recommended) return getLauncherSnapshot();
      return migrateToStorageTarget(alignment.recommended);
    });
  }

  return {
    initializeWorkspace,
    migrateToStorageTarget,
    persistRoot,
    prepareLegacyLayout,
    registerIpc,
    rootPath,
    snapshot,
    workspaceSnapshot: () => workspace,
  };
}

module.exports = { createStorageController };

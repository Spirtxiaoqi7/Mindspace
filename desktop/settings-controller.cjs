const { createSettingsSaveCoordinator, enhanceCredentialStatus } = require("./secret-store.cjs");

function createSettingsController({ fetch, coreOrigin, secretStore, isAuthorizedSender }) {
  async function coreRequest(method, patch) {
    const response = await fetch(`${coreOrigin}/api/v1/settings`, {
      method,
      ...(patch === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(String(payload.detail || payload.error || `Core 设置请求失败（HTTP ${response.status}）`));
      error.status = response.status;
      throw error;
    }
    return payload;
  }
  const coordinator = createSettingsSaveCoordinator({ secretStore, patchCore: (patch) => coreRequest("PATCH", patch) });
  async function save(payload) {
    const result = await coordinator.save(payload);
    if (result.ok) result.payload = enhanceCredentialStatus(result.payload, secretStore);
    return result;
  }
  async function get() {
    return enhanceCredentialStatus(await coreRequest("GET"), secretStore);
  }
  function registerIpc(ipcMain) {
    ipcMain.handle("launcher:settings-save", async (event, payload = {}) => {
      if (!isAuthorizedSender(event.sender)) return { ok: false, status: 403, phase: "authorization", core_applied: false, secret_persisted: false, error: "仅 Mindspace 产品窗口可以保存设置" };
      return save(payload);
    });
    ipcMain.handle("launcher:settings-get", async (event) => {
      if (!isAuthorizedSender(event.sender)) return { ok: false, status: 403, error: "仅 Mindspace 产品窗口可以读取桌面设置状态" };
      try { return { ok: true, status: 200, payload: await get() }; }
      catch (error) { return { ok: false, status: Number(error.status || 502), error: String(error.message || error) }; }
    });
  }
  return { get, registerIpc, save };
}

module.exports = { createSettingsController };

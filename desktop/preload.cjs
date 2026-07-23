const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("launcher", {
  snapshot: () => ipcRenderer.invoke("launcher:snapshot"),
  action: (service, action) => ipcRenderer.invoke("launcher:service", { service, action }),
  all: (action) => ipcRenderer.invoke("launcher:all", action),
  open: (kind) => ipcRenderer.invoke("launcher:open", kind),
  external: (url) => ipcRenderer.invoke("launcher:external", url),
  maintenance: (action) => ipcRenderer.invoke("launcher:maintenance", action),
  selectRoot: () => ipcRenderer.invoke("launcher:select-root"),
  selectStorage: () => ipcRenderer.invoke("launcher:select-storage"),
  shortcut: () => ipcRenderer.invoke("launcher:shortcut"),
  update: (action, options = {}) => ipcRenderer.invoke("launcher:update", { action, ...options }),
  component: (action, id = "") => ipcRenderer.invoke("launcher:component", { action, id }),
  voice: (action, id = "") => ipcRenderer.invoke("launcher:voice", { action, id }),
  diagnostics: () => ipcRenderer.invoke("runtime:diagnostics"),
  runtime: (action, id = "") => {
    const channel = {
      snapshot: "runtime:snapshot",
      install: "runtime:install",
      cancel: "runtime:cancel",
      retry: "runtime:retry",
      repair: "runtime:repair",
    }[action];
    return ipcRenderer.invoke(channel || "runtime:action", { action, id });
  },
  source: (source = "china") => ipcRenderer.invoke("runtime:source", { source }),
  proxy: (proxy = "") => ipcRenderer.invoke("runtime:proxy", { proxy }),
});

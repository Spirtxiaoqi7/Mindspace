const fs = require("node:fs");
const path = require("node:path");

function redactDiagnosticText(value) {
  return String(value || "")
    .replace(/(authorization["'\s:=]+bearer\s+)[^\s"']+/gi, "$1[REDACTED]")
    .replace(/((?:api[_-]?key|token|password|secret)["'\s:=]+)[^\s,"']+/gi, "$1[REDACTED]")
    .replace(/(https?:\/\/)[^\s/@:]+:[^\s/@]+@/gi, "$1[REDACTED]@");
}

function createDiagnosticsController({ app, currentLayout, downloadSource, logRoot, runtimeSnapshot, writeJsonAtomic }) {
  function tailLog(file, maximumLines = 240) {
    try {
      return redactDiagnosticText(fs.readFileSync(file, "utf8").split(/\r?\n/).slice(-maximumLines).join("\n"));
    } catch {
      return "";
    }
  }

  function createReport() {
    const generatedAt = new Date();
    const logs = logRoot();
    const folder = path.join(logs, "diagnostics", `mindspace-${generatedAt.toISOString().replace(/[:.]/g, "-")}`);
    fs.mkdirSync(folder, { recursive: true });
    const runtime = runtimeSnapshot();
    const report = {
      schema_version: "1.0.0",
      generated_at: generatedAt.toISOString(),
      launcher_version: app.getVersion(),
      packaged: app.isPackaged,
      platform: { platform: process.platform, arch: process.arch, release: runtime.system?.windowsRelease || "" },
      storage: { home: currentLayout().home, free_bytes: runtime.system?.freeBytes || 0, writable: runtime.system?.writable !== false },
      download_source: downloadSource(),
      runtime,
    };
    writeJsonAtomic(path.join(folder, "diagnostic-report.json"), report);
    const diagnosticLogs = new Set([
      "runtime-manager.jsonl",
      "component-download.log",
      "maintenance-verify.log",
      "api.launcher.log",
      "asr.launcher.log",
      "tts.launcher.log",
    ]);
    try {
      for (const entry of fs.readdirSync(logs, { withFileTypes: true })) {
        if (entry.isFile() && /^[a-z0-9._-]+\.install\.log$/i.test(entry.name)) diagnosticLogs.add(entry.name);
      }
    } catch {
      // A fresh install legitimately has no log directory yet.
    }
    for (const name of diagnosticLogs) {
      const content = tailLog(path.join(logs, name));
      if (content) fs.writeFileSync(path.join(folder, name), `${content}\n`, "utf8");
    }
    return folder;
  }

  return { createReport };
}

module.exports = { createDiagnosticsController };

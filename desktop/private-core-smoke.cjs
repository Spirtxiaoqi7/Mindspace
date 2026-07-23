const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const project = path.resolve(__dirname, "..");
const home = path.resolve(process.argv[2]);
const port = Number(process.argv[3] || 8875);
const pwsh = path.join(home, "environment", "tools", "powershell", "7.6.3", "pwsh.exe");
const uv = path.join(home, "environment", "tools", "uv", "0.11.26", "uv.exe");
const python = path.join(home, "environment", "venvs", "core", "0.4.0", "Scripts", "python.exe");
const logs = path.join(home, "logs");
fs.mkdirSync(logs, { recursive: true });
const output = fs.openSync(path.join(logs, "private-core-smoke.log"), "a");
const environment = {
  ...process.env,
  PATH: [path.dirname(pwsh), path.dirname(uv), path.join(process.env.SystemRoot || "C:\\Windows", "System32")].join(path.delimiter),
  MINDSPACE_HOME: home,
  MINDSPACE_ENVIRONMENT: path.join(home, "environment"),
  MINDSPACE_MODEL_ROOT: path.join(home, "models"),
  MINDSPACE_DATA_ROOT: path.join(home, "data"),
  MINDSPACE_RUNTIME_DIR: path.join(home, "data"),
  MINDSPACE_CORE_PYTHON: python,
  MINDSPACE_PWSH: pwsh,
  MINDSPACE_UV: uv,
  MINDSPACE_PORT: String(port),
};
const child = spawn(pwsh, ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path.join(project, "scripts", "start.ps1")], {
  cwd: project, env: environment, windowsHide: true, stdio: ["ignore", output, output],
});

(async () => {
  try {
    let health;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const response = await fetch(`http://127.0.0.1:${port}/api/v1/health`);
        if (response.ok) { health = await response.json(); break; }
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (!health) throw new Error(fs.readFileSync(path.join(logs, "private-core-smoke.log"), "utf8"));
    const pip = spawnSync(python, ["-m", "pip", "--version"], { encoding: "utf8", env: environment, windowsHide: true });
    if (pip.status !== 0) throw new Error(pip.stderr || "private pip failed");
    process.stdout.write(`${JSON.stringify({ health, launcher: pwsh, python, pip: pip.stdout.trim(), path: environment.PATH })}\n`);
  } finally {
    spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
    fs.closeSync(output);
  }
})().catch((error) => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; });

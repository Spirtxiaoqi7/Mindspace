import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = path.resolve(import.meta.dirname, "..");
const pyprojectPath = path.join(root, "pyproject.toml");
const pyproject = fs.readFileSync(pyprojectPath, "utf8");
const match = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
if (!match) throw new Error("pyproject.toml does not contain project.version");
const version = match[1];

for (const relative of ["frontend/package.json", "desktop/package.json"]) {
  const target = path.join(root, relative);
  const value = JSON.parse(fs.readFileSync(target, "utf8"));
  value.version = version;
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

const desktopLockPath = path.join(root, "desktop/package-lock.json");
const desktopLock = JSON.parse(fs.readFileSync(desktopLockPath, "utf8"));
desktopLock.version = version;
if (desktopLock.packages?.[""]) desktopLock.packages[""].version = version;
fs.writeFileSync(desktopLockPath, `${JSON.stringify(desktopLock, null, 2)}\n`, "utf8");

const frontendLockPath = path.join(root, "frontend/package-lock.json");
const frontendLock = JSON.parse(fs.readFileSync(frontendLockPath, "utf8"));
frontendLock.version = version;
if (frontendLock.packages?.[""]) frontendLock.packages[""].version = version;
fs.writeFileSync(frontendLockPath, `${JSON.stringify(frontendLock, null, 2)}\n`, "utf8");

fs.writeFileSync(
  path.join(root, "src/mindspace_graph/version.py"),
  `"""Build version synchronized from the project release source."""\n\nAPP_VERSION = "${version}"\n`,
  "utf8",
);
process.stdout.write(`${version}\n`);

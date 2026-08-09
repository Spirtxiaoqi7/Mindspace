const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const desktopRoot = __dirname;
const modelFile = path.join(
  desktopRoot,
  "companion",
  "public",
  "Resources",
  "mindspace-companion-v24",
  "mindspace-companion-v24.model3.json",
);

test("deferred companion release does not claim missing Live2D resources", () => {
  const mainProcess = fs.readFileSync(path.join(desktopRoot, "main.cjs"), "utf8");

  assert.equal(fs.existsSync(modelFile), false);
  assert.match(mainProcess, /available:\s*false/);
  assert.match(mainProcess, /if \(action !== "snapshot"\)/);
  assert.match(mainProcess, /COMPANION_RELEASE\.message/);
});

test("desktop package excludes the deferred companion runtime", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(desktopRoot, "package.json"), "utf8"));
  const files = manifest.build.files;

  assert.ok(files.includes("!assets/companion-renderer/**/*"));
  assert.ok(files.includes("!assets/live2d/**/*"));
  assert.equal(fs.existsSync(path.join(desktopRoot, "companion", "index.html")), false);
});

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const modelRoot = path.join(__dirname, "companion", "public", "Resources", "mindspace-companion-v24");

test("companion runtime contains only referenced export files", () => {
  const modelFile = path.join(modelRoot, "mindspace-companion-v24.model3.json");
  const model = JSON.parse(fs.readFileSync(modelFile, "utf8"));
  const references = [
    model.FileReferences.Moc,
    model.FileReferences.Physics,
    model.FileReferences.DisplayInfo,
    ...model.FileReferences.Textures,
  ];
  for (const reference of references) {
    assert.ok(fs.existsSync(path.join(modelRoot, reference)), `missing companion resource: ${reference}`);
  }
  const sourceFiles = fs.readdirSync(modelRoot, { recursive: true }).filter((name) => /\.cmo3$/i.test(String(name)));
  assert.deepEqual(sourceFiles, [], "editable Cubism sources must not enter the runtime package");
});

test("companion declares official blink and lip-sync parameter groups", () => {
  const model = JSON.parse(fs.readFileSync(path.join(modelRoot, "mindspace-companion-v24.model3.json"), "utf8"));
  const groups = Object.fromEntries(model.Groups.map((group) => [group.Name, group.Ids]));
  assert.deepEqual(groups.EyeBlink, ["ParamEyeLOpen", "ParamEyeROpen"]);
  assert.deepEqual(groups.LipSync, ["ParamMouthOpenY"]);
});

test("companion is draggable and does not hide the launcher", () => {
  const companionPage = fs.readFileSync(path.join(__dirname, "companion", "index.html"), "utf8");
  const mainProcess = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  assert.match(companionPage, /-webkit-app-region:\s*drag/);
  assert.match(companionPage, /canvas[^}]*pointer-events:\s*none/s);
  const showAction = mainProcess.match(/else if \(action === "show"\) \{([\s\S]*?)\n\s*\} else if \(action === "reset-position"\)/)?.[1] || "";
  assert.doesNotMatch(showAction, /launcherWindow\?\.hide/);
});

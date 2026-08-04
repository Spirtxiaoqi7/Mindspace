const assert = require("node:assert/strict");
const test = require("node:test");
const { normalizeCompanionConfig, companionBoundsForDisplay } = require("./companion-policy.cjs");

test("companion defaults to enabled with bounded size", () => {
  assert.deepEqual(normalizeCompanionConfig(), {
    enabled: true, clickThrough: false, behaviorVersion: 2, width: 336, height: 576, x: null, y: null,
  });
});

test("legacy click-through is reset once so the companion remains draggable", () => {
  assert.equal(normalizeCompanionConfig({ clickThrough: true }).clickThrough, false);
  assert.equal(normalizeCompanionConfig({ behaviorVersion: 2, clickThrough: true }).clickThrough, true);
});

test("companion bounds stay inside the selected work area", () => {
  assert.deepEqual(
    companionBoundsForDisplay({ x: 9999, y: -9999, width: 900, height: 200 }, { x: 100, y: 50, width: 1920, height: 1040 }),
    { x: 1300, y: 50, width: 720, height: 360 },
  );
});

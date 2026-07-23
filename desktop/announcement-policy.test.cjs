const assert = require("node:assert/strict");
const test = require("node:test");
const { shouldAutoOpenAnnouncement } = require("./announcement-policy.cjs");

test("announcement opens once per launcher process only for a newer release", () => {
  const update = { updateKind: "core", coreAvailable: true, launcherAvailable: false, releaseId: "release-7", status: "available" };
  assert.equal(shouldAutoOpenAnnouncement(update, ""), true);
  assert.equal(shouldAutoOpenAnnouncement(update, "release-7"), false);
  assert.equal(shouldAutoOpenAnnouncement({ ...update, updateKind: "none", coreAvailable: false, status: "current" }, ""), false);
});

test("announcement remains eligible while a new update downloads or waits to install", () => {
  for (const status of ["downloading", "verifying", "downloaded", "paused"]) {
    assert.equal(shouldAutoOpenAnnouncement({ updateKind: "core", coreAvailable: true, launcherAvailable: false, releaseId: "release-7", status }, ""), true);
  }
});

const assert = require("node:assert/strict");
const test = require("node:test");
const { EventEmitter } = require("node:events");

const { createLauncherUpdater } = require("./launcher-updater.cjs");

class FakeUpdater extends EventEmitter {
  setFeedURL(value) { this.feed = value; }
  async checkForUpdates() { this.emit("update-available", { version: "0.4.1" }); }
  async downloadUpdate() {
    this.emit("download-progress", { percent: 50, transferred: 5, total: 10, bytesPerSecond: 2 });
    this.emit("update-downloaded", { version: "0.4.1" });
  }
  quitAndInstall(...arguments_) { this.installed = true; this.installArguments = arguments_; }
}

test("launcher updater exposes check, progress and install state", async () => {
  const fake = new FakeUpdater();
  const manager = createLauncherUpdater({ updater: fake, currentVersion: () => "0.4.0" });
  manager.configure("https://downloads.example.com/stable/", true);
  await manager.check();
  assert.equal(manager.snapshot().latestVersion, "0.4.1");
  await manager.download();
  assert.equal(manager.snapshot().downloaded, true);
  manager.install();
  assert.equal(fake.installed, true);
  assert.deepEqual(fake.installArguments, [true, true]);
});

test("launcher updater refuses insecure public feeds", () => {
  const manager = createLauncherUpdater({ updater: new FakeUpdater(), currentVersion: () => "0.4.0" });
  assert.throws(() => manager.configure("http://updates.example.com/stable/"), /HTTPS/);
});

test("launcher updater retries transient content-length failures", async () => {
  class FlakyUpdater extends FakeUpdater {
    attempts = 0;
    async downloadUpdate() {
      this.attempts += 1;
      if (this.attempts < 3) throw new Error("net::ERR_CONTENT_LENGTH_MISMATCH");
      this.emit("update-downloaded", { version: "0.4.1" });
    }
  }
  const fake = new FlakyUpdater();
  const manager = createLauncherUpdater({ updater: fake, currentVersion: () => "0.4.0", retryDelayMs: 0 });
  await manager.download();
  assert.equal(fake.attempts, 3);
  assert.equal(manager.snapshot().downloaded, true);
});

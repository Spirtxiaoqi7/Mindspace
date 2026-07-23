function shouldAutoOpenAnnouncement(update, shownRelease = "") {
  if (!update || update.updateKind === "none") return false;
  if (!update.coreAvailable && !update.launcherAvailable) return false;
  if (!update.releaseId || update.releaseId === shownRelease) return false;
  return ["available", "downloading", "verifying", "downloaded", "paused"].includes(update.status);
}

module.exports = { shouldAutoOpenAnnouncement };

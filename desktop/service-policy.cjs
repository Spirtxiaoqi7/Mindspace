const SERVICE_START_ORDER = Object.freeze(["api", "asr", "tts"]);
const SERVICE_RESTART_DELAYS_MS = Object.freeze([1000, 2500, 5000]);

function isFatalStartFailure(service) {
  return service === "api";
}

function isStaleCore(detail, expectedVersion) {
  return Boolean(detail?.version && expectedVersion && detail.version !== expectedVersion);
}

function shouldWaitForAsrBeforeLocalTts(ttsProvider, asrStarted, asrReport) {
  const localTts = ["cosyvoice", "gpt-sovits"].includes(String(ttsProvider || "").toLowerCase());
  if (!localTts) return false;
  return Boolean(asrStarted || (asrReport?.online && asrReport?.detail?.ready !== true));
}

function serviceRestartDelay(failureCount) {
  const index = Math.max(0, Number(failureCount || 0) - 1);
  return SERVICE_RESTART_DELAYS_MS[index] ?? null;
}

module.exports = {
  SERVICE_START_ORDER,
  isFatalStartFailure,
  isStaleCore,
  serviceRestartDelay,
  shouldWaitForAsrBeforeLocalTts,
};

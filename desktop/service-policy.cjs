const SERVICE_START_ORDER = Object.freeze(["api", "asr", "tts"]);

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

module.exports = {
  SERVICE_START_ORDER,
  isFatalStartFailure,
  isStaleCore,
  shouldWaitForAsrBeforeLocalTts,
};

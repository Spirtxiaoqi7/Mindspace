const SERVICE_START_ORDER = Object.freeze(["api", "asr", "tts"]);
const SERVICE_RESTART_DELAYS_MS = Object.freeze([1000, 2500, 5000]);
const TEXT_ONLY_NOTICE = "本次启动未启用语音功能（VAD/ASR 未启动），可以正常进行文字对话。";

function isFatalStartFailure(service) {
  return service === "api";
}

function isStaleCore(detail, expectedVersion) {
  return Boolean(detail?.version && expectedVersion && detail.version !== expectedVersion);
}

function shouldWaitForAsrBeforeLocalTts(ttsProvider, asrStarted, asrReport) {
  // TTS and ASR have separate process, device and cancellation domains.
  // Waiting for ASR used to make a warm local engine appear unavailable on
  // first launch; Qwen3-vLLM must compile and warm up independently.
  void ttsProvider;
  void asrStarted;
  void asrReport;
  return false;
}

function serviceRestartDelay(failureCount) {
  const index = Math.max(0, Number(failureCount || 0) - 1);
  return SERVICE_RESTART_DELAYS_MS[index] ?? null;
}

function productEntryState({ coreOnline = false, asrOnline = false } = {}) {
  return {
    canEnter: Boolean(coreOnline),
    mode: asrOnline ? "voice-capable" : "text-only",
    notice: asrOnline ? "" : TEXT_ONLY_NOTICE,
  };
}

module.exports = {
  SERVICE_START_ORDER,
  TEXT_ONLY_NOTICE,
  isFatalStartFailure,
  isStaleCore,
  productEntryState,
  serviceRestartDelay,
  shouldWaitForAsrBeforeLocalTts,
};

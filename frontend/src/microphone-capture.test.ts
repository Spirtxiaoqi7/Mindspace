import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(resolve(__dirname, "./App.tsx"), "utf8");
const workletSource = readFileSync(resolve(__dirname, "../public/pcm-worklet.js"), "utf8");

describe("microphone capture", () => {
  it("keeps microphone capture independent from TTS playback", () => {
    expect(appSource).toContain("const captureAudioContext = useCallback");
    expect(appSource).toContain('context.audioWorklet.addModule("/assets/pcm-worklet.js")');
    expect(appSource).toContain("source.connect(worklet)");
    expect(appSource).toContain("worklet.connect(monitor)");
    expect(appSource).toContain("captureContextRef.current = context");
    expect(appSource).not.toContain("voiceCapturePrimeRef");
    expect(appSource).not.toContain("sharedAudioContext");
    expect(appSource).not.toContain("MediaStreamTrackProcessor");
    expect(appSource).not.toContain("createScriptProcessor(");
  });

  it("does not close the shared capture context from a stale start attempt", () => {
    expect(appSource).toContain("voiceStartInFlightGenerationRef");
    expect(appSource).toContain("context !== captureContextRef.current");
    expect(appSource).not.toContain("context !== playbackContextRef.current");
  });

  it("keeps ASR open while generation and TTS preparation are pending", () => {
    expect(appSource).not.toContain('setVoiceInputLocked(true, "turn_committed")');
    expect(appSource).not.toContain('setVoiceInputLocked(true, "response_replaced")');
    expect(appSource).not.toContain('setVoiceInputLocked(false, "tts_started")');
    expect(appSource).not.toContain("await playbackContext();\n      void startListening()");
    expect(appSource).toContain("TTS is a downstream consumer");
  });

  it("parks a disabled live track for a bounded rapid-reopen window", () => {
    expect(appSource).toContain("VOICE_CAPTURE_WARM_GRACE_MS = 15_000");
    expect(appSource).toContain("track.enabled = false");
    expect(appSource).toContain("track.enabled = true");
    expect(appSource).toContain("stopListening(false, true)");
  });

  it("forwards every PCM frame without a browser-side speech gate", () => {
    expect(workletSource).toContain('registerProcessor("mindspace-pcm"');
    expect(workletSource).toContain("this.port.postMessage");
    expect(workletSource).not.toContain("noiseGate");
    expect(workletSource).not.toContain("calibration");
    expect(workletSource).not.toContain("gateOpen");
  });

  it("selects a physical microphone without optional Chromium DSP negotiation", () => {
    expect(appSource).toContain("VOICE_INPUT_DEVICE_STORAGE_KEY");
    expect(appSource).toContain('device.deviceId !== "default"');
    expect(appSource).toContain("deviceId: { exact: selected.deviceId }");
    expect(appSource).toContain("VOICE_CAPTURE_FALLBACK_CONSTRAINTS: MediaStreamConstraints = { audio: true }");
    expect(appSource).not.toContain('deviceId: { ideal: "default" }');
    expect(appSource).not.toContain("noiseSuppression: { ideal: false }");
    expect(appSource).not.toContain("echoCancellation: { ideal: true }");
    expect(appSource).not.toContain("autoGainControl: { ideal: true }");
    expect(appSource).not.toContain("noise_floor_db:");
  });

  it("acquires the microphone before opening the WebAudio graph", () => {
    const captureStart = appSource.indexOf(
      "const activeStream = await Promise.race([",
    );
    const contextStart = appSource.indexOf("const context = await captureAudioContext()", captureStart);
    expect(captureStart).toBeGreaterThan(0);
    expect(contextStart).toBeGreaterThan(captureStart);
    expect(appSource).not.toContain("Promise.all([\n        captureAudioContext()");
  });

  it("does not connect ASR until a live microphone graph exists", () => {
    const captureStart = appSource.indexOf(
      "({ stream, context, source, worklet, monitor: silentMonitor } = await createVoiceCaptureGraph())",
    );
    const socketStart = appSource.indexOf(
      "socket = new WebSocket(`${protocol}://${location.host}/api/v1/audio/asr/stream`)",
      captureStart,
    );
    expect(captureStart).toBeGreaterThan(0);
    expect(socketStart).toBeGreaterThan(captureStart);
    expect(appSource).toContain("if (microphoneRequestRef.current) return microphoneRequestRef.current");
  });

  it("keeps Chromium capture as an explicit fallback without reloading the page", () => {
    expect(appSource).toContain('error.name = "VoiceCaptureTimeoutError"');
    expect(appSource).toContain("voiceFallbackCaptureRef.current");
    expect(appSource).toContain("切换备用采集");
    expect(appSource).not.toContain("window.location.reload()");
  });
});

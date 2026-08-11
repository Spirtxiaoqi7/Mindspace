import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const voiceRuntimeSource = readFileSync(
  resolve(__dirname, "./features/voice/useVoiceSessionRuntime.ts"),
  "utf8",
);
const workletSource = readFileSync(resolve(__dirname, "../public/pcm-worklet.js"), "utf8");

describe("microphone capture", () => {
  it("keeps microphone capture independent from TTS playback", () => {
    expect(voiceRuntimeSource).toContain("const captureAudioContext = useCallback");
    expect(voiceRuntimeSource).toContain('context.audioWorklet.addModule("/assets/pcm-worklet.js")');
    expect(voiceRuntimeSource).toContain("source.connect(worklet)");
    expect(voiceRuntimeSource).toContain("worklet.connect(monitor)");
    expect(voiceRuntimeSource).toContain("captureContextRef.current = context");
    expect(voiceRuntimeSource).not.toContain("voiceCapturePrimeRef");
    expect(voiceRuntimeSource).not.toContain("sharedAudioContext");
    expect(voiceRuntimeSource).not.toContain("MediaStreamTrackProcessor");
    expect(voiceRuntimeSource).not.toContain("createScriptProcessor(");
  });

  it("does not close the shared capture context from a stale start attempt", () => {
    expect(voiceRuntimeSource).toContain("voiceStartInFlightGenerationRef");
    expect(voiceRuntimeSource).toContain("context !== captureContextRef.current");
    expect(voiceRuntimeSource).not.toContain("context !== playbackContextRef.current");
  });

  it("keeps ASR open while generation and TTS preparation are pending", () => {
    expect(voiceRuntimeSource).not.toContain('setVoiceInputLocked(true, "turn_committed")');
    expect(voiceRuntimeSource).not.toContain('setVoiceInputLocked(true, "response_replaced")');
    expect(voiceRuntimeSource).not.toContain('setVoiceInputLocked(false, "tts_started")');
    expect(voiceRuntimeSource).not.toContain("await playbackContext();\n      void startListening()");
  });

  it("parks a disabled live track for a bounded rapid-reopen window", () => {
    expect(voiceRuntimeSource).toContain("VOICE_CAPTURE_WARM_GRACE_MS = 15_000");
    expect(voiceRuntimeSource).toContain("track.enabled = false");
    expect(voiceRuntimeSource).toContain("track.enabled = true");
    expect(voiceRuntimeSource).toContain("stopListening(false, true)");
  });

  it("forwards every PCM frame without a browser-side speech gate", () => {
    expect(workletSource).toContain('registerProcessor("mindspace-pcm"');
    expect(workletSource).toContain("this.port.postMessage");
    expect(workletSource).not.toContain("noiseGate");
    expect(workletSource).not.toContain("calibration");
    expect(workletSource).not.toContain("gateOpen");
  });

  it("selects a physical microphone without optional Chromium DSP negotiation", () => {
    expect(voiceRuntimeSource).toContain("VOICE_INPUT_DEVICE_STORAGE_KEY");
    expect(voiceRuntimeSource).toContain('device.deviceId !== "default"');
    expect(voiceRuntimeSource).toContain("deviceId: { exact: selected.deviceId }");
    expect(voiceRuntimeSource).toContain("VOICE_CAPTURE_FALLBACK_CONSTRAINTS: MediaStreamConstraints = { audio: true }");
    expect(voiceRuntimeSource).not.toContain('deviceId: { ideal: "default" }');
    expect(voiceRuntimeSource).not.toContain("noiseSuppression: { ideal: false }");
    expect(voiceRuntimeSource).not.toContain("echoCancellation: { ideal: true }");
    expect(voiceRuntimeSource).not.toContain("autoGainControl: { ideal: true }");
    expect(voiceRuntimeSource).not.toContain("noise_floor_db:");
  });

  it("acquires the microphone before opening the WebAudio graph", () => {
    const captureStart = voiceRuntimeSource.indexOf(
      "const activeStream = await Promise.race([",
    );
    const contextStart = voiceRuntimeSource.indexOf("const context = await captureAudioContext()", captureStart);
    expect(captureStart).toBeGreaterThan(0);
    expect(contextStart).toBeGreaterThan(captureStart);
    expect(voiceRuntimeSource).not.toContain("Promise.all([\n        captureAudioContext()");
  });

  it("does not connect ASR until a live microphone graph exists", () => {
    const captureStart = voiceRuntimeSource.indexOf(
      "({ stream, context, source, worklet, monitor: silentMonitor } = await createVoiceCaptureGraph())",
    );
    const socketStart = voiceRuntimeSource.indexOf(
      "socket = new WebSocket(`${protocol}://${location.host}/api/v1/audio/asr/stream`)",
      captureStart,
    );
    expect(captureStart).toBeGreaterThan(0);
    expect(socketStart).toBeGreaterThan(captureStart);
    expect(voiceRuntimeSource).toContain("if (microphoneRequestRef.current) return microphoneRequestRef.current");
  });

  it("keeps Chromium capture as an explicit fallback without reloading the page", () => {
    expect(voiceRuntimeSource).toContain('error.name = "VoiceCaptureTimeoutError"');
    expect(voiceRuntimeSource).toContain("voiceFallbackCaptureRef.current");
    expect(voiceRuntimeSource).toContain("切换备用采集");
    expect(voiceRuntimeSource).not.toContain("window.location.reload()");
  });
});

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";

import { request } from "../../shared/api";
import { asRecord, bool, num, str } from "../../shared/formatters";
import type { TurnSend } from "../../shared/turn";
import type {
  InitiativeTrigger,
  Message,
  ProductSettings,
  VoiceInteractionContext,
  VoiceInteractionMode,
  VoiceSessionState,
} from "../../types";
import type { VoiceCaptureGraph, WarmVoiceCapture } from "./types";
import type { useTtsRuntime } from "./useTtsRuntime";

const VOICE_INPUT_DEVICE_STORAGE_KEY = "mindspace.voice_input_device";
const VOICE_CAPTURE_RECOVERY_STORAGE_KEY = "mindspace.voice_capture_recovery";
const REQUEST_TIMEOUT_MS = 10_000;
const VOICE_ENTRY_PERSIST_TIMEOUT_MS = 5_000;
const VOICE_RECONNECT_DELAYS_MS = [250, 750, 1500, 3000] as const;
const VOICE_TRANSPORT_READY_TIMEOUT_MS = 3_000;
const VOICE_NATIVE_CAPTURE_STATUS_TIMEOUT_MS = 2_000;
const VOICE_DEVICE_ENUMERATION_TIMEOUT_MS = 800;
const VOICE_DEVICE_REQUEST_TIMEOUT_MS = 4_000;
const VOICE_CAPTURE_READY_TIMEOUT_MS = 3_000;
const VOICE_CAPTURE_STALL_TIMEOUT_MS = 10_000;
const VOICE_PENDING_PCM_FRAMES = 12;
const VOICE_CAPTURE_WARM_GRACE_MS = 15_000;
const VOICE_CAPTURE_FALLBACK_CONSTRAINTS: MediaStreamConstraints = { audio: true };
interface VoiceCaptureRecovery {
  context: VoiceInteractionContext;
  attempts: number;
  expires_at: number;
}

function readVoiceCaptureRecovery(): VoiceCaptureRecovery | null {
  try {
    const value = JSON.parse(
      sessionStorage.getItem(VOICE_CAPTURE_RECOVERY_STORAGE_KEY) || "null",
    ) as VoiceCaptureRecovery | null;
    if (!value || value.expires_at <= Date.now()) {
      sessionStorage.removeItem(VOICE_CAPTURE_RECOVERY_STORAGE_KEY);
      return null;
    }
    return value;
  } catch {
    sessionStorage.removeItem(VOICE_CAPTURE_RECOVERY_STORAGE_KEY);
    return null;
  }
}

function writeVoiceCaptureRecovery(context: VoiceInteractionContext, attempts: number) {
  sessionStorage.setItem(VOICE_CAPTURE_RECOVERY_STORAGE_KEY, JSON.stringify({
    context,
    attempts,
    // Keep a failed-attempt marker long enough that duplicate React callbacks
    // or a quick manual reopen cannot turn a bounded recovery back into a
    // reload loop. A real live track clears it immediately.
    expires_at: Date.now() + 5 * 60_000,
  } satisfies VoiceCaptureRecovery));
}

function clearVoiceCaptureRecovery() {
  sessionStorage.removeItem(VOICE_CAPTURE_RECOVERY_STORAGE_KEY);
}


async function preferredVoiceCaptureConstraints(): Promise<MediaStreamConstraints> {
  const cached = localStorage.getItem(VOICE_INPUT_DEVICE_STORAGE_KEY) || "";
  let devices: MediaDeviceInfo[] = [];
  try {
    devices = await Promise.race([
      navigator.mediaDevices.enumerateDevices(),
      new Promise<MediaDeviceInfo[]>((resolve) => {
        window.setTimeout(() => resolve([]), VOICE_DEVICE_ENUMERATION_TIMEOUT_MS);
      }),
    ]);
  } catch {
    // Device enumeration is an optimization. The basic audio request remains
    // available when a driver is still publishing its endpoint list.
  }
  const physicalInputs = devices.filter((device) => (
    device.kind === "audioinput"
    && device.deviceId
    && device.deviceId !== "default"
    && device.deviceId !== "communications"
  ));
  const selected = physicalInputs.find((device) => device.deviceId === cached)
    || physicalInputs[0];
  if (!selected) return VOICE_CAPTURE_FALLBACK_CONSTRAINTS;
  localStorage.setItem(VOICE_INPUT_DEVICE_STORAGE_KEY, selected.deviceId);
  return { audio: { deviceId: { exact: selected.deviceId } } };
}


async function requestWithTimeout<T>(
  url: string,
  init: RequestInit = {},
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const callerSignal = init.signal;
  const cancelFromCaller = () => controller.abort("cancelled");
  if (callerSignal?.aborted) cancelFromCaller();
  else callerSignal?.addEventListener("abort", cancelFromCaller, { once: true });
  const timeout = window.setTimeout(() => controller.abort("timeout"), timeoutMs);
  try {
    return await request<T>(url, { ...init, signal: controller.signal });
  } catch (error) {
    if ((error as Error).name === "AbortError" || controller.signal.aborted) {
      if (callerSignal?.aborted) throw new DOMException("Cancelled", "AbortError");
      throw new Error("语音服务响应超时，请重试或关闭语音入口");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", cancelFromCaller);
  }
}


function savedVoiceInteraction(settings: ProductSettings | null): VoiceInteractionContext {
  const interaction = settings?.interaction;
  const configuredMode = str(interaction?.voice_entry_mode);
  return {
    mode: configuredMode === "face_to_face" ? "face_to_face" : "call",
    scene: str(interaction?.face_to_face_scene).trim().slice(0, 2000),
  };
}


export function companionContinuationPlan(
  interaction: NonNullable<ProductSettings["interaction"]>,
  afterPlayback: boolean,
  completedRounds: number,
) {
  if (!bool(interaction.unlimited_reply_enabled) || !afterPlayback) return null;
  const limit = Math.max(1, Math.min(50, num(interaction.unlimited_reply_max_rounds, 10)));
  if (completedRounds >= limit) return null;
  return { delaySeconds: 10, nextSequence: completedRounds + 1, limit };
}


function mergeVoiceText(parts: string[]) {
  return parts.reduce((merged, part) => {
    const next = part.trim();
    if (!next) return merged;
    if (!merged) return next;
    if (/[，。！？；：,.!?;:]$/.test(merged) || /^[，。！？；：,.!?;:]/.test(next)) return `${merged}${next}`;
    return `${merged}，${next}`;
  }, "");
}


export function voiceMergeDelay(text: string, configured: unknown, afterBargeIn = false) {
  // ASR 的标点是模型预测，不是用户主动点击发送。保留足够的续话窗口，
  // 避免中文口语中的换气、犹豫和短停顿被拆成多个 LLM 请求。
  const normalDelay = Math.max(900, Math.min(2200, num(configured, 1100)));
  const compact = text.trim().replace(/[，。！？、,.!?\s]+$/g, "");
  if (afterBargeIn) return Math.max(1500, normalDelay);
  if (/(?:嗯|呃|额|那个|就是|然后|所以|但是|不过|怎么说|我想想)$/.test(compact)) {
    return Math.max(1700, normalDelay);
  }
  if (compact.length <= 4) return Math.max(1300, normalDelay);
  if (/(?:[。！？!?]|…{1,3})$/.test(text.trim())) return Math.max(650, Math.min(800, normalDelay));
  return normalDelay;
}


const INPUT_LOCKED_ASR_EVENTS = [
  "asr.speech_candidate",
  "asr.speech_candidate_cleared",
  "asr.speech_start",
  "asr.barge_in_confirmed",
  "asr.partial",
  "asr.final",
  "asr.deferred",
];

export function shouldIgnoreASREvent(inputLocked: boolean, event: string) {
  return inputLocked && INPUT_LOCKED_ASR_EVENTS.includes(event);
}


export function voiceReconnectDelay(attempt: number) {
  return VOICE_RECONNECT_DELAYS_MS[Math.min(
    Math.max(0, attempt),
    VOICE_RECONNECT_DELAYS_MS.length - 1,
  )];
}


export function shouldRetryMicrophoneStartup(error: unknown) {
  const name = str((error as { name?: unknown } | null)?.name);
  return !["NotAllowedError", "SecurityError", "TypeError"].includes(name);
}


export function asrClientDisposition(data: Record<string, unknown>) {
  const quality = str(data.quality || "accepted");
  const rawText = str(data.text).trim();
  const confirmedText = str(
    data.confirmed_text || (quality === "accepted" ? rawText : ""),
  ).trim();
  const uncertainSegments = Array.isArray(data.uncertain_segments)
    ? data.uncertain_segments
      .map(asRecord)
      .map((item) => ({
        text: str(item.text).trim(),
        reason: str(item.reason || "low_confidence"),
      }))
      .filter((item) => item.text)
    : [];
  return {
    quality,
    rawText,
    confirmedText,
    uncertainSegments,
    submitToLLM: Boolean(
      confirmedText && quality !== "rejected" && bool(data.auto_send),
    ),
    commitBargeIn: Boolean(
      confirmedText
      && quality !== "rejected"
      && bool(data.barge_in_eligible),
    ),
  };
}


type TtsRuntime = ReturnType<typeof useTtsRuntime>;

export interface VoiceSessionRuntimeCallbacks {
  notify: (message: string) => void;
  getInput: () => string;
  setInput: (value: string) => void;
  setMessages: Dispatch<SetStateAction<Message[]>>;
  cancelRun: () => Promise<void>;
  getRunId: () => string;
  getRound: () => number;
  isGenerating: () => boolean;
  sendMessage: TurnSend;
  openVoiceEntryModal: () => void;
  closeVoiceEntryModal: () => void;
  setModalDirty: (dirty: boolean) => void;
  onSettingsSaved: (settings: ProductSettings) => void;
}

interface UseVoiceSessionRuntimeOptions {
  settings: ProductSettings | null;
  input: string;
  generating: boolean;
  messages: Message[];
  callbacksRef: MutableRefObject<VoiceSessionRuntimeCallbacks>;
  tts: Pick<
    TtsRuntime,
    | "captureVoiceInterruption"
    | "hasQueuedAudio"
    | "isAudioPlaying"
    | "playbackAudioContext"
    | "publishPlaybackState"
    | "setPlaybackDucked"
    | "stopAudio"
  >;
}

export function useVoiceSessionRuntime({
  settings,
  input,
  generating,
  messages,
  callbacksRef,
  tts,
}: UseVoiceSessionRuntimeOptions) {
  const [voice, setVoice] = useState<VoiceSessionState>({
    open: false,
    phase: "idle",
    transcript: "",
    reply: "",
    level: 0,
    error: "",
  });
  const [voiceEntryMode, setVoiceEntryMode] = useState<VoiceInteractionMode>("call");
  const [voiceEntryScene, setVoiceEntryScene] = useState("");
  const [voiceEntryBusy, setVoiceEntryBusy] = useState(false);
  const [voiceEntryError, setVoiceEntryError] = useState("");
  const [companionRound, setCompanionRound] = useState(0);

  const voiceOpenRef = useRef(false);
  const voiceInteractionRef = useRef<VoiceInteractionContext>({ mode: "call", scene: "" });
  const companionRoundRef = useRef(0);
  const companionArmedRef = useRef(false);
  const voiceSocketRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const micWorkletRef = useRef<AudioWorkletNode | null>(null);
  const silentMonitorRef = useRef<GainNode | null>(null);
  const warmVoiceCaptureRef = useRef<WarmVoiceCapture | null>(null);
  const captureContextRef = useRef<AudioContext | null>(null);
  const captureWorkletLoadedRef = useRef(false);
  const voiceSessionGenerationRef = useRef(0);
  const voiceIntentRef = useRef({ id: "", generation: 0, lastEventSeq: 0 });
  const voiceFallbackCaptureRef = useRef(false);
  const voiceStartInFlightGenerationRef = useRef<number | null>(null);
  const voiceReconnectTimerRef = useRef<number | null>(null);
  const voiceReconnectAttemptRef = useRef(0);
  const startListeningRef = useRef<(() => void) | null>(null);
  const voiceEntryControllerRef = useRef<AbortController | null>(null);
  const voiceEntryGenerationRef = useRef(0);
  const voiceConnectWatchdogRef = useRef<number | null>(null);
  const voiceCaptureWatchdogRef = useRef<number | null>(null);
  const voiceNativeCaptureRef = useRef(false);
  const microphoneRequestRef = useRef<Promise<MediaStream> | null>(null);
  const voiceInputLockedRef = useRef(false);
  const partialRenderRef = useRef(0);
  const voiceLevelRenderRef = useRef(0);
  const closingVoiceRef = useRef(false);
  const idleTimerRef = useRef<number | null>(null);
  const idleContinuationSentRef = useRef(false);
  const voiceMergeTimerRef = useRef<number | null>(null);
  const voiceSegmentsRef = useRef<string[]>([]);
  const deferredVoiceSegmentsRef = useRef<string[]>([]);
  const activeVoiceTurnTextRef = useRef("");
  const activeVoiceTurnRoundRef = useRef(0);
  const pendingASREvidenceRef = useRef<{
    uncertain_segments: Array<{ text: string; reason: string }>;
    decision_reasons: string[];
  } | null>(null);
  const lastBargeCommitAtRef = useRef(0);
  const bargeCommittedRef = useRef(false);
  const bargeBackoffRef = useRef({ level: 0, until: 0 });
  const recentVoiceTextsRef = useRef<Map<string, number>>(new Map());
  const queueVoiceSegmentRef = useRef<((text: string, deferred?: boolean) => void) | null>(null);

  const {
    captureVoiceInterruption,
    hasQueuedAudio,
    isAudioPlaying,
    playbackAudioContext,
    publishPlaybackState: republishTtsPlaybackState,
    setPlaybackDucked,
    stopAudio,
  } = tts;

  const notify = useCallback(
    (message: string) => callbacksRef.current.notify(message),
    [callbacksRef],
  );
  const setInput = useCallback(
    (value: string) => callbacksRef.current.setInput(value),
    [callbacksRef],
  );
  const setMessages = useCallback<Dispatch<SetStateAction<Message[]>>>(
    (value) => callbacksRef.current.setMessages(value),
    [callbacksRef],
  );
  const cancelRun = useCallback(
    () => callbacksRef.current.cancelRun(),
    [callbacksRef],
  );
  const setModalDirty = useCallback(
    (dirty: boolean) => callbacksRef.current.setModalDirty(dirty),
    [callbacksRef],
  );
  const setModal = useCallback(
    (value: "voice-entry" | null) => {
      if (value === "voice-entry") callbacksRef.current.openVoiceEntryModal();
      else callbacksRef.current.closeVoiceEntryModal();
    },
    [callbacksRef],
  );
  const setSettings = useCallback(
    (value: ProductSettings) => callbacksRef.current.onSettingsSaved(value),
    [callbacksRef],
  );
  const discardWarmVoiceCapture = useCallback((suspendContext = true) => {
    const warm = warmVoiceCaptureRef.current;
    warmVoiceCaptureRef.current = null;
    if (!warm) return;
    window.clearTimeout(warm.timer);
    warm.graph.worklet.port.onmessage = null;
    warm.graph.stream.getTracks().forEach((track) => track.stop());
    warm.graph.source.disconnect();
    warm.graph.worklet.disconnect();
    warm.graph.monitor.disconnect();
    if (suspendContext && warm.graph.context.state === "running") {
      void warm.graph.context.suspend().catch(() => undefined);
    }
  }, []);


  const cancelIdleContinuation = useCallback(() => {
    if (idleTimerRef.current != null) window.clearTimeout(idleTimerRef.current);
    idleTimerRef.current = null;
  }, []);

  const scheduleIdleContinuation = useCallback((mode: "text" | "voice", afterPlayback = false) => {
    const interaction = settings?.interaction;
    const continuous = mode === "voice" && bool(interaction?.unlimited_reply_enabled);
    const companionPlan = continuous
      ? companionContinuationPlan(interaction || {}, afterPlayback, companionRoundRef.current)
      : null;
    if (continuous && !companionPlan) return;
    cancelIdleContinuation();
    if (!continuous && (!bool(interaction?.idle_continuation_enabled) || idleContinuationSentRef.current)) return;
    const delaySeconds = companionPlan
      ? companionPlan.delaySeconds
      : mode === "voice"
        ? num(interaction?.voice_idle_seconds, 30)
        : num(interaction?.text_idle_seconds, 180);
    idleTimerRef.current = window.setTimeout(() => {
      idleTimerRef.current = null;
      if (callbacksRef.current.isGenerating() || callbacksRef.current.getInput().trim()) return;
      if (mode === "voice" && (!voiceOpenRef.current || isAudioPlaying())) return;
      if (mode === "text" && voiceOpenRef.current) return;
      if (!continuous) idleContinuationSentRef.current = true;
      const nextSequence = companionPlan?.nextSequence || 0;
      void callbacksRef.current.sendMessage(
        "",
        "primary",
        callbacksRef.current.getRound(),
        true,
        continuous ? "continuous_companionship" : "idle_continuation",
        nextSequence,
        companionPlan?.limit || 0,
      );
    }, Math.max(1, delaySeconds) * 1000);
  }, [cancelIdleContinuation, settings]);


  const publishTtsPlaybackState = useCallback((playing: boolean, playbackText: string) => {
    const socket = voiceSocketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      const now = performance.now();
      if (bargeBackoffRef.current.until <= now) bargeBackoffRef.current = { level: 0, until: 0 };
      socket.send(JSON.stringify({
        action: "playback_state",
        playing,
        playback_text: playbackText,
        barge_backoff_level: playing ? bargeBackoffRef.current.level : 0,
      }));
    }
  }, []);


  const setVoiceInputLocked = useCallback((locked: boolean, reason: string) => {
    voiceInputLockedRef.current = locked;
    const socket = voiceSocketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action: "input_gate", locked, reason }));
    }
    if (locked) setVoice((current) => ({ ...current, level: 0 }));
  }, []);


  const scheduleVoiceReconnect = useCallback((reason: string, phase: VoiceSessionState["phase"] = "connecting") => {
    if (!voiceOpenRef.current || closingVoiceRef.current || voiceReconnectTimerRef.current != null) return;
    setVoiceInputLocked(false, "voice_transport_lost");
    const attempt = voiceReconnectAttemptRef.current;
    const delay = voiceReconnectDelay(attempt);
    voiceReconnectAttemptRef.current = attempt + 1;
    const initialRecovery = attempt < VOICE_RECONNECT_DELAYS_MS.length;
    setVoice((current) => ({
      ...current,
      phase,
      error: initialRecovery
        ? `${reason}，正在自动恢复（${attempt + 1}/${VOICE_RECONNECT_DELAYS_MS.length}）`
        : `${reason}，服务仍在启动，后台持续恢复（第 ${attempt + 1} 次）`,
      level: 0,
    }));
    voiceReconnectTimerRef.current = window.setTimeout(() => {
      voiceReconnectTimerRef.current = null;
      if (voiceOpenRef.current && !closingVoiceRef.current) startListeningRef.current?.();
    }, delay);
  }, [setVoiceInputLocked]);


  const closeCaptureContext = useCallback(() => {
    discardWarmVoiceCapture(false);
    const context = captureContextRef.current;
    captureContextRef.current = null;
    captureWorkletLoadedRef.current = false;
    if (context && context.state !== "closed") {
      void context.close().catch(() => undefined);
    }
  }, [discardWarmVoiceCapture]);

  const resetStalledCaptureContext = useCallback(() => {
    // A Chromium AudioContext can remain pending while resume() or
    // audioWorklet.addModule() is awaiting the audio service. Detach it before
    // retrying so the next attempt cannot inherit the same stalled context.
    const context = captureContextRef.current;
    captureContextRef.current = null;
    captureWorkletLoadedRef.current = false;
    if (context && context.state !== "closed") {
      context.onstatechange = null;
      void context.close().catch(() => undefined);
    }
  }, []);

  const captureAudioContext = useCallback(async () => {
    let context = captureContextRef.current;
    if (!context || context.state === "closed") {
      context = new AudioContext({ latencyHint: "interactive" });
      captureContextRef.current = context;
      captureWorkletLoadedRef.current = false;
    }
    if (context.state !== "running") await context.resume();
    if (context.state !== "running") throw new Error("麦克风音频上下文未能启动");
    if (!captureWorkletLoadedRef.current) {
      await context.audioWorklet.addModule("/assets/pcm-worklet.js");
      captureWorkletLoadedRef.current = true;
    }
    return context;
  }, []);

  const acquireMicrophoneStream = useCallback(() => {
    if (microphoneRequestRef.current) return microphoneRequestRef.current;
    const request = (async () => {
      const constraints = await preferredVoiceCaptureConstraints();
      try {
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        // Some WebView/test shims expose only getTracks(); real MediaStreams
        // expose both. Device caching must never invalidate an otherwise-live
        // stream when the specialized accessor is absent.
        const track = (
          typeof stream.getAudioTracks === "function"
            ? stream.getAudioTracks()
            : stream.getTracks()
        )[0];
        const deviceId = typeof track?.getSettings === "function"
          ? track.getSettings().deviceId
          : "";
        if (deviceId && deviceId !== "default" && deviceId !== "communications") {
          localStorage.setItem(VOICE_INPUT_DEVICE_STORAGE_KEY, deviceId);
        }
        return stream;
      } catch (error) {
        if (
          constraints !== VOICE_CAPTURE_FALLBACK_CONSTRAINTS
          && (error as DOMException)?.name === "OverconstrainedError"
        ) {
          localStorage.removeItem(VOICE_INPUT_DEVICE_STORAGE_KEY);
          return navigator.mediaDevices.getUserMedia(VOICE_CAPTURE_FALLBACK_CONSTRAINTS);
        }
        throw error;
      }
    })();
    microphoneRequestRef.current = request;
    void request.finally(() => {
      if (microphoneRequestRef.current === request) microphoneRequestRef.current = null;
    }).catch(() => undefined);
    return request;
  }, []);

  const createVoiceCaptureGraph = useCallback(async (): Promise<VoiceCaptureGraph> => {
    const warm = warmVoiceCaptureRef.current;
    warmVoiceCaptureRef.current = null;
    if (warm) {
      window.clearTimeout(warm.timer);
      const tracks = warm.graph.stream.getAudioTracks();
      if (tracks.length && tracks.every((track) => track.readyState === "live")) {
        try {
          if (warm.graph.context.state !== "running") await warm.graph.context.resume();
          if (warm.graph.context.state !== "running") throw new Error("缓存的麦克风音频上下文未能恢复");
          tracks.forEach((track) => { track.enabled = true; });
          warm.graph.source.connect(warm.graph.worklet);
          return warm.graph;
        } catch {
          // A parked graph is only an optimization. If Chromium cannot resume
          // it, dispose it and fall through to a fresh capture instead of
          // leaving the voice entry permanently pending.
          warm.graph.stream.getTracks().forEach((track) => track.stop());
          warm.graph.source.disconnect();
          warm.graph.worklet.disconnect();
          warm.graph.monitor.disconnect();
          if (captureContextRef.current === warm.graph.context) {
            captureContextRef.current = null;
            captureWorkletLoadedRef.current = false;
          }
          if (warm.graph.context.state !== "closed") {
            void warm.graph.context.close().catch(() => undefined);
          }
        }
      } else {
        warm.graph.stream.getTracks().forEach((track) => track.stop());
        warm.graph.source.disconnect();
        warm.graph.worklet.disconnect();
        warm.graph.monitor.disconnect();
      }
    }
    let stream: MediaStream | null = null;
    try {
      // Capture owns a dedicated, reusable AudioContext. TTS playback cannot
      // suspend or close the microphone graph, and the PCM worklet is loaded
      // only once for the lifetime of the product window.
      // Acquire the input endpoint before opening the WebAudio render graph.
      // Initializing both sides of the same USB headset in Promise.all can
      // deadlock Chromium's Windows audio service on the first voice entry.
      let requestTimeout = 0;
      const activeStream = await Promise.race([
        acquireMicrophoneStream(),
        new Promise<MediaStream>((_resolve, reject) => {
          requestTimeout = window.setTimeout(() => {
            const error = new Error("麦克风设备在 4 秒内没有响应");
            error.name = "VoiceCaptureTimeoutError";
            reject(error);
          }, VOICE_DEVICE_REQUEST_TIMEOUT_MS);
        }),
      ]).finally(() => window.clearTimeout(requestTimeout));
      stream = activeStream;
      const context = await captureAudioContext();
      const source = context.createMediaStreamSource(activeStream);
      const worklet = new AudioWorkletNode(context, "mindspace-pcm", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        channelCount: 1,
        channelCountMode: "explicit",
      });
      const monitor = context.createGain();
      monitor.gain.value = 0.0000001;
      source.connect(worklet);
      worklet.connect(monitor);
      monitor.connect(context.destination);
      return { stream: activeStream, context, source, worklet, monitor };
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      throw error;
    }
  }, [acquireMicrophoneStream, captureAudioContext]);



  const flushVoiceSegments = useCallback(async () => {
    voiceMergeTimerRef.current = null;
    const pending = voiceSegmentsRef.current.splice(0);
    if (!pending.length) return;
    const supplement = mergeVoiceText(pending);
    const hasActiveTurn = Boolean(activeVoiceTurnTextRef.current);
    const targetRound = hasActiveTurn ? activeVoiceTurnRoundRef.current : callbacksRef.current.getRound();
    const content = hasActiveTurn
      ? mergeVoiceText([activeVoiceTurnTextRef.current, supplement])
      : supplement;
    if (!content) return;
    bargeCommittedRef.current = false;
    if (callbacksRef.current.isGenerating()) {
      await cancelRun();
      setMessages((items) => items.filter((item) => item.round !== targetRound));
    }
    setInput("");
    setVoice((current) => ({ ...current, transcript: content, phase: "thinking", error: "" }));
    await callbacksRef.current.sendMessage(content, "primary", targetRound, false, "none");
  }, [cancelRun]);

  const queueVoiceSegment = useCallback((text: string, deferred = false) => {
    const cleaned = text.trim();
    if (!cleaned) return;
    if (deferred && isAudioPlaying()) {
      deferredVoiceSegmentsRef.current.push(cleaned);
      setVoice((current) => ({ ...current, transcript: mergeVoiceText(deferredVoiceSegmentsRef.current), phase: "deferred", error: "" }));
      return;
    }
    const last = voiceSegmentsRef.current.at(-1);
    if (last !== cleaned) voiceSegmentsRef.current.push(cleaned);
    if (voiceMergeTimerRef.current != null) window.clearTimeout(voiceMergeTimerRef.current);
    const preview = mergeVoiceText([
      activeVoiceTurnTextRef.current,
      ...voiceSegmentsRef.current,
    ]);
    setInput(preview);
    setVoice((current) => ({ ...current, transcript: preview, phase: "collecting", level: 0, error: "" }));
    const delay = voiceMergeDelay(
      cleaned,
      settings?.audio.asr_utterance_merge_ms,
      bargeCommittedRef.current,
    );
    voiceMergeTimerRef.current = window.setTimeout(() => { void flushVoiceSegments(); }, delay);
  }, [flushVoiceSegments, settings?.audio.asr_utterance_merge_ms]);

  useEffect(() => { queueVoiceSegmentRef.current = queueVoiceSegment; }, [queueVoiceSegment]);

  useEffect(() => {
    if (input.trim()) {
      cancelIdleContinuation();
      return;
    }
    if (!generating && messages.some((item) => item.role === "assistant" && item.status === "complete")) {
      if (voiceOpenRef.current && (isAudioPlaying() || hasQueuedAudio())) return;
      scheduleIdleContinuation(voiceOpenRef.current ? "voice" : "text");
    }
  }, [cancelIdleContinuation, generating, input, messages, scheduleIdleContinuation]);

  useEffect(() => () => cancelIdleContinuation(), [cancelIdleContinuation]);

  const stopListening = useCallback((finalize = false, keepCaptureWarm = false, keepNativeTransport = false) => {
    if (voiceReconnectTimerRef.current != null) window.clearTimeout(voiceReconnectTimerRef.current);
    voiceReconnectTimerRef.current = null;
    if (voiceConnectWatchdogRef.current != null) window.clearTimeout(voiceConnectWatchdogRef.current);
    voiceConnectWatchdogRef.current = null;
    if (voiceCaptureWatchdogRef.current != null) window.clearInterval(voiceCaptureWatchdogRef.current);
    voiceCaptureWatchdogRef.current = null;
    const socket = voiceSocketRef.current;
    if (keepNativeTransport && voiceNativeCaptureRef.current && socket?.readyState === WebSocket.OPEN) {
      try { socket.send(JSON.stringify({ action: finalize ? "stop" : "deactivate" })); } catch { /* transport is already closing */ }
      return;
    }
    voiceSessionGenerationRef.current += 1;
    const stream = mediaStreamRef.current;
    const context = audioContextRef.current;
    const source = audioSourceRef.current;
    const worklet = micWorkletRef.current;
    const monitor = silentMonitorRef.current;
    voiceNativeCaptureRef.current = false;
    voiceSocketRef.current = null;
    mediaStreamRef.current = null;
    audioContextRef.current = null;
    audioSourceRef.current = null;
    micWorkletRef.current = null;
    silentMonitorRef.current = null;
    if (worklet) worklet.port.onmessage = null;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ action: finalize ? "stop" : "deactivate" }));
        }
        socket.close(1000, "voice mode closed");
      } catch { /* the connection is already closing */ }
    }
    const graphReady = Boolean(stream && context && source && worklet && monitor);
    if (graphReady && keepCaptureWarm) {
      const graph = {
        stream: stream as MediaStream,
        context: context as AudioContext,
        source: source as MediaStreamAudioSourceNode,
        worklet: worklet as AudioWorkletNode,
        monitor: monitor as GainNode,
      };
      graph.stream.getTracks().forEach((track) => {
        track.onended = null;
        track.onmute = null;
        track.onunmute = null;
        track.enabled = false;
      });
      graph.source.disconnect();
      discardWarmVoiceCapture(false);
      const expiresAt = performance.now() + VOICE_CAPTURE_WARM_GRACE_MS;
      const timer = window.setTimeout(() => {
        const warm = warmVoiceCaptureRef.current;
        if (!warm || warm.graph !== graph) return;
        warmVoiceCaptureRef.current = null;
        graph.stream.getTracks().forEach((track) => track.stop());
        graph.source.disconnect();
        graph.worklet.disconnect();
        graph.monitor.disconnect();
        if (!voiceOpenRef.current && graph.context.state === "running") {
          void graph.context.suspend().catch(() => undefined);
        }
      }, VOICE_CAPTURE_WARM_GRACE_MS);
      warmVoiceCaptureRef.current = { graph, expiresAt, timer };
      return;
    }
    stream?.getTracks().forEach((track) => {
      track.onended = null;
      track.onmute = null;
      track.onunmute = null;
      track.stop();
    });
    source?.disconnect();
    worklet?.disconnect();
    monitor?.disconnect();
    if (context && context !== captureContextRef.current && context.state !== "closed") {
      void context.close().catch(() => undefined);
    }
  }, [discardWarmVoiceCapture]);

  const startListening = useCallback(async () => {
    if (!voiceOpenRef.current) return;
    closingVoiceRef.current = false;
    const currentGeneration = voiceSessionGenerationRef.current;
    if (voiceStartInFlightGenerationRef.current === currentGeneration) return;
    const existingSocket = voiceSocketRef.current;
    const existingTrack = mediaStreamRef.current?.getAudioTracks()[0];
    if (
      existingSocket
      && (existingSocket.readyState === WebSocket.CONNECTING || existingSocket.readyState === WebSocket.OPEN)
      && (
        voiceNativeCaptureRef.current
        || (
          existingTrack?.readyState === "live"
          && audioContextRef.current?.state !== "closed"
        )
      )
    ) {
      if (voiceNativeCaptureRef.current && existingSocket.readyState === WebSocket.OPEN) {
        const intent = { id: crypto.randomUUID(), generation: currentGeneration, lastEventSeq: 0 };
        voiceIntentRef.current = intent;
        existingSocket.send(JSON.stringify({
          action: "start",
          run_id: callbacksRef.current.getRunId(),
          intent_id: intent.id,
          generation: intent.generation,
        }));
        existingSocket.send(JSON.stringify({ action: "playback_state", playing: isAudioPlaying() }));
        setVoice((current) => ({
          ...current,
          phase: isAudioPlaying() ? "assistant-speaking" : "listening",
          error: "",
          level: 0,
        }));
      }
      return;
    }
    stopListening(false);
    if (!voiceOpenRef.current) return;
    const generation = voiceSessionGenerationRef.current;
    voiceStartInFlightGenerationRef.current = generation;
    setVoice((current) => ({ ...current, phase: "connecting", error: "", level: 0 }));
    let stream: MediaStream | null = null;
    let context: AudioContext | null = null;
    let source: MediaStreamAudioSourceNode | null = null;
    let worklet: AudioWorkletNode | null = null;
    let silentMonitor: GainNode | null = null;
    let socket: WebSocket | null = null;
    let captureWatchdog: number | null = null;
    let activeTrack: MediaStreamTrack | null = null;
    let nativeCaptureMode = false;
    let asrReady = false;
    let captureGraphReady = false;
    let captureHealthy = false;
    let hasCaptureFrame = false;
    let captureStartedAt = performance.now();
    let lastCaptureFrameAt = captureStartedAt;
    let recoveryStarted = false;
    const pendingSocketEvents: MessageEvent[] = [];
    const pendingPCM: ArrayBuffer[] = [];
    let handleSocketEvent: ((event: MessageEvent) => void) | null = null;
    const isCurrent = () => (
      voiceOpenRef.current
      && !closingVoiceRef.current
      && voiceSessionGenerationRef.current === generation
    );
    const invalidateCurrentStart = () => {
      // The start promise may still be waiting for getUserMedia/AudioWorklet.
      // Clear both guards before scheduling recovery; otherwise the reconnect
      // timer sees the old generation as "already starting" and does nothing.
      if (voiceSessionGenerationRef.current === generation) {
        voiceSessionGenerationRef.current += 1;
      }
      if (voiceStartInFlightGenerationRef.current === generation) {
        voiceStartInFlightGenerationRef.current = null;
      }
    };
    const releaseLocal = () => {
      if (captureWatchdog != null) window.clearInterval(captureWatchdog);
      if (voiceCaptureWatchdogRef.current === captureWatchdog) voiceCaptureWatchdogRef.current = null;
      captureWatchdog = null;
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        try { socket.close(1000, "stale voice session"); } catch { /* already closed */ }
      }
      stream?.getTracks().forEach((track) => {
        track.onended = null;
        track.onmute = null;
        track.onunmute = null;
        track.stop();
      });
      if (worklet) worklet.port.onmessage = null;
      source?.disconnect();
      worklet?.disconnect();
      silentMonitor?.disconnect();
      if (
        context
        // Capture owns one process-lifetime AudioContext. A stale async start
        // may dispose only its nodes/track; closing this shared context also
        // kills a newer session that has already adopted it.
        && context !== captureContextRef.current
        && context.state !== "closed"
      ) void context.close().catch(() => undefined);
      if (voiceSocketRef.current === socket) voiceSocketRef.current = null;
      if (voiceSocketRef.current == null) voiceNativeCaptureRef.current = false;
      if (mediaStreamRef.current === stream) mediaStreamRef.current = null;
      if (audioContextRef.current === context) audioContextRef.current = null;
      if (audioSourceRef.current === source) audioSourceRef.current = null;
      if (micWorkletRef.current === worklet) micWorkletRef.current = null;
      if (silentMonitorRef.current === silentMonitor) silentMonitorRef.current = null;
    };
    const recoverCapture = (reason: string) => {
      if (!isCurrent() || recoveryStarted) return;
      recoveryStarted = true;
      invalidateCurrentStart();
      releaseLocal();
      resetStalledCaptureContext();
      scheduleVoiceReconnect(reason);
    };
    try {
      // Prefer the resident native stream owned by the ASR worker. HyperX
      // takes several seconds every time Windows opens the endpoint, so the
      // worker pays that cost once during preload and the page only subscribes.
      try {
        const status = await requestWithTimeout<Record<string, unknown>>(
          "/api/v1/audio/status",
          {},
          VOICE_NATIVE_CAPTURE_STATUS_TIMEOUT_MS,
        );
        const native = asRecord(asRecord(status.asr_detail).native_capture);
        if (
          str(native.state) === "error"
          && str(native.error_code) === "no_input_device"
        ) {
          const error = new Error(
            str(native.error || "Windows 没有发现可用麦克风输入设备"),
          );
          error.name = "NativeMicrophoneUnavailableError";
          throw error;
        }
        nativeCaptureMode = bool(native.available)
          && ["opening", "ready"].includes(str(native.state));
        captureGraphReady = nativeCaptureMode && bool(native.ready);
        captureHealthy = captureGraphReady;
        hasCaptureFrame = captureGraphReady;
      } catch (error) {
        if ((error as Error).name === "NativeMicrophoneUnavailableError") {
          setVoice((current) => ({
            ...current,
            phase: "error",
            error: `无法开始监听：${(error as Error).message}`,
            level: 0,
          }));
          return;
        }
        if (!voiceFallbackCaptureRef.current) {
          setVoice((current) => ({
            ...current,
            phase: "preparing",
            error: "本机麦克风仍在预热；不会自动抢占浏览器麦克风",
            level: 0,
          }));
          scheduleVoiceReconnect("等待本机麦克风就绪", "preparing");
          return;
        }
      }

      if (!nativeCaptureMode && !voiceFallbackCaptureRef.current) {
        setVoice((current) => ({
          ...current,
          phase: "preparing",
          error: "本机语音服务正在准备麦克风；可稍候或手动切换备用采集",
          level: 0,
        }));
        scheduleVoiceReconnect("等待本机麦克风首帧", "preparing");
        return;
      }

      if (!nativeCaptureMode) {
        // Browser fallback is explicit user intent only.  It must be live
        // before opening ASR so it can never race a native device request.
        ({ stream, context, source, worklet, monitor: silentMonitor } = await createVoiceCaptureGraph());
        if (!isCurrent() || recoveryStarted) { releaseLocal(); return; }
        activeTrack = stream.getAudioTracks()[0] || null;
        if (!activeTrack) throw new Error("系统没有返回可用的麦克风音轨");
        if (!context || context.state !== "running") throw new Error("麦克风音频上下文未能启动");
        if (!source || !worklet || !silentMonitor) throw new Error("麦克风采集图未能建立");
        const activeStream = stream as MediaStream;
        const activeContext = context as AudioContext;
        const activeSource = source as MediaStreamAudioSourceNode;
        const activeWorklet = worklet as AudioWorkletNode;
        const activeMonitor = silentMonitor as GainNode;
        mediaStreamRef.current = activeStream;
        audioContextRef.current = activeContext;
        audioSourceRef.current = activeSource;
        micWorkletRef.current = activeWorklet;
        silentMonitorRef.current = activeMonitor;
        captureGraphReady = true;
        clearVoiceCaptureRecovery();
      }
      voiceNativeCaptureRef.current = nativeCaptureMode;

      // ASR is resident and normally answers in milliseconds. Only transport
      // recovery is retried automatically; it reuses the already-live graph.
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${location.host}/api/v1/audio/asr/stream`);
      socket.binaryType = "arraybuffer";
      const activeSocket = socket as WebSocket;
      voiceSocketRef.current = activeSocket;
      voiceConnectWatchdogRef.current = window.setTimeout(() => {
        if (!isCurrent()) return;
        voiceConnectWatchdogRef.current = null;
        recoveryStarted = true;
        invalidateCurrentStart();
        stopListening(false, true);
        scheduleVoiceReconnect("语音识别连接等待超时");
      }, VOICE_TRANSPORT_READY_TIMEOUT_MS);
      const intent = { id: crypto.randomUUID(), generation, lastEventSeq: 0 };
      voiceIntentRef.current = intent;
      activeSocket.onopen = () => {
        if (!isCurrent()) { releaseLocal(); return; }
        activeSocket.send(JSON.stringify({
          action: "start",
          run_id: callbacksRef.current.getRunId(),
          intent_id: intent.id,
          generation: intent.generation,
        }));
        activeSocket.send(JSON.stringify({ action: "playback_state", playing: isAudioPlaying() }));
      };
      activeSocket.onmessage = (event) => {
        if (handleSocketEvent) handleSocketEvent(event);
        else if (pendingSocketEvents.length < 8) pendingSocketEvents.push(event);
      };
      activeSocket.onerror = () => {
        if (voiceConnectWatchdogRef.current != null) window.clearTimeout(voiceConnectWatchdogRef.current);
        voiceConnectWatchdogRef.current = null;
        if (isCurrent()) setVoice((current) => ({ ...current, phase: "connecting", error: "实时语音连接失败，等待恢复", level: 0 }));
      };
      activeSocket.onclose = () => {
        if (voiceConnectWatchdogRef.current != null) window.clearTimeout(voiceConnectWatchdogRef.current);
        voiceConnectWatchdogRef.current = null;
        const current = isCurrent();
        recoveryStarted = true;
        if (current && captureGraphReady) {
          // A socket reconnect does not require asking Windows for the
          // microphone again. Park the still-live capture graph and let the
          // bounded reconnect reuse it.
          stopListening(false, true);
        } else {
          if (current) {
            invalidateCurrentStart();
            resetStalledCaptureContext();
          }
          releaseLocal();
        }
        if (current) scheduleVoiceReconnect("语音识别连接已断开");
      };

      const markTransportReady = () => {
        if (!isCurrent() || !asrReady || !captureGraphReady) return;
        if (voiceConnectWatchdogRef.current != null) window.clearTimeout(voiceConnectWatchdogRef.current);
        voiceConnectWatchdogRef.current = null;
        voiceReconnectAttemptRef.current = 0;
        setVoice((current) => ({
          ...current,
          phase: isAudioPlaying() ? "assistant-speaking" : "listening",
          error: "",
        }));
        if (!isAudioPlaying()) scheduleIdleContinuation("voice");
      };
      const markCaptureHealthy = () => {
        if (!isCurrent() || captureHealthy || !asrReady || !hasCaptureFrame) return;
        captureHealthy = true;
      };
      const flushPendingPCM = () => {
        if (
          !asrReady
          || activeSocket.readyState !== WebSocket.OPEN
          || activeSocket.bufferedAmount > 256 * 1024
          || voiceInputLockedRef.current
        ) return;
        while (pendingPCM.length) activeSocket.send(pendingPCM.shift()!);
      };
      const handleCaptureFrame = (data: { pcm: ArrayBuffer; level: number }) => {
        if (!isCurrent()) return;
        lastCaptureFrameAt = performance.now();
        hasCaptureFrame = true;
        markCaptureHealthy();
        if (voiceInputLockedRef.current) return;
        if (
          !asrReady
          || activeSocket.readyState !== WebSocket.OPEN
          || activeSocket.bufferedAmount > 256 * 1024
        ) {
          pendingPCM.push(data.pcm);
          if (pendingPCM.length > VOICE_PENDING_PCM_FRAMES) pendingPCM.shift();
        } else {
          flushPendingPCM();
          activeSocket.send(data.pcm);
        }
        const now = performance.now();
        if (!isAudioPlaying() && now - voiceLevelRenderRef.current >= 50) {
          voiceLevelRenderRef.current = now;
          setVoice((current) => ({ ...current, level: data.level }));
        }
      };
      captureStartedAt = performance.now();
      lastCaptureFrameAt = captureStartedAt;
      markTransportReady();
      if (nativeCaptureMode) {
        captureWatchdog = window.setInterval(() => {
          if (!isCurrent() || recoveryStarted) return;
          const now = performance.now();
          if (!captureGraphReady && now - captureStartedAt >= 30_000) {
            recoverCapture("后台麦克风启动超时");
            return;
          }
          if (
            captureGraphReady
            && hasCaptureFrame
            && now - lastCaptureFrameAt >= VOICE_CAPTURE_STALL_TIMEOUT_MS
          ) {
            recoverCapture("后台麦克风采集已中断");
          }
        }, 1_000);
        voiceCaptureWatchdogRef.current = captureWatchdog;
      } else if (activeTrack && worklet) {
        activeTrack.onended = () => recoverCapture("麦克风设备已断开");
        // A transient track mute is common while Windows changes audio focus.
        // Let the existing track recover in place; only `ended` or a sustained
        // capture stall should rebuild the whole microphone/WebSocket path.
        activeTrack.onmute = null;
        activeTrack.onunmute = () => { lastCaptureFrameAt = performance.now(); };
        worklet.port.onmessage = (event: MessageEvent<{ pcm?: ArrayBuffer; level?: number }>) => {
          const pcm = event.data?.pcm;
          if (!(pcm instanceof ArrayBuffer)) return;
          handleCaptureFrame({ pcm, level: num(event.data.level) });
        };
        captureWatchdog = window.setInterval(() => {
          if (!isCurrent() || recoveryStarted) return;
          const now = performance.now();
          if (activeTrack?.readyState !== "live") {
            recoverCapture("麦克风设备已断开");
            return;
          }
          if (asrReady && !hasCaptureFrame && now - captureStartedAt >= VOICE_CAPTURE_READY_TIMEOUT_MS) {
            recoverCapture("麦克风没有返回音频");
            return;
          }
          if (hasCaptureFrame && now - lastCaptureFrameAt >= VOICE_CAPTURE_STALL_TIMEOUT_MS) {
            recoverCapture("麦克风采集已中断");
          }
        }, 1_000);
        voiceCaptureWatchdogRef.current = captureWatchdog;
      }
      handleSocketEvent = (event) => {
        if (!isCurrent()) return;
        let payload: { event: string; data: Record<string, unknown> };
        try {
          payload = JSON.parse(String(event.data)) as { event: string; data: Record<string, unknown> };
        } catch {
          setVoice((current) => ({ ...current, phase: "error", error: "实时语音返回了无效数据", level: 0 }));
          return;
        }
        const eventIntentId = str(payload.data.intent_id);
        const eventGeneration = num(payload.data.generation);
        const eventSeq = num(payload.data.event_seq);
        if (eventIntentId && (
          eventIntentId !== voiceIntentRef.current.id
          || eventGeneration !== voiceIntentRef.current.generation
          || (eventSeq > 0 && eventSeq <= voiceIntentRef.current.lastEventSeq)
        )) return;
        if (eventIntentId && eventSeq > 0) voiceIntentRef.current.lastEventSeq = eventSeq;
        if (shouldIgnoreASREvent(voiceInputLockedRef.current, payload.event)) return;
        if (payload.event === "asr.ready") {
          asrReady = true;
          // The WebSocket/model is healthy even when the process-lifetime
          // microphone is still completing its one-time device open.
          if (voiceConnectWatchdogRef.current != null) {
            window.clearTimeout(voiceConnectWatchdogRef.current);
            voiceConnectWatchdogRef.current = null;
          }
          const captureMode = str(payload.data.capture_mode);
          if (nativeCaptureMode && captureMode !== "native") {
            recoverCapture("后台麦克风不可用，正在切换备用采集");
            return;
          }
          if (captureMode === "native") {
            nativeCaptureMode = true;
            voiceNativeCaptureRef.current = true;
            captureGraphReady = bool(payload.data.capture_ready);
            captureHealthy = captureGraphReady;
            hasCaptureFrame = captureGraphReady;
            if (captureGraphReady) clearVoiceCaptureRecovery();
          } else {
            flushPendingPCM();
          }
          markTransportReady();
          markCaptureHealthy();
          // Transport readiness is enough to enter listening. The first PCM
          // frame is monitored separately and must never hold the UI in the
          // misleading "connecting" state.
        }
        if (payload.event === "asr.capture_ready") {
          nativeCaptureMode = true;
          voiceNativeCaptureRef.current = true;
          captureGraphReady = true;
          captureHealthy = true;
          hasCaptureFrame = true;
          clearVoiceCaptureRecovery();
          markTransportReady();
        }
        if (payload.event === "asr.level") {
          const now = performance.now();
          lastCaptureFrameAt = now;
          hasCaptureFrame = true;
          markCaptureHealthy();
          if (!isAudioPlaying() && now - voiceLevelRenderRef.current >= 50) {
            voiceLevelRenderRef.current = now;
            setVoice((current) => ({ ...current, level: num(payload.data.level) }));
          }
        }
        if (payload.event === "asr.loading") setVoice((current) => ({ ...current, phase: "connecting" }));
        if (payload.event === "asr.speech_candidate") {
          cancelIdleContinuation();
          bargeCommittedRef.current = false;
          if (isAudioPlaying()) {
            setPlaybackDucked(true);
            setVoice((current) => ({ ...current, phase: "candidate-interruption", error: "" }));
          }
          // A raw energy candidate is not yet confirmed speech. In quiet
          // listening mode keep the UI stable until FSMN-VAD or a decoded
          // partial confirms that the user is actually speaking.
        }
        if (payload.event === "asr.speech_candidate_cleared") {
          setPlaybackDucked(false);
          if (isAudioPlaying()) {
            const backoffMs = num(settings?.audio.asr_false_candidate_backoff_ms, 3000);
            bargeBackoffRef.current = {
              level: Math.min(2, bargeBackoffRef.current.level + 1),
              until: performance.now() + backoffMs,
            };
            republishTtsPlaybackState(true);
            setVoice((current) => ({ ...current, phase: "assistant-speaking", error: "" }));
          } else {
            setVoice((current) => ({ ...current, phase: "listening", error: "" }));
            scheduleIdleContinuation("voice", companionArmedRef.current);
          }
        }
        if (payload.event === "asr.speech_start") {
          // Acoustic/VAD confirmation is not semantic confirmation. Keep TTS
          // ducked and continue collecting text until the arbiter commits.
          setVoice((current) => ({ ...current, phase: "user-speaking", reply: "", error: "" }));
        }
        if (payload.event === "asr.barge_in_confirmed") {
          const now = performance.now();
          const explicitStop = bool(payload.data.explicit_stop);
          const cooldownMs = num(settings?.audio.asr_barge_in_cooldown_ms, 1500);
          const coolingDown = now - lastBargeCommitAtRef.current < cooldownMs;
          if (isAudioPlaying() && !bargeCommittedRef.current && (explicitStop || !coolingDown)) {
            bargeCommittedRef.current = true;
            lastBargeCommitAtRef.current = now;
            captureVoiceInterruption(explicitStop ? "explicit_stop_command" : "confirmed_barge_in");
            setPlaybackDucked(false);
            if (callbacksRef.current.getRunId()) void cancelRun();
            else stopAudio();
          }
        }
        if (payload.event === "asr.partial") {
          const now = performance.now();
          const text = str(payload.data.text);
          if (now - partialRenderRef.current >= 100) {
            partialRenderRef.current = now;
            const preview = mergeVoiceText([activeVoiceTurnTextRef.current, ...voiceSegmentsRef.current, text]);
            setInput(preview);
            setVoice((current) => ({ ...current, transcript: preview, phase: "user-speaking" }));
          }
        }
        if (payload.event === "asr.final") {
          const disposition = asrClientDisposition(payload.data);
          const {
            rawText,
            quality,
            confirmedText,
            uncertainSegments,
          } = disposition;
          const uncertainLabel = uncertainSegments.map((item) => item.text).join("、");
          const displayText = confirmedText && uncertainLabel ? `${confirmedText}（可能是：${uncertainLabel}）` : confirmedText || (rawText ? `（可能是：${rawText}）` : "");
          setVoice((current) => ({ ...current, transcript: displayText, phase: "transcribing", level: 0 }));
          if (disposition.commitBargeIn && isAudioPlaying() && !bargeCommittedRef.current) {
            const now = performance.now();
            const explicitStop = bool(payload.data.explicit_stop);
            const cooldownMs = num(settings?.audio.asr_barge_in_cooldown_ms, 1500);
            if (explicitStop || now - lastBargeCommitAtRef.current >= cooldownMs) {
              bargeCommittedRef.current = true;
              lastBargeCommitAtRef.current = now;
              captureVoiceInterruption(explicitStop ? "explicit_stop_command" : "accepted_asr_final");
              setPlaybackDucked(false);
              if (callbacksRef.current.getRunId()) void cancelRun();
              else stopAudio();
            }
          }
          if (disposition.submitToLLM) {
            const normalized = confirmedText.replace(/[^\u4e00-\u9fffA-Za-z0-9]/g, "").toLowerCase();
            const now = performance.now();
            const duplicateWindow = num(settings?.audio.asr_duplicate_text_window_ms, 3000);
            const previous = recentVoiceTextsRef.current.get(normalized) || 0;
            if (!normalized || now - previous >= duplicateWindow) {
              recentVoiceTextsRef.current.set(normalized, now);
              for (const [key, seenAt] of recentVoiceTextsRef.current) {
                if (now - seenAt > duplicateWindow) recentVoiceTextsRef.current.delete(key);
              }
              if (uncertainSegments.length) {
                const previousEvidence = pendingASREvidenceRef.current;
                pendingASREvidenceRef.current = {
                  uncertain_segments: [
                    ...(previousEvidence?.uncertain_segments || []),
                    ...uncertainSegments,
                  ],
                  decision_reasons: [
                    ...(previousEvidence?.decision_reasons || []),
                    ...(Array.isArray(payload.data.decision_reasons) ? payload.data.decision_reasons.map(String) : []),
                  ].filter((reason, index, values) => values.indexOf(reason) === index),
                };
              }
              queueVoiceSegment(confirmedText, false);
              if (uncertainLabel) setVoice((current) => ({ ...current, transcript: displayText }));
            }
          } else if (displayText) {
            // Draft-only recognition: visible and editable, but it cannot stop
            // TTS, call the LLM, or enter any durable message/memory path.
            setInput(displayText);
            setPlaybackDucked(false);
            if (isAudioPlaying()) {
              const backoffMs = num(settings?.audio.asr_false_candidate_backoff_ms, 3000);
              bargeBackoffRef.current = {
                level: Math.min(2, bargeBackoffRef.current.level + 1),
                until: performance.now() + backoffMs,
              };
              republishTtsPlaybackState(true);
            }
            setVoice((current) => ({ ...current, phase: isAudioPlaying() ? "assistant-speaking" : "listening" }));
          }
        }
        if (payload.event === "asr.deferred") {
          const quality = str(payload.data.quality || "uncertain");
          const confirmed = str(payload.data.confirmed_text).trim();
          if (confirmed && quality !== "rejected") queueVoiceSegment(confirmed, true);
          else setPlaybackDucked(false);
        }
        if (payload.event === "asr.interrupted") setVoice((current) => ({ ...current, phase: "interrupted", reply: "", level: 0 }));
        if (payload.event === "asr.error") {
          setVoice((current) => ({ ...current, phase: "connecting", error: str(payload.data.error), level: 0 }));
          try { activeSocket.close(1012, "asr service unavailable"); } catch { /* close handler will recover */ }
        }
      };
      pendingSocketEvents.splice(0).forEach((event) => handleSocketEvent?.(event));
    } catch (error) {
      const current = isCurrent();
      const retryable = shouldRetryMicrophoneStartup(error);
      recoveryStarted = true;
      // Invalidate this generation before closing the speculative ASR socket.
      // Otherwise its queued `close` event can overwrite a concrete microphone
      // permission error with a generic reconnecting state.
      if (current) invalidateCurrentStart();
      releaseLocal();
      if (current && retryable) resetStalledCaptureContext();
      if (current) {
        if (voiceConnectWatchdogRef.current != null) window.clearTimeout(voiceConnectWatchdogRef.current);
        voiceConnectWatchdogRef.current = null;
        const message = `无法使用麦克风：${(error as Error).message}`;
        if (retryable) {
          scheduleVoiceReconnect(message);
        } else {
          setVoice((state) => ({ ...state, phase: "error", error: message, level: 0 }));
        }
      }
    } finally {
      if (voiceStartInFlightGenerationRef.current === generation) {
        voiceStartInFlightGenerationRef.current = null;
      }
    }
  }, [cancelIdleContinuation, cancelRun, captureVoiceInterruption, createVoiceCaptureGraph, queueVoiceSegment, resetStalledCaptureContext, scheduleIdleContinuation, scheduleVoiceReconnect, setPlaybackDucked, stopAudio, stopListening]);

  useEffect(() => {
    startListeningRef.current = () => { void startListening(); };
    return () => { startListeningRef.current = null; };
  }, [startListening]);

  const enterVoice = useCallback((context: VoiceInteractionContext) => {
    if (voiceOpenRef.current) return;
    cancelIdleContinuation();
    idleContinuationSentRef.current = false;
    companionRoundRef.current = 0;
    companionArmedRef.current = false;
    voiceInputLockedRef.current = false;
    voiceReconnectAttemptRef.current = 0;
    setCompanionRound(0);
    voiceOpenRef.current = true;
    voiceInteractionRef.current = context;
    voiceSegmentsRef.current = [];
    deferredVoiceSegmentsRef.current = [];
    activeVoiceTurnTextRef.current = "";
    activeVoiceTurnRoundRef.current = 0;
    pendingASREvidenceRef.current = null;
    setVoice({ open: true, phase: "connecting", transcript: "", reply: "", level: 0, error: "" });
    void startListening();
  }, [cancelIdleContinuation, startListening]);

  useEffect(() => {
    const recovery = readVoiceCaptureRecovery();
    if (!recovery) return;
    // Keep the attempt marker until a real microphone track is acquired. This
    // guarantees at most one automatic renderer rebuild for one voice entry.
    const timer = window.setTimeout(() => enterVoice(recovery.context), 0);
    return () => window.clearTimeout(timer);
  }, [enterVoice]);

  const openVoiceEntry = useCallback(() => {
    const saved = savedVoiceInteraction(settings);
    setVoiceEntryMode(saved.mode);
    setVoiceEntryScene(saved.scene);
    setVoiceEntryBusy(false);
    setVoiceEntryError("");
    setModalDirty(false);
    setModal("voice-entry");
  }, [settings]);

  const closeVoiceEntry = useCallback(() => {
    voiceEntryGenerationRef.current += 1;
    voiceEntryControllerRef.current?.abort();
    voiceEntryControllerRef.current = null;
    setVoiceEntryBusy(false);
    setVoiceEntryError("");
    setModalDirty(false);
    setModal(null);
  }, []);

  const startVoiceFromEntry = useCallback(() => {
    if (voiceEntryBusy || voiceEntryControllerRef.current) return;
    const context: VoiceInteractionContext = {
      mode: voiceEntryMode,
      scene: voiceEntryScene.trim().slice(0, 2000),
    };
    const generation = voiceEntryGenerationRef.current + 1;
    voiceEntryGenerationRef.current = generation;
    const controller = new AbortController();
    voiceEntryControllerRef.current = controller;
    setVoiceEntryBusy(true);
    setVoiceEntryError("");
    setModalDirty(false);
    setModal(null);
    setVoiceEntryBusy(false);
    enterVoice(context);

    // Persist the user's last interaction choice in the background. Failure to
    // save this preference does not tear down a healthy microphone session.
    void requestWithTimeout<{ settings: ProductSettings }>("/api/v1/settings", {
      method: "PUT",
      body: JSON.stringify({
        interaction: {
          voice_entry_mode: context.mode,
          face_to_face_scene: context.scene,
        },
      }),
      signal: controller.signal,
    }, VOICE_ENTRY_PERSIST_TIMEOUT_MS).then((result) => {
      if (generation === voiceEntryGenerationRef.current && !controller.signal.aborted) {
        setSettings(result.settings);
      }
    }).catch((error: Error) => {
      if (error.name !== "AbortError" && generation === voiceEntryGenerationRef.current) {
        notify(`通话偏好暂未保存：${error.message}`);
      }
    }).finally(() => {
      if (voiceEntryControllerRef.current === controller) voiceEntryControllerRef.current = null;
    });
  }, [enterVoice, notify, voiceEntryBusy, voiceEntryMode, voiceEntryScene]);

  const exitVoice = useCallback(() => {
    voiceEntryControllerRef.current?.abort();
    voiceEntryControllerRef.current = null;
    cancelIdleContinuation();
    companionRoundRef.current = 0;
    companionArmedRef.current = false;
    setCompanionRound(0);
    closingVoiceRef.current = true;
    voiceOpenRef.current = false;
    voiceInputLockedRef.current = false;
    const unsent = mergeVoiceText([
      ...deferredVoiceSegmentsRef.current,
      ...voiceSegmentsRef.current,
    ]);
    if (unsent) setInput(unsent);
    if (voiceMergeTimerRef.current != null) window.clearTimeout(voiceMergeTimerRef.current);
    voiceMergeTimerRef.current = null;
    voiceSegmentsRef.current = [];
    deferredVoiceSegmentsRef.current = [];
    activeVoiceTurnTextRef.current = "";
    activeVoiceTurnRoundRef.current = 0;
    stopListening(false, true, true);
    if (callbacksRef.current.getRunId()) {
      captureVoiceInterruption("voice_mode_closed");
      void cancelRun();
    }
    stopAudio();
    setVoice({ open: false, phase: "idle", transcript: "", reply: "", level: 0, error: "" });
    if (messages.some((item) => item.role === "assistant" && item.status === "complete")) {
      scheduleIdleContinuation("text");
    }
  }, [cancelIdleContinuation, cancelRun, captureVoiceInterruption, messages, scheduleIdleContinuation, stopAudio, stopListening]);

  const retryVoice = useCallback(() => {
    if (voiceReconnectTimerRef.current != null) window.clearTimeout(voiceReconnectTimerRef.current);
    voiceReconnectTimerRef.current = null;
    voiceReconnectAttemptRef.current = 0;
    voiceInputLockedRef.current = false;
    stopAudio();
    voiceFallbackCaptureRef.current = false;
    stopListening(false);
    void startListening();
  }, [startListening, stopAudio, stopListening]);

  const useBrowserVoiceFallback = useCallback(() => {
    if (voiceReconnectTimerRef.current != null) window.clearTimeout(voiceReconnectTimerRef.current);
    voiceReconnectTimerRef.current = null;
    voiceReconnectAttemptRef.current = 0;
    voiceFallbackCaptureRef.current = true;
    stopListening(false);
    void startListening();
  }, [startListening, stopListening]);


  const handleTtsPlaybackComplete = useCallback((playbackFailed: boolean) => {
    const deferred = deferredVoiceSegmentsRef.current.splice(0);
    if (deferred.length) {
      deferred.forEach((text) => queueVoiceSegmentRef.current?.(text, false));
    } else if (!playbackFailed) {
      companionArmedRef.current = true;
      scheduleIdleContinuation("voice", true);
    }
  }, [scheduleIdleContinuation]);

  const isVoiceOpen = useCallback(() => voiceOpenRef.current, []);
  const getVoiceContext = useCallback(() => voiceInteractionRef.current, []);
  const getPendingASREvidence = useCallback(() => pendingASREvidenceRef.current, []);
  const clearPendingASREvidence = useCallback(() => {
    pendingASREvidenceRef.current = null;
  }, []);
  const updateVoice = useCallback(
    (updater: (current: VoiceSessionState) => VoiceSessionState) => setVoice(updater),
    [],
  );
  const markVoiceThinking = useCallback((turn: {
    content: string;
    targetRound: number;
    initiative: boolean;
  }) => {
    if (voiceOpenRef.current) {
      setVoice((current) => ({
        ...current,
        transcript: turn.initiative ? current.transcript : turn.content,
        reply: "",
        phase: "thinking",
        error: "",
      }));
    }
    if (voiceOpenRef.current && !turn.initiative) {
      activeVoiceTurnTextRef.current = turn.content;
      activeVoiceTurnRoundRef.current = turn.targetRound;
    }
  }, []);
  const resetIdleContinuation = useCallback((trigger: InitiativeTrigger) => {
    if (trigger !== "idle_continuation") idleContinuationSentRef.current = false;
  }, []);
  const setIdleContinuationSent = useCallback((sent: boolean) => {
    idleContinuationSentRef.current = sent;
  }, []);
  const recordCompanionRound = useCallback((nextRound: number) => {
    companionRoundRef.current = nextRound;
    setCompanionRound(nextRound);
  }, []);
  const clearActiveVoiceTurn = useCallback(() => {
    activeVoiceTurnTextRef.current = "";
    activeVoiceTurnRoundRef.current = 0;
  }, []);
  const resetConversationVoiceState = useCallback(() => {
    cancelIdleContinuation();
    idleContinuationSentRef.current = false;
    companionRoundRef.current = 0;
    companionArmedRef.current = false;
    setCompanionRound(0);
    pendingASREvidenceRef.current = null;
  }, [cancelIdleContinuation]);

  useEffect(() => () => {
    closingVoiceRef.current = true;
    voiceEntryControllerRef.current?.abort();
    stopListening(false);
    closeCaptureContext();
  }, [closeCaptureContext, stopListening]);

  return {
    cancelIdleContinuation,
    clearActiveVoiceTurn,
    clearPendingASREvidence,
    closeCaptureContext,
    closeVoiceEntry,
    companionRound,
    enterVoice,
    exitVoice,
    getPendingASREvidence,
    getVoiceContext,
    handleTtsPlaybackComplete,
    isVoiceOpen,
    markVoiceThinking,
    openVoiceEntry,
    publishPlaybackState: publishTtsPlaybackState,
    recordCompanionRound,
    resetConversationVoiceState,
    resetIdleContinuation,
    retryVoice,
    scheduleIdleContinuation,
    setCompanionRound,
    setIdleContinuationSent,
    setVoiceEntryMode,
    setVoiceEntryScene,
    setVoiceInputLocked,
    startVoiceFromEntry,
    stopListening,
    updateVoice,
    useBrowserVoiceFallback,
    voice,
    voiceEntryBusy,
    voiceEntryError,
    voiceEntryMode,
    voiceEntryScene,
  };
}

import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { consumeResumableEventStream, request } from "./api";
import {
  estimateDeliveredPrefix,
  hasSpeakableContent,
  SpeechSegmenter,
  stripLeadingTtsFiller,
} from "./speech";
import {
  CharacterLibrary,
  CharacterPicker,
  DrawWorkshop,
  ModeLobby,
} from "./CharacterExperience";
import type { AppView } from "./CharacterExperience";
import { ScenePickerPage, sceneAssetPath } from "./SceneExperience";
import type {
  AvatarConfig,
  AvatarEntry,
  ASRVocabularyEntry,
  ASRVocabularySnapshot,
  DiagnosticReport,
  InspectorEvent,
  InspectorTab,
  InitiativeTrigger,
  KnowledgeItem,
  MemoryItem,
  Message,
  ProductSettings,
  ProfileCardData,
  ProfileHistoryItem,
  PromptInspection,
  Role,
  SessionDocument,
  SessionSummary,
  StreamEnvelope,
  VoicePhase,
  VoiceDeliveryState,
  VoiceInteractionContext,
  VoiceInteractionMode,
  VoiceSessionState,
  CharacterRecord,
  CharacterSummary,
  ConversationScene,
} from "./types";

type ModalName = "settings" | "knowledge" | "memory" | "profile" | "diagnostics" | "voice-entry" | null;

interface SpeechQueueItem {
  id: string;
  text: string;
  voiceCue: string;
  prepared?: Promise<PCMStreamHandle>;
  retries?: number;
}

interface PCMStreamHandle {
  sampleRate: number;
  chunks: ArrayBuffer[];
  done: boolean;
  error: Error | null;
  waiters: Set<() => void>;
  pump: Promise<void>;
  totalInputSamples: number;
  cancel: () => void;
}

interface VoiceCaptureGraph {
  stream: MediaStream;
  context: AudioContext;
  source: MediaStreamAudioSourceNode;
  worklet: AudioWorkletNode;
  monitor: GainNode;
}

interface WarmVoiceCapture {
  graph: VoiceCaptureGraph;
  expiresAt: number;
  timer: number;
}

const DEFAULT_AVATARS: AvatarConfig = {
  user: { src: "/assets/avatar-user-default.webp", aspect: "2 / 3", scale: 1.08, x: -12, y: 0 },
  assistant: { src: "/assets/avatar-ai-default.webp", aspect: "2 / 3", scale: 1, x: 0, y: 0 },
};

const VOICE_LABELS: Record<VoicePhase, string> = {
  idle: "准备开始",
  preparing: "正在准备麦克风",
  connecting: "正在连接语音服务",
  listening: "我在听，请说话",
  "user-speaking": "正在聆听",
  collecting: "已收到，等待你继续说",
  deferred: "已听到，等回应结束后发送",
  transcribing: "正在确认你说的话",
  thinking: "正在思考并流式回复",
  "assistant-speaking": "正在回应你",
  "candidate-interruption": "听到声音，正在确认",
  interrupted: "已打断，继续说吧",
  error: "语音服务暂时不可用",
};

const uid = () => crypto.randomUUID();

interface ConfirmationOptions {
  title: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  danger?: boolean;
}

function styledConfirm(options: ConfirmationOptions): Promise<boolean> {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "confirmation-backdrop";
    const card = document.createElement("section");
    card.className = `confirmation-card${options.danger ? " danger" : ""}`;
    card.setAttribute("role", "alertdialog");
    card.setAttribute("aria-modal", "true");

    const mark = document.createElement("span");
    mark.className = "confirmation-mark";
    mark.textContent = options.danger ? "!" : "◇";
    const copy = document.createElement("div");
    copy.className = "confirmation-copy";
    const kicker = document.createElement("small");
    kicker.textContent = options.danger ? "需要确认" : "确认操作";
    const title = document.createElement("h2");
    title.textContent = options.title;
    const message = document.createElement("p");
    message.textContent = options.message;
    copy.append(kicker, title, message);
    if (options.detail) {
      const detail = document.createElement("span");
      detail.textContent = options.detail;
      copy.append(detail);
    }

    const actions = document.createElement("footer");
    const cancel = document.createElement("button");
    cancel.className = "confirmation-cancel";
    cancel.textContent = "取消";
    const confirm = document.createElement("button");
    confirm.className = "confirmation-accept";
    confirm.textContent = options.confirmLabel || "继续";
    actions.append(cancel, confirm);
    card.append(mark, copy, actions);
    backdrop.append(card);

    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", onKeyDown);
      backdrop.remove();
      resolve(value);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") finish(false);
    };
    cancel.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
    backdrop.addEventListener("mousedown", (event) => {
      if (event.target === backdrop) finish(false);
    });
    document.addEventListener("keydown", onKeyDown);
    document.body.append(backdrop);
    window.requestAnimationFrame(() => {
      backdrop.classList.add("visible");
      confirm.focus();
    });
  });
}

// A recovered SSE stream is historical UI state. It must never become a new
// audio job: restoring a page must not make the companion read an old answer.
export function shouldSynthesizeStreamEvent(isRecoveryReplay: boolean, processRecovery = false): boolean {
  return !isRecoveryReplay && !processRecovery;
}

// Fetch stream chunk boundaries are arbitrary. PCM16 samples are two bytes, so
// a lone trailing byte must be carried into the next network chunk instead of
// being silently dropped (which would misalign every following sample).
export function alignPCM16Chunk(carry: Uint8Array, incoming: Uint8Array): {
  pcm: Uint8Array<ArrayBuffer>;
  remainder: Uint8Array<ArrayBuffer>;
} {
  const merged = new Uint8Array(carry.byteLength + incoming.byteLength);
  merged.set(carry);
  merged.set(incoming, carry.byteLength);
  const alignedLength = merged.byteLength - (merged.byteLength % 2);
  const pcm = new Uint8Array(alignedLength);
  const remainder = new Uint8Array(merged.byteLength - alignedLength);
  pcm.set(merged.subarray(0, alignedLength));
  remainder.set(merged.subarray(alignedLength));
  return {
    pcm,
    remainder,
  };
}
const ACTIVE_RUN_STORAGE_KEY = "mindspace.active_run";
const ADULT_MODE_STORAGE_KEY = "mindspace.r18_enhanced";
const NSFW_ADULT_CONFIRMED_STORAGE_KEY = "mindspace.nsfw_adult_confirmed";
const R18_STYLE_STORAGE_KEY = "mindspace.r18_style";
const VOICE_INPUT_DEVICE_STORAGE_KEY = "mindspace.voice_input_device";
const VOICE_CAPTURE_RECOVERY_STORAGE_KEY = "mindspace.voice_capture_recovery";
const REQUEST_TIMEOUT_MS = 10_000;
const VOICE_ENTRY_PERSIST_TIMEOUT_MS = 5_000;
const TTS_RESPONSE_TIMEOUT_MS = 15_000;
const TTS_FIRST_PCM_TIMEOUT_MS = 8_000;
const TTS_PLAYBACK_START_TIMEOUT_MS = 1_500;
const TTS_STREAM_IDLE_TIMEOUT_MS = 15_000;
const TTS_PLAYBACK_END_GRACE_MS = 8_000;
// Keep a little headroom for providers whose PCM has already been normalized
// close to full scale. This is a safety ceiling, not a voice-style control.
const TTS_SAFE_OUTPUT_GAIN = 0.72;
const TTS_READY_WAIT_LIMIT_MS = 90_000;
const TTS_READY_POLL_MS = 2_000;
const VOICE_RECONNECT_DELAYS_MS = [250, 750, 1500, 3000] as const;
const VOICE_TRANSPORT_READY_TIMEOUT_MS = 3_000;
const VOICE_NATIVE_CAPTURE_STATUS_TIMEOUT_MS = 2_000;
const VOICE_DEVICE_ENUMERATION_TIMEOUT_MS = 800;
const VOICE_DEVICE_REQUEST_TIMEOUT_MS = 4_000;
const VOICE_CAPTURE_READY_TIMEOUT_MS = 3_000;
const VOICE_CAPTURE_STALL_TIMEOUT_MS = 10_000;
const VOICE_PENDING_PCM_FRAMES = 12;
const VOICE_CAPTURE_WARM_GRACE_MS = 15_000;
// Do not negotiate Chromium DSP here. Sensitivity and speech arbitration belong
// to the resident ASR/VAD pipeline. A concrete physical endpoint is selected at
// runtime so Windows does not have to resolve the mutable `default` alias while
// the same USB headset is also being opened for playback.
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

interface ActiveRunRecord {
  run_id: string;
  session_id: string;
  round: number;
  user_content: string;
  started_at: string;
}

function readActiveRun(): ActiveRunRecord | null {
  try {
    const value = JSON.parse(localStorage.getItem(ACTIVE_RUN_STORAGE_KEY) || "null") as ActiveRunRecord | null;
    return value?.run_id && value.session_id ? value : null;
  } catch {
    localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
    return null;
  }
}

function clearActiveRun(runId = "") {
  const active = readActiveRun();
  if (!runId || active?.run_id === runId) localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
}

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? (value as Record<string, unknown>) : {};
const bool = (value: unknown) => Boolean(value);
const num = (value: unknown, fallback = 0) =>
  Number.isFinite(Number(value)) ? Number(value) : fallback;
const str = (value: unknown) => String(value ?? "");

// Qwen receives one complete reply per turn. Its CustomVoice sampling context
// is therefore never reset at sentence boundaries.
export function shouldBufferQwenReplyForSinglePass(settings: ProductSettings | null | undefined, voiceOpen: boolean): boolean {
  return settings?.audio.tts_provider === "qwen3-vllm"
    && (voiceOpen || bool(settings.audio.auto_tts));
}

export function shouldFollowConversationScroll(distanceFromBottom: number, threshold = 180): boolean {
  return Number.isFinite(distanceFromBottom)
    && distanceFromBottom <= Math.max(0, threshold);
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
  const interaction = settings?.interaction || {};
  const configuredMode = str(interaction.voice_entry_mode);
  return {
    mode: configuredMode === "face_to_face" ? "face_to_face" : "call",
    scene: str(interaction.face_to_face_scene).trim().slice(0, 2000),
  };
}

export function companionContinuationPlan(
  interaction: Record<string, unknown>,
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

export function shouldSkipSpeechSegmentFailure(text: string, error: unknown) {
  if (!hasSpeakableContent(text)) return true;
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /没有可朗读的正文内容|请输入有效文本/.test(message);
}

function waitWithSignal(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Cancelled", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
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

function normalizeAvatarConfig(value: unknown): AvatarConfig {
  const raw = asRecord(value);
  const normalize = (role: Role): AvatarEntry => {
    const entry = asRecord(raw[role]);
    const fallback = DEFAULT_AVATARS[role];
    const aspect = str(entry.aspect || fallback.aspect);
    return {
      src: str(entry.src || fallback.src),
      aspect: (["2 / 3", "3 / 4", "4 / 5", "9 / 16", "1 / 1"].includes(aspect) ? aspect : fallback.aspect) as AvatarEntry["aspect"],
      scale: Math.max(0.6, Math.min(3, num(entry.scale, fallback.scale))),
      x: Math.max(-80, Math.min(80, num(entry.x, fallback.x))),
      y: Math.max(-80, Math.min(80, num(entry.y, fallback.y))),
    };
  };
  return { user: normalize("user"), assistant: normalize("assistant") };
}

function formatTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? ""
    : new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

function encodeMonoWav(samples: Float32Array, sampleRate: number) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  write(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); write(8, "WAVE");
  write(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => view.setInt16(44 + index * 2, Math.round(Math.max(-1, Math.min(1, sample)) * 32767), true));
  return buffer;
}

async function normalizeReferenceAudio(file: File) {
  const context = new AudioContext();
  let decoded: AudioBuffer;
  try { decoded = await context.decodeAudioData(await file.arrayBuffer()); } finally { await context.close(); }
  if (decoded.duration < 0.2) throw new Error("参考音频过短，至少需要 0.2 秒");
  if (decoded.duration > 120) throw new Error("参考音频过长，请裁剪到 120 秒以内");
  const sampleRate = 16000;
  const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * sampleRate), sampleRate);
  const source = offline.createBufferSource(); source.buffer = decoded; source.connect(offline.destination); source.start();
  const rendered = await offline.startRendering();
  const name = `${file.name.replace(/\.[^.]+$/, "") || "reference"}.wav`;
  return new File([encodeMonoWav(rendered.getChannelData(0), sampleRate)], name, { type: "audio/wav" });
}

function friendlyValue(value: unknown): string {
  if (value == null || value === "") return "暂无";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(friendlyValue).join("、") || "暂无";
  return Object.entries(asRecord(value)).map(([key, item]) => `${key}：${friendlyValue(item)}`).join("；") || "暂无";
}

function richText(text: string) {
  const parts = text.split(/(```[\s\S]*?```|`[^`]+`)/g);
  return parts.map((part, index) => {
    if (part.startsWith("```") && part.endsWith("```")) return <pre key={index}><code>{part.slice(3, -3).trim()}</code></pre>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    const lines = part.split("\n");
    return lines.map((line, lineIndex) => <span key={`${index}-${lineIndex}`}>{line}{lineIndex < lines.length - 1 && <br />}</span>);
  });
}

function avatarStyle(entry: AvatarEntry): CSSProperties {
  return {
    "--avatar-aspect": entry.aspect,
    "--avatar-scale": entry.scale,
    "--avatar-x": `${entry.x}%`,
    "--avatar-y": `${entry.y}%`,
  } as CSSProperties;
}

function PortraitAvatar({ role, avatars, label, onClick, className = "" }: {
  role: Role;
  avatars: AvatarConfig;
  label: string;
  onClick?: () => void;
  className?: string;
}) {
  const entry = avatars[role];
  return <button type="button" className={`portrait-avatar ${className}`} style={avatarStyle(entry)} onClick={onClick} title={`查看${label}人物卡`} aria-label={`查看${label}人物卡`}><img src={entry.src} alt={`${label}头像`} /></button>;
}

function NsfwAdultConfirmation({ seconds, onCancel, onConfirm }: {
  seconds: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return <div className="modal-backdrop nsfw-confirmation-backdrop">
    <section className="nsfw-confirmation" role="dialog" aria-modal="true" aria-labelledby="nsfw-confirmation-title">
      <span>NSFW</span>
      <h2 id="nsfw-confirmation-title">请先确认你已成年</h2>
      <p>开启后将允许成人主题内容。此确认只显示一次，确认结果仅保存在本机。</p>
      <div>
        <button className="secondary" onClick={onCancel}>取消</button>
        <button className="primary" onClick={onConfirm} disabled={seconds > 0}>
          {seconds > 0 ? `请等待 ${seconds} 秒` : "我确认已成年并开启"}
        </button>
      </div>
    </section>
  </div>;
}

function App() {
  const [settings, setSettings] = useState<ProductSettings | null>(null);
  const [avatars, setAvatars] = useState<AvatarConfig>(DEFAULT_AVATARS);
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);
  const [activeCharacterId, setActiveCharacterId] = useState("");
  const [activeActivitySessionId, setActiveActivitySessionId] = useState("");
  const [conversationScene, setConversationScene] = useState<ConversationScene | null>(null);
  const [appView, setAppView] = useState<AppView>("modes");
  const [characterPickerOpen, setCharacterPickerOpen] = useState(false);
  const [characterPickerScope, setCharacterPickerScope] = useState<"all" | "custom">("all");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState(localStorage.getItem("mindspace.session") || uid());
  const [settingsInitialTab, setSettingsInitialTab] = useState("model");
  const [messages, setMessages] = useState<Message[]>([]);
  const [round, setRound] = useState(1);
  const [input, setInput] = useState("");
  const [adultMode, setAdultMode] = useState(
    () => localStorage.getItem(ADULT_MODE_STORAGE_KEY) === "1"
      && localStorage.getItem(NSFW_ADULT_CONFIRMED_STORAGE_KEY) === "1",
  );
  const [nsfwConfirmationOpen, setNsfwConfirmationOpen] = useState(false);
  const [nsfwConfirmationSeconds, setNsfwConfirmationSeconds] = useState(3);
  const [r18StyleId, setR18StyleId] = useState(
    () => localStorage.getItem(R18_STYLE_STORAGE_KEY) || "high_intensity",
  );
  const [search, setSearch] = useState("");
  const [runId, setRunId] = useState("");
  const [initialDataLoaded, setInitialDataLoaded] = useState(false);
  const [inspectionRunId, setInspectionRunId] = useState("");
  const runIdRef = useRef("");
  const [generating, setGenerating] = useState(false);
  const conversationRef = useRef<HTMLElement | null>(null);
  const conversationTailRef = useRef<HTMLDivElement | null>(null);
  const followConversationRef = useRef(true);
  const pendingConversationJumpRef = useRef(false);
  const [modal, setModal] = useState<ModalName>(null);
  const [modalDirty, setModalDirty] = useState(false);
  const [profileCardRole, setProfileCardRole] = useState<Role | null>(null);
  const [profileEditorRole, setProfileEditorRole] = useState<Role | "state">("user");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("flow");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [events, setEvents] = useState<InspectorEvent[]>([]);
  const [retrieval, setRetrieval] = useState<Record<string, unknown>[]>([]);
  const [toast, setToast] = useState("");
  const [voice, setVoice] = useState<VoiceSessionState>({ open: false, phase: "idle", transcript: "", reply: "", level: 0, error: "" });
  const [voiceEntryMode, setVoiceEntryMode] = useState<VoiceInteractionMode>("call");
  const [voiceEntryScene, setVoiceEntryScene] = useState("");
  const [voiceEntryBusy, setVoiceEntryBusy] = useState(false);
  const [voiceEntryError, setVoiceEntryError] = useState("");
  const [companionRound, setCompanionRound] = useState(0);
  const voiceOpenRef = useRef(false);
  const voiceInteractionRef = useRef<VoiceInteractionContext>({ mode: "call", scene: "" });
  const companionRoundRef = useRef(0);
  const companionArmedRef = useRef(false);
  const activeInitiativeRef = useRef<{ trigger: InitiativeTrigger; sequence: number }>({ trigger: "none", sequence: 0 });
  const abortRef = useRef<AbortController | null>(null);
  const ttsControllersRef = useRef<Set<AbortController>>(new Set());
  const voiceSocketRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const micWorkletRef = useRef<AudioWorkletNode | null>(null);
  const silentMonitorRef = useRef<GainNode | null>(null);
  const warmVoiceCaptureRef = useRef<WarmVoiceCapture | null>(null);
  const captureContextRef = useRef<AudioContext | null>(null);
  const captureWorkletLoadedRef = useRef(false);
  const playbackContextRef = useRef<AudioContext | null>(null);
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
  const audioQueueRef = useRef<SpeechQueueItem[]>([]);
  const audioPlayingRef = useRef(false);
  const voiceInputLockedRef = useRef(false);
  const currentPlaybackNodeRef = useRef<AudioWorkletNode | null>(null);
  const currentPlaybackGainRef = useRef<GainNode | null>(null);
  const currentPlaybackDoneRef = useRef<(() => void) | null>(null);
  const currentSpeechRef = useRef<{ item: SpeechQueueItem; playedMs: number; totalMs: number; complete: boolean } | null>(null);
  const completedSpeechRef = useRef<string[]>([]);
  const voiceDeliveryRef = useRef<VoiceDeliveryState | null>(null);
  const voiceReplyRef = useRef("");
  const ttsVoiceCueRef = useRef("neutral");
  const currentAssistantIdRef = useRef("");
  const lastVoiceRunIdRef = useRef("");
  const ttsWorkletLoadedRef = useRef(false);
  const playbackGenerationRef = useRef(0);
  const speechSegmenterRef = useRef(new SpeechSegmenter());
  const ttsResponseStartedRef = useRef(false);
  const qwenFullReplySubmittedRef = useRef(false);
  const partialRenderRef = useRef(0);
  const voiceLevelRenderRef = useRef(0);
  const pendingResponseDeltaRef = useRef("");
  const responseFrameRef = useRef<number | null>(null);
  const closingVoiceRef = useRef(false);
  const idleTimerRef = useRef<number | null>(null);
  const idleContinuationSentRef = useRef(false);
  const voiceMergeTimerRef = useRef<number | null>(null);
  const voiceSegmentsRef = useRef<string[]>([]);
  const deferredVoiceSegmentsRef = useRef<string[]>([]);
  const activeVoiceTurnTextRef = useRef("");
  const activeVoiceTurnRoundRef = useRef(0);
  const pendingASREvidenceRef = useRef<{ uncertain_segments: Array<{ text: string; reason: string }>; decision_reasons: string[] } | null>(null);
  const lastBargeCommitAtRef = useRef(0);
  const bargeCommittedRef = useRef(false);
  const bargeBackoffRef = useRef({ level: 0, until: 0 });
  const recentVoiceTextsRef = useRef<Map<string, number>>(new Map());
  const queueVoiceSegmentRef = useRef<((text: string, deferred?: boolean) => void) | null>(null);
  const inputRef = useRef("");
  const generatingRef = useRef(false);
  const roundRef = useRef(1);
  const sendMessageRef = useRef<((text?: string, mode?: "primary" | "regenerate", targetRound?: number, initiative?: boolean, initiativeTrigger?: InitiativeTrigger, initiativeSequence?: number, initiativeSequenceLimit?: number) => Promise<void>) | null>(null);
  const llmMode = str(settings?.llm.mode || "openai");
  const llmBaseUrl = str(settings?.llm.base_url);
  const llmLocalEndpoint = /^https?:\/\/(?:127\.0\.0\.1|localhost)(?::|\/|$)/i.test(llmBaseUrl);
  const llmReady = llmMode === "openai" && (bool(settings?.llm.credentials_configured) || llmLocalEndpoint);
  const activeCharacter = useMemo(
    () => characters.find((item) => item.character_id === activeCharacterId),
    [activeCharacterId, characters],
  );
  const effectiveAvatars = useMemo<AvatarConfig>(() => ({
    ...avatars,
    assistant: activeCharacter?.avatar?.src ? activeCharacter.avatar : avatars.assistant,
  }), [activeCharacter, avatars]);

  const notify = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 3400);
  }, []);

  useEffect(() => {
    if (!nsfwConfirmationOpen || nsfwConfirmationSeconds <= 0) return;
    const timer = window.setTimeout(
      () => setNsfwConfirmationSeconds((current) => Math.max(0, current - 1)),
      1000,
    );
    return () => window.clearTimeout(timer);
  }, [nsfwConfirmationOpen, nsfwConfirmationSeconds]);

  const toggleAdultMode = useCallback(() => {
    if (adultMode) {
      setAdultMode(false);
      localStorage.setItem(ADULT_MODE_STORAGE_KEY, "0");
      notify("NSFW 已关闭");
      return;
    }
    if (localStorage.getItem(NSFW_ADULT_CONFIRMED_STORAGE_KEY) !== "1") {
      setNsfwConfirmationSeconds(3);
      setNsfwConfirmationOpen(true);
      return;
    }
    setAdultMode(true);
    localStorage.setItem(ADULT_MODE_STORAGE_KEY, "1");
    notify("NSFW 已开启");
  }, [adultMode, notify]);

  const confirmAdultMode = useCallback(() => {
    if (nsfwConfirmationSeconds > 0) return;
    localStorage.setItem(NSFW_ADULT_CONFIRMED_STORAGE_KEY, "1");
    localStorage.setItem(ADULT_MODE_STORAGE_KEY, "1");
    setAdultMode(true);
    setNsfwConfirmationOpen(false);
    notify("已确认成年，NSFW 已开启");
  }, [notify, nsfwConfirmationSeconds]);

  useEffect(() => { inputRef.current = input; }, [input]);
  useEffect(() => { generatingRef.current = generating; }, [generating]);
  useEffect(() => { roundRef.current = round; }, [round]);

  useLayoutEffect(() => {
    if (!messages.length || !followConversationRef.current) return;
    const viewport = conversationRef.current;
    const tail = conversationTailRef.current;
    if (!viewport || !tail) return;
    // Chat CSS normally uses smooth scrolling. New turns and streamed deltas
    // must instead land deterministically so a long history cannot leave the
    // active response hidden behind the composer.
    const previousBehavior = viewport.style.scrollBehavior;
    viewport.style.scrollBehavior = "auto";
    if (typeof tail.scrollIntoView === "function") {
      tail.scrollIntoView({ block: "end", behavior: "auto" });
    } else {
      viewport.scrollTop = viewport.scrollHeight;
    }
    viewport.style.scrollBehavior = previousBehavior;
    pendingConversationJumpRef.current = false;
  }, [messages]);

  const handleConversationScroll = useCallback(() => {
    const viewport = conversationRef.current;
    if (!viewport || pendingConversationJumpRef.current) return;
    const distanceFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    followConversationRef.current = shouldFollowConversationScroll(distanceFromBottom);
  }, []);

  const pauseConversationFollow = useCallback(() => {
    followConversationRef.current = false;
  }, []);

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
    const interaction = settings?.interaction || {};
    const continuous = mode === "voice" && bool(interaction.unlimited_reply_enabled);
    const companionPlan = continuous
      ? companionContinuationPlan(interaction, afterPlayback, companionRoundRef.current)
      : null;
    if (continuous && !companionPlan) return;
    cancelIdleContinuation();
    if (!continuous && (!bool(interaction.idle_continuation_enabled) || idleContinuationSentRef.current)) return;
    const delaySeconds = companionPlan
      ? companionPlan.delaySeconds
      : mode === "voice"
        ? num(interaction.voice_idle_seconds, 30)
        : num(interaction.text_idle_seconds, 180);
    idleTimerRef.current = window.setTimeout(() => {
      idleTimerRef.current = null;
      if (generatingRef.current || inputRef.current.trim()) return;
      if (mode === "voice" && (!voiceOpenRef.current || audioPlayingRef.current)) return;
      if (mode === "text" && voiceOpenRef.current) return;
      if (!continuous) idleContinuationSentRef.current = true;
      const nextSequence = companionPlan?.nextSequence || 0;
      void sendMessageRef.current?.(
        "",
        "primary",
        roundRef.current,
        true,
        continuous ? "continuous_companionship" : "idle_continuation",
        nextSequence,
        companionPlan?.limit || 0,
      );
    }, Math.max(1, delaySeconds) * 1000);
  }, [cancelIdleContinuation, settings]);

  const setPlaybackDucked = useCallback((ducked: boolean) => {
    const gain = currentPlaybackGainRef.current;
    const context = playbackContextRef.current;
    if (!gain || !context) return;
    gain.gain.cancelScheduledValues(context.currentTime);
    gain.gain.setTargetAtTime(ducked ? TTS_SAFE_OUTPUT_GAIN * 0.25 : TTS_SAFE_OUTPUT_GAIN, context.currentTime, 0.035);
  }, []);

  const publishPlaybackState = useCallback((playing: boolean) => {
    const socket = voiceSocketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      const now = performance.now();
      if (bargeBackoffRef.current.until <= now) bargeBackoffRef.current = { level: 0, until: 0 };
      socket.send(JSON.stringify({
        action: "playback_state",
        playing,
        playback_text: playing ? currentSpeechRef.current?.item.text || voiceReplyRef.current : "",
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

  const scheduleVoiceReconnect = useCallback((reason: string, phase: VoicePhase = "connecting") => {
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

  const captureVoiceInterruption = useCallback((cause = "confirmed_user_speech") => {
    const current = currentSpeechRef.current;
    const completed = completedSpeechRef.current.join("");
    const progress = current?.totalMs ? Math.min(1, current.playedMs / current.totalMs) : 0;
    const currentPrefix = current ? estimateDeliveredPrefix(current.item.text, progress) : "";
    const heardText = `${completed}${currentPrefix}`;
    const spokenText = [
      ...completedSpeechRef.current,
      ...(current ? [current.item.text] : []),
      ...audioQueueRef.current.map((item) => item.text),
    ].join("");
    const visibleText = voiceReplyRef.current.trim();
    const visibleHeardIndex = heardText ? visibleText.indexOf(heardText) : 0;
    const unheardText = visibleText && visibleHeardIndex >= 0
      ? visibleText.slice(visibleHeardIndex + heardText.length).trim()
      : spokenText.slice(Math.min(heardText.length, spokenText.length));
    voiceDeliveryRef.current = {
      mode: "voice",
      run_id: lastVoiceRunIdRef.current,
      assistant_message_id: currentAssistantIdRef.current,
      delivery_status: "interrupted",
      current_segment_id: current?.item.id || "",
      played_audio_ms: Math.max(0, Math.round(current?.playedMs || 0)),
      heard_text: heardText,
      unheard_text: unheardText || (heardText ? "" : visibleText),
      full_text_visible: Boolean(visibleText),
      position_confidence: current ? (current.complete ? 0.86 : 0.66) : 0.95,
      interruption_cause: cause,
    };
  }, []);

  const loadSessions = useCallback(async () => {
    const result = await request<{ sessions: SessionSummary[] }>("/api/v1/sessions");
    setSessions(result.sessions);
  }, []);

  const loadCharacters = useCallback(async () => {
    const result = await request<{ items: CharacterSummary[] }>("/api/v1/characters");
    setCharacters(result.items);
    return result.items;
  }, []);

  const navigate = useCallback((view: AppView) => {
    setAppView(view);
    const path = view === "modes"
      ? "/modes"
      : view === "draw"
        ? "/draw"
        : view === "characters"
          ? "/characters"
          : view === "scenes"
            ? `/chat/${sessionId}/scenes`
            : `/chat/${sessionId}`;
    window.history.replaceState(null, "", `#${path}`);
  }, [sessionId]);

  const loadConversationScene = useCallback(async (id: string) => {
    try {
      const scene = await request<ConversationScene>(
        `/api/v1/sessions/${encodeURIComponent(id)}/scene`,
      );
      setConversationScene(scene);
      return scene;
    } catch {
      setConversationScene(null);
      return null;
    }
  }, []);

  const openSession = useCallback(async (id: string) => {
    cancelIdleContinuation();
    idleContinuationSentRef.current = false;
    companionRoundRef.current = 0;
    companionArmedRef.current = false;
    setCompanionRound(0);
    voiceDeliveryRef.current = null;
    const value = await request<SessionDocument>(`/api/v1/sessions/${encodeURIComponent(id)}`);
    setSessionId(id);
    localStorage.setItem("mindspace.session", id);
    setActiveCharacterId(value.character_id || value.character?.character_id || "");
    setActiveActivitySessionId("");
    void loadConversationScene(id);
    const loadedMessages = value.messages || [];
    const sameSession = id === sessionId;
    const recovering = readActiveRun()?.session_id === id;
    setMessages((current) =>
      (sameSession || recovering) && current.length ? current : loadedMessages
    );
    idleContinuationSentRef.current = loadedMessages.at(-1)?.initiative_trigger === "idle_continuation";
    setRound(Math.max(0, ...loadedMessages.map((item) => item.round || 0)) + 1);
    setSidebarOpen(false);
    setAppView("chat");
    window.history.replaceState(null, "", `#/chat/${id}`);
  }, [cancelIdleContinuation, loadConversationScene, sessionId]);

  useEffect(() => {
    Promise.all([
      request<ProductSettings>("/api/v1/settings"),
      request<{ sessions: SessionSummary[] }>("/api/v1/sessions"),
      request<AvatarConfig>("/api/v1/avatar/config"),
      request<{ items: CharacterSummary[] }>("/api/v1/characters"),
    ]).then(async ([config, sessionResult, avatarResult, characterResult]) => {
      setSettings(config);
      setSessions(sessionResult.sessions);
      setAvatars(normalizeAvatarConfig(avatarResult));
      setCharacters(characterResult.items);
      const activeCharacterIds = new Set(
        characterResult.items
          .filter((item) => item.status === "active")
          .map((item) => item.character_id),
      );
      const usableSessions = sessionResult.sessions.filter((item) =>
        !item.character_id || activeCharacterIds.has(item.character_id),
      );
      const rememberedId = localStorage.getItem("mindspace.session");
      const preferred = usableSessions.find((item) => item.session_id === rememberedId)
        || usableSessions[0];
      if (preferred) await openSession(preferred.session_id);
      else {
        setAppView("modes");
        window.history.replaceState(null, "", "#/modes");
      }
      setInitialDataLoaded(true);
    }).catch((error: Error) => notify(error.message));
  }, [notify]);

  useEffect(() => {
    if (!initialDataLoaded) return;
    let disposed = false;
    const synchronizeAudioSelection = async () => {
      if (modal === "settings" && modalDirty) return;
      try {
        const latest = await request<ProductSettings>("/api/v1/settings");
        if (disposed) return;
        setSettings((current) => {
          if (!current) return latest;
          const currentSelection = JSON.stringify({
            provider: current.audio.tts_provider,
            gpt: current.audio.tts_gpt_sovits_voice,
            qwen: current.audio.tts_qwen3_vllm_voice,
            auto: current.audio.auto_tts,
          });
          const latestSelection = JSON.stringify({
            provider: latest.audio.tts_provider,
            gpt: latest.audio.tts_gpt_sovits_voice,
            qwen: latest.audio.tts_qwen3_vllm_voice,
            auto: latest.audio.auto_tts,
          });
          return currentSelection === latestSelection
            ? current
            : { ...current, audio: { ...current.audio, ...latest.audio } };
        });
      } catch {
        // Core health and the existing error surfaces remain authoritative.
      }
    };
    const timer = window.setInterval(() => { void synchronizeAudioSelection(); }, 2500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [initialDataLoaded, modal, modalDirty]);

  useEffect(() => {
    const requested = str(settings?.appearance.theme || "mindscape");
    document.documentElement.dataset.theme = requested === "dark" ? "dark" : "mindscape";
    document.documentElement.dataset.density = str(settings?.appearance.density || "chat");
    const configuredScale = Math.max(1, Math.min(1.6, num(settings?.appearance.font_scale, 1.3)));
    const applyTypography = () => {
      const viewportBonus = window.innerWidth >= 1900 && window.innerHeight >= 900
        ? 0.14
        : window.innerWidth >= 1500 && window.innerHeight >= 820 ? 0.08 : 0;
      const effectiveScale = Math.min(1.78, configuredScale + viewportBonus);
      document.documentElement.style.fontSize = `${16 * effectiveScale}px`;
      document.documentElement.dataset.viewportTypography = viewportBonus ? "expanded" : "normal";
    };
    applyTypography();
    window.addEventListener("resize", applyTypography);
    return () => window.removeEventListener("resize", applyTypography);
  }, [settings]);

  const stopAudio = useCallback(() => {
    publishPlaybackState(false);
    playbackGenerationRef.current += 1;
    audioQueueRef.current = [];
    speechSegmenterRef.current.reset();
    ttsResponseStartedRef.current = false;
    qwenFullReplySubmittedRef.current = false;
    ttsControllersRef.current.forEach((controller) => controller.abort());
    ttsControllersRef.current.clear();
    currentPlaybackNodeRef.current?.port.postMessage({ type: "stop" });
    currentPlaybackNodeRef.current?.disconnect();
    currentPlaybackNodeRef.current = null;
    currentPlaybackGainRef.current?.disconnect();
    currentPlaybackGainRef.current = null;
    currentPlaybackDoneRef.current?.();
    currentPlaybackDoneRef.current = null;
    audioPlayingRef.current = false;
    currentSpeechRef.current = null;
    completedSpeechRef.current = [];
    setVoice((current) => ({ ...current, level: 0 }));
  }, [publishPlaybackState]);

  const closePlaybackContext = useCallback(() => {
    const context = playbackContextRef.current;
    playbackContextRef.current = null;
    ttsWorkletLoadedRef.current = false;
    if (context) {
      context.onstatechange = null;
      if (context.state !== "closed") void context.close().catch(() => undefined);
    }
  }, []);

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

  const playbackAudioContext = useCallback(async () => {
    let context = playbackContextRef.current;
    if (!context || context.state === "closed") {
      context = new AudioContext({ latencyHint: "interactive" });
      playbackContextRef.current = context;
      ttsWorkletLoadedRef.current = false;
      context.onstatechange = () => {
        if (audioPlayingRef.current && context?.state !== "running" && voiceOpenRef.current) {
          currentPlaybackDoneRef.current?.();
          setVoice((current) => ({
            ...current,
            // Playback failure must not make a healthy ASR session look dead.
            // The microphone remains usable while the user decides whether to
            // retry speech playback.
            phase: "listening",
            error: "系统暂停了声音播放，请点击“恢复语音”",
            level: 0,
          }));
        }
      };
    }
    if (context.state !== "running") await context.resume();
    if (context.state !== "running") throw new Error("声音播放尚未解锁，请重新点击开始通话");
    return context;
  }, []);

  const playbackContext = useCallback(async () => {
    const context = await playbackAudioContext();
    if (!ttsWorkletLoadedRef.current) {
      await context.audioWorklet.addModule("/assets/tts-playback-worklet.js");
      ttsWorkletLoadedRef.current = true;
    }
    return context;
  }, [playbackAudioContext]);

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

  const prepareSpeech = useCallback((item: SpeechQueueItem) => {
    if (item.prepared) return item.prepared;
    const controller = new AbortController();
    ttsControllersRef.current.add(controller);
    item.prepared = (async () => {
      const speed = num(settings?.audio.tts_speed, 1);
      const readyStartedAt = performance.now();
      while (true) {
        const status = await requestWithTimeout<{
          tts_ready?: boolean;
          tts_error?: string;
        }>("/api/v1/audio/status", { signal: controller.signal }, 3_000);
        if (status.tts_ready) break;
        if (performance.now() - readyStartedAt >= TTS_READY_WAIT_LIMIT_MS) {
          throw new Error(str(status.tts_error || "语音合成服务启动超时"));
        }
        if (voiceOpenRef.current) {
          setVoice((current) => ({
            ...current,
            // TTS is a downstream consumer. Waiting for it must never replace
            // the independent ASR transport state with "connecting".
            error: str(status.tts_error || "回复已生成，语音播放正在准备"),
          }));
        }
        await waitWithSignal(TTS_READY_POLL_MS, controller.signal);
      }
      const responseTimeout = window.setTimeout(() => controller.abort("tts_response_timeout"), TTS_RESPONSE_TIMEOUT_MS);
      let response: Response;
      try {
        response = await fetch("/api/v1/audio/tts/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: item.text,
            // All segments from one answer share the run id.  AudioService now
            // tracks a set of tasks per id, so one interrupt cancels the whole
            // spoken answer without later segments overwriting earlier ones.
            request_id: runIdRef.current || item.id,
            speed,
            // Acoustic instructions belong exclusively to Qwen3 CustomVoice.
            // GPT-SoVITS and CosyVoice consume only the streamed spoken text.
            ...(settings?.audio.tts_provider === "qwen3-vllm"
              ? { voice_cue: item.voiceCue }
              : {}),
          }),
          signal: controller.signal,
        });
      } catch (error) {
        if (controller.signal.aborted) {
          if (controller.signal.reason === "tts_response_timeout") {
            throw new Error("语音合成响应超时");
          }
          throw new DOMException("Cancelled", "AbortError");
        }
        throw error;
      } finally {
        window.clearTimeout(responseTimeout);
      }
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(str(detail.detail || "语音合成失败"));
      }
      if (!response.body) throw new Error("浏览器不支持流式语音响应");
      const handle: PCMStreamHandle = {
        sampleRate: num(response.headers.get("X-Audio-Sample-Rate"), 24000),
        chunks: [], done: false, error: null, waiters: new Set(), pump: Promise.resolve(), totalInputSamples: 0,
        cancel: () => controller.abort("tts_playback_cancelled"),
      };
      const wake = () => {
        handle.waiters.forEach((resolve) => resolve());
        handle.waiters.clear();
      };
      const reader = response.body.getReader();
      handle.pump = (async () => {
        let pcmRemainder = new Uint8Array(0);
        try {
          while (true) {
            let idleTimer: number | null = null;
            const stalled = new Promise<never>((_resolve, reject) => {
              idleTimer = window.setTimeout(() => {
                controller.abort("tts_stream_idle_timeout");
                reject(new Error("语音合成流长时间没有继续返回数据"));
              }, TTS_STREAM_IDLE_TIMEOUT_MS);
            });
            let packet: ReadableStreamReadResult<Uint8Array>;
            try {
              packet = await Promise.race([reader.read(), stalled]);
            } finally {
              if (idleTimer != null) window.clearTimeout(idleTimer);
            }
            const { value, done } = packet;
            if (done) break;
            if (value?.byteLength) {
              const aligned = alignPCM16Chunk(pcmRemainder, value);
              pcmRemainder = aligned.remainder;
              if (aligned.pcm.byteLength) {
                handle.totalInputSamples += aligned.pcm.byteLength / 2;
                handle.chunks.push(aligned.pcm.buffer);
                wake();
              }
            }
          }
        } catch (error) {
          handle.error = error as Error;
        } finally {
          handle.done = true;
          wake();
          ttsControllersRef.current.delete(controller);
        }
      })();
      return handle;
    })().catch((error) => {
      ttsControllersRef.current.delete(controller);
      throw error;
    });
    return item.prepared;
  }, [settings]);

  const playPCMStream = useCallback(async (item: SpeechQueueItem, handle: PCMStreamHandle, generation: number) => {
    if (!handle.chunks.length && !handle.done) {
      await new Promise<void>((resolve, reject) => {
        const onActivity = () => {
          window.clearTimeout(timeout);
          handle.waiters.delete(onActivity);
          resolve();
        };
        const timeout = window.setTimeout(() => {
          handle.waiters.delete(onActivity);
          handle.cancel();
          reject(new Error("语音合成已连接，但长时间没有返回音频"));
        }, TTS_FIRST_PCM_TIMEOUT_MS);
        handle.waiters.add(onActivity);
      });
    }
    if (handle.done && handle.totalInputSamples <= 0) {
      throw new Error("语音合成返回了空音频");
    }
    const context = await playbackContext();
    if (generation !== playbackGenerationRef.current) return;
    const node = new AudioWorkletNode(context, "mindspace-tts-playback", {
      numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1],
    });
    currentPlaybackNodeRef.current = node;
    const gain = context.createGain();
    currentPlaybackGainRef.current = gain;
    gain.gain.value = TTS_SAFE_OUTPUT_GAIN;
    node.connect(gain);
    gain.connect(context.destination);
    node.port.postMessage({ type: "configure", sampleRate: handle.sampleRate, prebufferMs: 120 });
    let resolveEnded: () => void = () => undefined;
    const ended = new Promise<void>((resolve) => { resolveEnded = resolve; });
    currentPlaybackDoneRef.current = resolveEnded;
    currentSpeechRef.current = { item, playedMs: 0, totalMs: 0, complete: false };
    let playbackStarted = false;
    let playbackStartError: Error | null = null;
    let playbackStartTimer: number | null = null;
    node.port.onmessage = (event: MessageEvent<{ type: string; value?: number; playedFrames?: number; outputSampleRate?: number }>) => {
      if (event.data.type === "started") {
        playbackStarted = true;
        if (playbackStartTimer != null) window.clearTimeout(playbackStartTimer);
        if (voiceOpenRef.current) {
          publishPlaybackState(true);
          setVoice((current) => ({ ...current, phase: "assistant-speaking", error: "" }));
        }
      } else if (event.data.type === "level") {
        const playedMs = num(event.data.playedFrames) / Math.max(1, num(event.data.outputSampleRate, context.sampleRate)) * 1000;
        const receivedMs = handle.totalInputSamples / Math.max(1, handle.sampleRate) * 1000;
        const estimatedMs = Math.max(receivedMs, item.text.length * 180);
        currentSpeechRef.current = { item, playedMs, totalMs: estimatedMs, complete: handle.done };
        const now = performance.now();
        if (now - voiceLevelRenderRef.current >= 50) {
          voiceLevelRenderRef.current = now;
          setVoice((current) => ({ ...current, level: num(event.data.value) }));
        }
      } else if (event.data.type === "ended") {
        resolveEnded();
      }
    };
    try {
      while (generation === playbackGenerationRef.current) {
        while (handle.chunks.length) {
          const chunk = handle.chunks.shift()!;
          node.port.postMessage({ type: "push", pcm: chunk }, [chunk]);
          if (playbackStartTimer == null && !playbackStarted) {
            playbackStartTimer = window.setTimeout(() => {
              if (playbackStarted || generation !== playbackGenerationRef.current) return;
              playbackStartError = new Error("音频数据已到达，但播放器没有启动");
              handle.cancel();
              resolveEnded();
            }, TTS_PLAYBACK_START_TIMEOUT_MS);
          }
        }
        if (handle.done) break;
        await new Promise<void>((resolve) => handle.waiters.add(resolve));
      }
      if (generation !== playbackGenerationRef.current) return;
      if (playbackStartError) throw playbackStartError;
      if (handle.error) throw handle.error;
      if (handle.totalInputSamples <= 0) throw new Error("语音合成返回了空音频");
      node.port.postMessage({ type: "end" });
      const expectedPlaybackMs = handle.totalInputSamples / Math.max(1, handle.sampleRate) * 1000;
      let endTimer: number | null = null;
      try {
        await Promise.race([
          ended,
          new Promise<never>((_resolve, reject) => {
            endTimer = window.setTimeout(
              () => reject(new Error("语音播放器结束等待超时")),
              Math.max(TTS_PLAYBACK_END_GRACE_MS, expectedPlaybackMs + TTS_PLAYBACK_END_GRACE_MS),
            );
          }),
        ]);
      } finally {
        if (endTimer != null) window.clearTimeout(endTimer);
      }
      if (playbackStartError) throw playbackStartError;
      if (!playbackStarted) throw new Error("音频播放器未能启动");
    } finally {
      if (playbackStartTimer != null) window.clearTimeout(playbackStartTimer);
      node.port.postMessage({ type: "stop" });
      node.disconnect();
      gain.disconnect();
      if (currentPlaybackNodeRef.current === node) currentPlaybackNodeRef.current = null;
      if (currentPlaybackGainRef.current === gain) currentPlaybackGainRef.current = null;
      if (currentPlaybackDoneRef.current === resolveEnded) currentPlaybackDoneRef.current = null;
    }
  }, [playbackContext, publishPlaybackState]);

  const playQueue = useCallback(async () => {
    if (audioPlayingRef.current || !audioQueueRef.current.length) return;
    audioPlayingRef.current = true;
    const generation = playbackGenerationRef.current;
    let playbackFailed = false;
    while (audioQueueRef.current.length) {
      const item = audioQueueRef.current[0];
      if (!hasSpeakableContent(item.text)) {
        audioQueueRef.current.shift();
        continue;
      }
      try {
        const stream = await prepareSpeech(item);
        if (generation !== playbackGenerationRef.current) return;
        audioQueueRef.current.shift();
        await playPCMStream(item, stream, generation);
        if (generation === playbackGenerationRef.current) completedSpeechRef.current.push(item.text);
      } catch (error) {
        if (shouldSkipSpeechSegmentFailure(item.text, error)) {
          if (audioQueueRef.current[0] === item) audioQueueRef.current.shift();
          continue;
        }
        // A broken segment must not poison the whole reply.  Reissue only the
        // current segment once; the scheduler never overlaps local inference.
        if (generation === playbackGenerationRef.current && (item.retries || 0) < 1) {
          item.retries = (item.retries || 0) + 1;
          item.prepared = undefined;
          continue;
        }
        playbackFailed = true;
        if (audioQueueRef.current[0] === item) audioQueueRef.current.shift();
        if ((error as Error).name !== "AbortError") {
          const message = (error as Error).message;
          setVoiceInputLocked(false, "tts_failed");
          if (voiceOpenRef.current) {
            setVoice((current) => ({
              ...current,
              // Only playback failed. ASR stays online and can accept the next
              // utterance without forcing the user to reopen voice mode.
              phase: generatingRef.current ? "thinking" : "listening",
              error: `语音播放失败：${message}（仍在监听）`,
              level: 0,
            }));
          }
          notify(message);
        }
        break;
      }
    }
    audioPlayingRef.current = false;
    if (generation === playbackGenerationRef.current && voiceOpenRef.current) {
      publishPlaybackState(false);
      currentSpeechRef.current = null;
      voiceDeliveryRef.current = null;
      setVoice((current) => current.phase === "error" ? current : ({ ...current, phase: "listening", level: 0 }));
      const deferred = deferredVoiceSegmentsRef.current.splice(0);
      if (deferred.length) {
        deferred.forEach((text) => queueVoiceSegmentRef.current?.(text, false));
      } else if (!playbackFailed) {
        companionArmedRef.current = true;
        scheduleIdleContinuation("voice", true);
      }
    }
  }, [notify, playPCMStream, prepareSpeech, publishPlaybackState, scheduleIdleContinuation, setVoiceInputLocked]);

  const enqueueSpeech = useCallback((text: string, force = false, voiceCue = ttsVoiceCueRef.current) => {
    if ((!force && !voiceOpenRef.current && !bool(settings?.audio.auto_tts)) || !text.trim()) return;
    const speech = ttsResponseStartedRef.current ? text.trim() : stripLeadingTtsFiller(text);
    if (!hasSpeakableContent(speech)) return;
    ttsResponseStartedRef.current = true;
    audioQueueRef.current.push({ id: uid(), text: speech, voiceCue });
    // One VoiceIntent has one local synthesis request at a time.  Keeping only
    // text in this queue avoids a hidden stack of HTTP streams behind the
    // GPT-SoVITS lock when a streamed answer arrives quickly.
    if (!audioPlayingRef.current) void playQueue();
  }, [playQueue, settings]);

  const acceptSpeechDelta = useCallback((delta: string, flush = false) => {
    const sentences = speechSegmenterRef.current.feed(
      delta,
      flush,
      voiceOpenRef.current,
    );
    sentences.forEach((sentence) => enqueueSpeech(sentence));
  }, [enqueueSpeech]);

  const flushResponseDelta = useCallback(() => {
    // 一帧最多触发一次 React 状态更新；provider token 全部保留，只合并渲染。
    responseFrameRef.current = null;
    const delta = pendingResponseDeltaRef.current;
    pendingResponseDeltaRef.current = "";
    if (!delta) return;
    setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content: item.content + delta } : item));
    if (voiceOpenRef.current) {
      setVoice((current) => ({ ...current, reply: current.reply + delta, phase: audioPlayingRef.current ? "assistant-speaking" : "thinking" }));
    }
  }, []);

  const scheduleResponseDelta = useCallback((delta: string) => {
    // 模型 token 到达频率通常高于屏幕刷新率，先积累到下一 animation frame。
    pendingResponseDeltaRef.current += delta;
    if (responseFrameRef.current === null) {
      responseFrameRef.current = window.requestAnimationFrame(flushResponseDelta);
    }
  }, [flushResponseDelta]);

  const clearPendingResponseDelta = useCallback(() => {
    pendingResponseDeltaRef.current = "";
    if (responseFrameRef.current !== null) {
      window.cancelAnimationFrame(responseFrameRef.current);
      responseFrameRef.current = null;
    }
  }, []);

  useEffect(() => clearPendingResponseDelta, [clearPendingResponseDelta]);

  const cancelRun = useCallback(async () => {
    flushResponseDelta();
    stopAudio();
    abortRef.current?.abort();
    const active = runIdRef.current;
    if (active) await fetch(`/api/v1/runs/${encodeURIComponent(active)}/cancel`, { method: "POST" }).catch(() => undefined);
    setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, status: "cancelled" as const } : item));
    setGenerating(false);
    runIdRef.current = "";
    setRunId("");
    clearActiveRun(active);
    setVoiceInputLocked(false, "run_cancelled");
    // Cancellation only owns the active LLM/TTS intent.  The resident ASR
    // transport remains subscribed, so return directly to listening instead
    // of leaving the call UI in an inert "interrupted" state.
    if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "listening", reply: "", level: 0, error: "" }));
  }, [flushResponseDelta, setVoiceInputLocked, stopAudio]);

  const addEvent = useCallback((event: InspectorEvent) => setEvents((items) => [...items.slice(-79), event]), []);

  const handleStreamEvent = useCallback((event: StreamEnvelope, isRecoveryReplay = false) => {
    const data = asRecord(event.data);
    if (event.event === "run.accepted") {
      runIdRef.current = event.run_id;
      setInspectionRunId(event.run_id);
      if (voiceOpenRef.current) lastVoiceRunIdRef.current = event.run_id;
      setRunId(event.run_id);
      // ASR activation belongs to the voice intent created when the call is
      // opened.  Re-sending `start` here used to reset an active recognizer
      // halfway through a streamed response.
      if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "thinking", reply: "", error: "" }));
    } else if (event.event === "node.started") {
      addEvent({ event: str(data.node), label: str(data.label || data.node), timestamp: event.timestamp, state: "active" });
    } else if (event.event === "node.completed") {
      setEvents((items) => items.map((item) => item.event === str(data.node) && item.state === "active" ? { ...item, state: data.error ? "error" : "done" } : item));
    } else if (event.event === "response.delta") {
      const delta = str(data.delta);
      if (voiceOpenRef.current) voiceReplyRef.current += delta;
      if (
        shouldSynthesizeStreamEvent(isRecoveryReplay)
        && !shouldBufferQwenReplyForSinglePass(settings, voiceOpenRef.current)
      ) {
        acceptSpeechDelta(delta);
      }
      scheduleResponseDelta(delta);
    } else if (event.event === "response.ready") {
      const content = str(data.content);
      const cue = str(data.voice_cue || ttsVoiceCueRef.current).toLowerCase();
      if (
        content
        && shouldSynthesizeStreamEvent(isRecoveryReplay)
        && shouldBufferQwenReplyForSinglePass(settings, voiceOpenRef.current)
        && !qwenFullReplySubmittedRef.current
      ) {
        ttsVoiceCueRef.current = ["neutral", "thoughtful", "warm", "firm", "playful", "intimate", "reflective", "tender", "teasing", "lively", "dramatic", "breathy", "laughing", "sighing", "seductive", "alluring", "moaning", "satisfied"].includes(cue) ? cue : "neutral";
        speechSegmenterRef.current.reset();
        qwenFullReplySubmittedRef.current = true;
        enqueueSpeech(content);
      }
    } else if (event.event === "response.voice_cue") {
      const cue = str(data.cue).toLowerCase();
      ttsVoiceCueRef.current = ["neutral", "thoughtful", "warm", "firm", "playful", "intimate", "reflective", "tender", "teasing", "lively", "dramatic", "breathy", "laughing", "sighing", "seductive", "alluring", "moaning", "satisfied"].includes(cue) ? cue : "neutral";
      setMessages((items) => items.map((item) => item.status === "streaming"
        ? { ...item, voice_cue: ttsVoiceCueRef.current }
        : item));
    } else if (event.event === "capability.notice") {
      // Capability progress is transient UI state.  It must not become assistant
      // prose, persisted memory, or TTS audio; the final answer remains one reply.
      addEvent({ event: event.event, label: str(data.label || "AI 正在补充只读信息"), timestamp: event.timestamp, data, state: "active" });
      if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "thinking" }));
    } else if (event.event.startsWith("capability.")) {
      const capability = str(data.capability || data.reason || "只读能力");
      const state = event.event === "capability.failed" ? "error" : event.event === "capability.started" ? "active" : "done";
      const capabilityNames: Record<string, string> = {
        "web.search": "联网搜索",
        "web.open": "网页查阅",
        "web.trending": "实时热点",
        "local.status": "本机状态",
        "mindspace.health": "Mindspace 服务",
        "local.knowledge": "本地知识",
      };
      const output = asRecord(data.output);
      const argumentsData = asRecord(data.arguments);
      const query = str(output.query || output.related_query || argumentsData.query || argumentsData.url);
      const capabilityName = capabilityNames[capability] || capability;
      const labels: Record<string, string> = {
        "capability.routing": "判断是否需要补充信息",
        "capability.planned": "补充查询规划完成",
        "capability.reviewed": "证据复核与二次查阅完成",
        "capability.started": `正在读取：${capabilityName}${query ? ` · ${query}` : ""}`,
        "capability.completed": `读取完成：${capabilityName}${query ? ` · ${query}` : ""}`,
        "capability.failed": `读取失败：${capabilityName}${query ? ` · ${query}` : ""}`,
      };
      const callId = str(data.call_id);
      const eventId = callId ? `capability:${callId}` : `${event.event}:${event.seq}`;
      if (callId && event.event !== "capability.started") {
        setEvents((items) => {
          const existing = items.find((item) => item.event === eventId);
          if (!existing) return [...items.slice(-79), { event: eventId, label: labels[event.event] || event.event, timestamp: event.timestamp, data, state }];
          return items.map((item) => item.event === eventId ? {
            ...item,
            label: labels[event.event] || event.event,
            timestamp: event.timestamp,
            data: { ...asRecord(item.data), ...data },
            state,
          } : item);
        });
      } else {
        addEvent({ event: eventId, label: labels[event.event] || event.event, timestamp: event.timestamp, data, state });
      }
    } else if (event.event === "emotion.completed") {
      const confidence = num(data.confidence, 0);
      const degraded = bool(data.degraded);
      addEvent({
        event: `${event.event}:${event.seq}`,
        label: degraded
          ? `情绪侧链已降级 · ${num(data.elapsed_ms)} ms`
          : `情绪侧链融合完成 · 置信度 ${Math.round(confidence * 100)}%`,
        timestamp: event.timestamp,
        data,
        state: degraded ? "error" : "done",
      });
    } else if (event.event === "response.replace") {
      clearPendingResponseDelta();
      const processRecovery = str(data.reason) === "process_recovery";
      // A replacement invalidates any locally queued audio, including when the
      // server restored a process. Never leave old PCM eligible for playback.
      stopAudio();
      const content = str(data.content);
      if (voiceOpenRef.current) voiceReplyRef.current = content;
      speechSegmenterRef.current.reset();
      if (
        shouldSynthesizeStreamEvent(isRecoveryReplay, processRecovery)
        && !shouldBufferQwenReplyForSinglePass(settings, voiceOpenRef.current)
      ) {
        acceptSpeechDelta(content, true);
      }
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content } : item));
      if (voiceOpenRef.current) setVoice((current) => ({ ...current, reply: content }));
    } else if (event.event === "retrieval.completed") {
      const ranked = Array.isArray(data.ranked) ? data.ranked as Record<string, unknown>[] : [];
      setRetrieval(ranked);
      addEvent({ event: event.event, label: `召回完成：知识 ${num(data.knowledge)} · 记忆 ${num(data.chat)}`, timestamp: event.timestamp, data, state: "done" });
    } else if (event.event === "validation.completed" || event.event === "json_update.committed") {
      addEvent({ event: event.event, label: event.event === "json_update.committed" ? "JSON 安全写回完成" : `${str(data.kind)} 校验完成`, timestamp: event.timestamp, data, state: bool(data.is_valid ?? true) ? "done" : "error" });
    } else if (event.event === "run.completed") {
      flushResponseDelta();
      const response = asRecord(data.response);
      const completedInitiative = activeInitiativeRef.current;
      if (completedInitiative.trigger === "continuous_companionship") {
        companionRoundRef.current = completedInitiative.sequence;
        setCompanionRound(completedInitiative.sequence);
      }
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      currentAssistantIdRef.current = str(response.assistant_message_id);
      if (shouldSynthesizeStreamEvent(isRecoveryReplay)) {
        if (shouldBufferQwenReplyForSinglePass(settings, voiceOpenRef.current)) {
          if (!qwenFullReplySubmittedRef.current) {
            qwenFullReplySubmittedRef.current = true;
            enqueueSpeech(str(response.reply));
          }
        } else {
          acceptSpeechDelta("", true);
        }
      }
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, message_id: str(response.assistant_message_id) || item.message_id, content: str(response.reply || item.content), status: "complete" as const } : item));
      if (voiceOpenRef.current) setVoice((current) => ({ ...current, reply: str(response.reply || current.reply), phase: audioPlayingRef.current || audioQueueRef.current.length ? "assistant-speaking" : "listening" }));
      activeVoiceTurnTextRef.current = "";
      activeVoiceTurnRoundRef.current = 0;
      setRound((value) => value + 1);
      setGenerating(false);
      runIdRef.current = "";
      setRunId("");
      clearActiveRun(event.run_id);
      if (voiceOpenRef.current && !audioPlayingRef.current && !audioQueueRef.current.length) {
        setVoiceInputLocked(false, "turn_completed_without_audio");
      }
      void loadSessions();
      if (!voiceOpenRef.current) scheduleIdleContinuation("text");
    } else if (event.event === "run.cancelled") {
      flushResponseDelta();
      stopAudio();
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, status: "cancelled" as const } : item));
      setGenerating(false);
      runIdRef.current = "";
      setRunId("");
      clearActiveRun(event.run_id);
      setVoiceInputLocked(false, "run_cancelled_event");
      if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "listening", level: 0, error: "" }));
    } else if (event.event === "run.interrupted") {
      flushResponseDelta();
      // The Core has already marked the run interrupted.  Drop any queued
      // audio for that intent; a later reconnect must not finish speaking a
      // stale partial answer while the UI is ready for the next user turn.
      stopAudio();
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      const partialText = str(data.partial_text);
      setMessages((items) => items.map((item) => item.status === "streaming"
        ? { ...item, content: partialText || item.content, status: "interrupted" as const }
        : item));
      setGenerating(false);
      runIdRef.current = "";
      setRunId("");
      clearActiveRun(event.run_id);
      setVoiceInputLocked(false, "run_interrupted");
      if (voiceOpenRef.current) {
        setVoice((current) => ({
          ...current,
          reply: partialText || current.reply,
          phase: "listening",
          error: "Core 重启，已保留中断前生成的内容",
        }));
      }
    } else if (event.event === "run.error") {
      flushResponseDelta();
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      const response = asRecord(data.response);
      const errors = Array.isArray(response.errors) ? response.errors.join("；") : str(data.error);
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content: errors || "生成失败", status: "error" as const } : item));
      setGenerating(false);
      runIdRef.current = "";
      setRunId("");
      clearActiveRun(event.run_id);
      setVoiceInputLocked(false, "run_failed");
      if (voiceOpenRef.current) {
        notify(errors || "生成失败，语音识别仍在监听");
        setVoice((current) => ({
          ...current,
          phase: audioPlayingRef.current ? "assistant-speaking" : "listening",
          error: errors || "生成失败",
          level: 0,
        }));
      }
      activeVoiceTurnTextRef.current = "";
      activeVoiceTurnRoundRef.current = 0;
    }
  }, [acceptSpeechDelta, addEvent, clearPendingResponseDelta, enqueueSpeech, flushResponseDelta, loadSessions, notify, scheduleIdleContinuation, scheduleResponseDelta, setVoiceInputLocked, settings, stopAudio]);

  const sendMessage = useCallback(async (
    text = input,
    mode: "primary" | "regenerate" = "primary",
    targetRound = round,
    initiative = false,
    initiativeTrigger: InitiativeTrigger = initiative ? "manual" : "none",
    initiativeSequence = 0,
    initiativeSequenceLimit = 0,
  ) => {
    const content = initiative ? "请求 AI 主动回复" : text.trim();
    const asrEvidence = !initiative && voiceOpenRef.current
      ? pendingASREvidenceRef.current
      : null;
    pendingASREvidenceRef.current = null;
    if (!content) { notify("请输入消息内容"); return; }
    if (!activeCharacterId) {
      notify("请先为当前会话选择角色");
      setCharacterPickerScope("all");
      setCharacterPickerOpen(true);
      return;
    }
    if (!llmReady) {
      notify("请先在设置中填写并保存 LLM API 配置");
      setSettingsInitialTab("model");
      setModalDirty(false);
      setModal("settings");
      return;
    }
    cancelIdleContinuation();
    if (initiativeTrigger !== "idle_continuation") idleContinuationSentRef.current = false;
    if (generating) await cancelRun();
    if (voiceOpenRef.current && audioPlayingRef.current && !initiative) {
      captureVoiceInterruption("explicit_user_message");
    }
    stopAudio();
    // Do not lock ASR while the LLM is thinking. A committed utterance is
    // already protected by text deduplication, and any genuinely new utterance
    // must remain able to cancel/replace a slow or failed generation.
    speechSegmenterRef.current.reset();
    clearPendingResponseDelta();
    voiceReplyRef.current = "";
    currentAssistantIdRef.current = "";
    ttsVoiceCueRef.current = "neutral";
    completedSpeechRef.current = [];
    setInput("");
    setEvents([]);
    setRetrieval([]);
    if (voiceOpenRef.current) setVoice((current) => ({ ...current, transcript: initiative ? current.transcript : content, reply: "", phase: "thinking", error: "" }));
    if (voiceOpenRef.current && !initiative) {
      activeVoiceTurnTextRef.current = content;
      activeVoiceTurnRoundRef.current = targetRound;
    }
    const requestId = uid();
    activeInitiativeRef.current = { trigger: initiativeTrigger, sequence: initiativeSequence };
    runIdRef.current = requestId;
    setRunId(requestId);
    setGenerating(true);
    const clientSentAt = new Date().toISOString();
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, JSON.stringify({
      run_id: requestId,
      session_id: sessionId,
      round: targetRound,
      user_content: initiative ? "" : content,
      started_at: clientSentAt,
    } satisfies ActiveRunRecord));
    const user: Message = { role: "user", content, round: targetRound, status: "complete", timestamp: clientSentAt };
    const assistant: Message = { role: "assistant", content: "", round: targetRound, status: "streaming", kind: initiative ? "initiative_response" : "message", initiative_trigger: initiativeTrigger };
    const outgoing = initiative ? [assistant] : [user, assistant];
    followConversationRef.current = true;
    pendingConversationJumpRef.current = true;
    setMessages((items) => [...(mode === "regenerate" ? items.filter((item) => item.round !== targetRound) : items), ...outgoing]);
    const persona = settings?.persona || {};
    const retrievalSettings = settings?.retrieval || {};
    const llm = settings?.llm || {};
    // 这里只提交公开的人格、检索和采样参数。API key、base URL 和模型名由服务端覆盖，
    // 防止前端状态或请求重放改变真正使用的 provider 凭据。
    const payload = {
      message: content, session_id: sessionId, character_id: activeCharacterId,
      activity_session_id: activeActivitySessionId,
      session_mode: activeCharacter?.source === "draw" ? "draw" : "custom",
      round: targetRound, mode, interaction_mode: voiceOpenRef.current ? "voice" : "text", adult_mode: adultMode, r18_style_id: r18StyleId, initiative, initiative_trigger: initiativeTrigger,
      initiative_sequence: initiativeSequence, initiative_sequence_limit: initiativeSequenceLimit,
      client_sent_at: clientSentAt,
      client_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      client_utc_offset_minutes: -new Date().getTimezoneOffset(),
      voice_delivery: voiceOpenRef.current ? voiceDeliveryRef.current : null,
      voice_context: voiceOpenRef.current ? voiceInteractionRef.current : null,
      input_evidence: asrEvidence ? {
        asr: {
          quality: "uncertain",
          confirmed_text: content,
          uncertain_segments: asrEvidence.uncertain_segments,
          decision_reasons: asrEvidence.decision_reasons,
        },
      } : null,
      user_name: str(persona.user_name || "用户"), user_persona: str(persona.user_persona),
      character_name: str(persona.character_name || "Mindspace"), system_prompt: str(persona.system_prompt),
      api: { temperature: num(llm.temperature, 0.7), max_tokens: num(llm.max_tokens, 2000) },
      retrieval: {
        rag_enabled: bool(retrievalSettings.rag_enabled ?? true), knowledge_enabled: bool(retrievalSettings.knowledge_enabled ?? true),
        chat_enabled: bool(retrievalSettings.chat_enabled ?? true), structured_memory_enabled: bool(retrievalSettings.structured_memory_enabled ?? true), temporal_enabled: bool(retrievalSettings.temporal_enabled ?? true),
        knowledge_k: num(retrievalSettings.knowledge_k, 5), chat_k: num(retrievalSettings.chat_k, 10),
        similarity_threshold: num(retrievalSettings.similarity_threshold, 0.5), decay_rounds: num(retrievalSettings.decay_rounds, 20),
        decay_hours: num(retrievalSettings.decay_hours, 168),
        fairness_enabled: bool(retrievalSettings.fairness_enabled ?? true), low_exposure_ratio: num(retrievalSettings.low_exposure_ratio, 0.2),
        memory_family_limit: num(retrievalSettings.memory_family_limit, 2), starvation_rounds: num(retrievalSettings.starvation_rounds, 6),
        starvation_boost: num(retrievalSettings.starvation_boost, 0.12),
        bm25_enabled: bool(retrievalSettings.bm25_enabled ?? true), vector_enabled: bool(retrievalSettings.vector_enabled ?? true),
        rrf_k: num(retrievalSettings.rrf_k, 60), candidate_multiplier: num(retrievalSettings.candidate_multiplier, 4),
        max_total_boost: num(retrievalSettings.max_total_boost, 0.25), reranker_enabled: bool(retrievalSettings.reranker_enabled ?? false),
        reranker_top_n: num(retrievalSettings.reranker_top_n, 12), boosts: retrievalSettings.boosts || {},
      },
    };
    if (voiceOpenRef.current) voiceDeliveryRef.current = null;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await fetch("/api/v1/chat/stream", { method: "POST", headers: { "Content-Type": "application/json", "X-Request-ID": requestId }, body: JSON.stringify(payload), signal: controller.signal });
      await consumeResumableEventStream(response, requestId, handleStreamEvent, controller.signal);
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        notify((error as Error).message);
        setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content: "模型连接失败。请检查 API 地址、密钥或模型名称，然后重新尝试。", status: "error" as const } : item));
        if (voiceOpenRef.current) {
          setVoice((current) => ({
            ...current,
            phase: audioPlayingRef.current ? "assistant-speaking" : "listening",
            error: (error as Error).message,
            level: 0,
          }));
        }
      }
      setGenerating(false);
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      setVoiceInputLocked(false, "request_failed");
    } finally {
      abortRef.current = null;
    }
  }, [activeActivitySessionId, activeCharacter, activeCharacterId, adultMode, cancelIdleContinuation, cancelRun, captureVoiceInterruption, clearPendingResponseDelta, generating, handleStreamEvent, input, llmReady, notify, r18StyleId, round, sessionId, setVoiceInputLocked, settings, stopAudio]);

  useEffect(() => { sendMessageRef.current = sendMessage; }, [sendMessage]);

  useEffect(() => {
    if (!initialDataLoaded || generatingRef.current) return;
    const active = readActiveRun();
    if (!active || active.session_id !== sessionId) return;
    // This tab has no ownership of audio generated before it mounted. Clear
    // every local playback reference before rebuilding the visual transcript.
    stopAudio();
    const controller = new AbortController();
    abortRef.current = controller;
    runIdRef.current = active.run_id;
    setRunId(active.run_id);
    setGenerating(true);
    setMessages((items) => {
      const hasUser = items.some((item) => item.round === active.round && item.role === "user");
      const hasAssistant = items.some((item) => item.round === active.round && item.role === "assistant");
      const recovered: Message[] = [];
      if (!hasUser && active.user_content) {
        recovered.push({
          role: "user",
          content: active.user_content,
          round: active.round,
          status: "complete",
          timestamp: active.started_at,
        });
      }
      if (!hasAssistant) {
        recovered.push({
          role: "assistant",
          content: "",
          round: active.round,
          status: "streaming",
        });
      }
      return recovered.length ? [...items, ...recovered] : items;
    });
    void fetch(`/api/v1/runs/${encodeURIComponent(active.run_id)}/stream?after=0`, {
      headers: { "Last-Event-ID": "0" },
      signal: controller.signal,
    }).then((response) => consumeResumableEventStream(
      response,
      active.run_id,
      (event) => handleStreamEvent(event, true),
      controller.signal,
    )).catch((error: Error) => {
      if (error.name === "AbortError") return;
      clearActiveRun(active.run_id);
      runIdRef.current = "";
      setRunId("");
      setGenerating(false);
      setMessages((items) => items.map((item) => item.status === "streaming"
        ? { ...item, content: item.content || "未找到可恢复的运行", status: "error" as const }
        : item));
      notify(error.message);
    }).finally(() => {
      if (abortRef.current === controller) abortRef.current = null;
    });
    return () => controller.abort();
  }, [handleStreamEvent, initialDataLoaded, notify, sessionId, stopAudio]);

  const flushVoiceSegments = useCallback(async () => {
    voiceMergeTimerRef.current = null;
    const pending = voiceSegmentsRef.current.splice(0);
    if (!pending.length) return;
    const supplement = mergeVoiceText(pending);
    const hasActiveTurn = Boolean(activeVoiceTurnTextRef.current);
    const targetRound = hasActiveTurn ? activeVoiceTurnRoundRef.current : roundRef.current;
    const content = hasActiveTurn
      ? mergeVoiceText([activeVoiceTurnTextRef.current, supplement])
      : supplement;
    if (!content) return;
    bargeCommittedRef.current = false;
    if (generatingRef.current) {
      await cancelRun();
      setMessages((items) => items.filter((item) => item.round !== targetRound));
    }
    setInput("");
    setVoice((current) => ({ ...current, transcript: content, phase: "thinking", error: "" }));
    await sendMessageRef.current?.(content, "primary", targetRound, false, "none");
  }, [cancelRun]);

  const queueVoiceSegment = useCallback((text: string, deferred = false) => {
    const cleaned = text.trim();
    if (!cleaned) return;
    if (deferred && audioPlayingRef.current) {
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
      if (voiceOpenRef.current && (audioPlayingRef.current || audioQueueRef.current.length)) return;
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
        const intent = { id: uid(), generation: currentGeneration, lastEventSeq: 0 };
        voiceIntentRef.current = intent;
        existingSocket.send(JSON.stringify({
          action: "start",
          run_id: runIdRef.current,
          intent_id: intent.id,
          generation: intent.generation,
        }));
        existingSocket.send(JSON.stringify({ action: "playback_state", playing: audioPlayingRef.current }));
        setVoice((current) => ({
          ...current,
          phase: audioPlayingRef.current ? "assistant-speaking" : "listening",
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
      const intent = { id: uid(), generation, lastEventSeq: 0 };
      voiceIntentRef.current = intent;
      activeSocket.onopen = () => {
        if (!isCurrent()) { releaseLocal(); return; }
        activeSocket.send(JSON.stringify({
          action: "start",
          run_id: runIdRef.current,
          intent_id: intent.id,
          generation: intent.generation,
        }));
        activeSocket.send(JSON.stringify({ action: "playback_state", playing: audioPlayingRef.current }));
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
          phase: audioPlayingRef.current ? "assistant-speaking" : "listening",
          error: "",
        }));
        if (!audioPlayingRef.current) scheduleIdleContinuation("voice");
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
        if (!audioPlayingRef.current && now - voiceLevelRenderRef.current >= 50) {
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
          if (!audioPlayingRef.current && now - voiceLevelRenderRef.current >= 50) {
            voiceLevelRenderRef.current = now;
            setVoice((current) => ({ ...current, level: num(payload.data.level) }));
          }
        }
        if (payload.event === "asr.loading") setVoice((current) => ({ ...current, phase: "connecting" }));
        if (payload.event === "asr.speech_candidate") {
          cancelIdleContinuation();
          bargeCommittedRef.current = false;
          if (audioPlayingRef.current) {
            setPlaybackDucked(true);
            setVoice((current) => ({ ...current, phase: "candidate-interruption", error: "" }));
          }
          // A raw energy candidate is not yet confirmed speech. In quiet
          // listening mode keep the UI stable until FSMN-VAD or a decoded
          // partial confirms that the user is actually speaking.
        }
        if (payload.event === "asr.speech_candidate_cleared") {
          setPlaybackDucked(false);
          if (audioPlayingRef.current) {
            const backoffMs = num(settings?.audio.asr_false_candidate_backoff_ms, 3000);
            bargeBackoffRef.current = {
              level: Math.min(2, bargeBackoffRef.current.level + 1),
              until: performance.now() + backoffMs,
            };
            publishPlaybackState(true);
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
          if (audioPlayingRef.current && !bargeCommittedRef.current && (explicitStop || !coolingDown)) {
            bargeCommittedRef.current = true;
            lastBargeCommitAtRef.current = now;
            captureVoiceInterruption(explicitStop ? "explicit_stop_command" : "confirmed_barge_in");
            setPlaybackDucked(false);
            if (runIdRef.current) void cancelRun();
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
          if (disposition.commitBargeIn && audioPlayingRef.current && !bargeCommittedRef.current) {
            const now = performance.now();
            const explicitStop = bool(payload.data.explicit_stop);
            const cooldownMs = num(settings?.audio.asr_barge_in_cooldown_ms, 1500);
            if (explicitStop || now - lastBargeCommitAtRef.current >= cooldownMs) {
              bargeCommittedRef.current = true;
              lastBargeCommitAtRef.current = now;
              captureVoiceInterruption(explicitStop ? "explicit_stop_command" : "accepted_asr_final");
              setPlaybackDucked(false);
              if (runIdRef.current) void cancelRun();
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
            if (audioPlayingRef.current) {
              const backoffMs = num(settings?.audio.asr_false_candidate_backoff_ms, 3000);
              bargeBackoffRef.current = {
                level: Math.min(2, bargeBackoffRef.current.level + 1),
                until: performance.now() + backoffMs,
              };
              publishPlaybackState(true);
            }
            setVoice((current) => ({ ...current, phase: audioPlayingRef.current ? "assistant-speaking" : "listening" }));
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
    if (runIdRef.current) {
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

  const createSessionForCharacter = useCallback(async (character: CharacterSummary | CharacterRecord) => {
    const matchingSessions = sessions
      .filter((item) => item.character_id === character.character_id
        || (!item.character_id && str(item.character_name).trim() === character.display_name.trim()))
      .sort((left, right) => Date.parse(right.updated_at || "0") - Date.parse(left.updated_at || "0"));
    const existing = matchingSessions[0]?.session_id || character.latest_session_id;
    if (existing) {
      setCharacterPickerOpen(false);
      await openSession(existing);
      return;
    }
    cancelIdleContinuation();
    idleContinuationSentRef.current = false;
    companionRoundRef.current = 0;
    companionArmedRef.current = false;
    setCompanionRound(0);
    voiceDeliveryRef.current = null;
    if (generating) await cancelRun();
    const id = uid();
    await request<SessionDocument>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ session_id: id, character_id: character.character_id }),
    });
    setSessionId(id);
    localStorage.setItem("mindspace.session", id);
    setActiveCharacterId(character.character_id);
    setActiveActivitySessionId("");
    await loadConversationScene(id);
    setMessages([]); setRound(1); setEvents([]); setRetrieval([]); setSidebarOpen(false);
    setCharacterPickerOpen(false);
    setAppView("chat");
    window.history.replaceState(null, "", `#/chat/${id}`);
    await loadSessions();
    notify("已创建新对话");
  }, [cancelIdleContinuation, cancelRun, generating, loadConversationScene, loadSessions, notify, openSession, sessions]);

  const newSession = useCallback(() => {
    setCharacterPickerScope("all");
    setCharacterPickerOpen(true);
  }, []);

  const deleteSession = async (id: string) => {
    const target = sessions.find((item) => item.session_id === id);
    if (!target) { notify("会话不存在或已经删除"); return; }
    const displayName = str(target.character_name || target.title || "未命名会话").trim();
    const normalizedName = displayName.toLocaleLowerCase();
    const hasDuplicateName = sessions.some((item) => item.session_id !== id
      && str(item.character_name || item.title).trim().toLocaleLowerCase() === normalizedName);
    const hasConversation = num(target.message_count) > 0;
    if ((!hasDuplicateName || hasConversation) && !(await styledConfirm({
      title: `删除“${displayName}”？`,
      message: hasConversation
        ? `这个会话包含 ${target.message_count} 条对话，删除后无法恢复。`
        : "这是该名称下唯一的会话，删除后将回到其他最近会话。",
      detail: hasDuplicateName ? "同名但有内容，因此仍需要确认。" : "唯一名称需要明确确认。",
      confirmLabel: "删除会话",
      danger: true,
    }))) return;
    await request(`/api/v1/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
    const remaining = sessions.filter((item) => item.session_id !== id)
      .sort((left, right) => Date.parse(right.updated_at || "0") - Date.parse(left.updated_at || "0"));
    setSessions(remaining);
    if (id === sessionId) {
      const next = remaining[0];
      if (next) await openSession(next.session_id);
      else {
        setMessages([]);
        setConversationScene(null);
        navigate("modes");
      }
    }
    else await loadSessions();
    notify("会话已删除");
  };

  const deleteReply = async (messageId?: string) => {
    if (!messageId) { notify("回复尚未完成保存，暂时不能删除"); return; }
    if (!(await styledConfirm({ title: "删除这条 AI 回复？", message: "用户原话会保留，相关 JSON 会在下一轮重新校正。", confirmLabel: "删除回复", danger: true }))) return;
    const result = await request<{ pending_json_reconciliation: boolean }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`, { method: "DELETE" });
    setMessages((items) => items.filter((item) => item.message_id !== messageId));
    await loadSessions();
    notify(result.pending_json_reconciliation ? "回复已删除；相关 JSON 将在下一轮重新校正" : "主动回复已删除");
  };

  const clearCurrent = async () => {
    if (!messages.length) { notify("当前会话没有可清空的内容"); return; }
    if (!(await styledConfirm({ title: "清空当前上下文？", message: "当前会话内容会被清空，但人物档案与长期记忆不会被删除。", confirmLabel: "清空上下文", danger: true }))) return;
    await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/clear`, { method: "POST" });
    setMessages([]); setRound(1); await loadSessions(); notify("当前上下文已清空");
  };

  const exportSession = () => {
    if (!messages.length) { notify("当前会话没有可导出的内容"); return; }
    const content = messages.map((message) => `## ${message.role === "user" ? "用户" : "Mindspace"}\n\n${message.content}`).join("\n\n");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
    link.download = `mindspace-${sessionId}.md`; link.click(); URL.revokeObjectURL(link.href); notify("会话已导出");
  };

  const speakMessage = (text: string, voiceCue = "neutral") => {
    stopAudio();
    // Manual replay and live Qwen replies both use one continuous synthesis
    // request, so sentence boundaries cannot reset the sampled voice.
    enqueueSpeech(text, true, voiceCue);
  };
  const openModal = (name: Exclude<ModalName, null>) => { setModalDirty(false); setModal(name); };
  const closeModal = useCallback(async () => {
    if (modalDirty && !(await styledConfirm({ title: "放弃未保存的修改？", message: "关闭后，本次尚未保存的编辑会丢失。", confirmLabel: "放弃修改", danger: true }))) return;
    setModal(null); setModalDirty(false);
  }, [modalDirty]);
  const showFlow = () => { setInspectorTab("flow"); setInspectorOpen(true); };
  const showContext = () => { setInspectorTab("context"); setInspectorOpen(true); };

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const ctrl = event.ctrlKey || event.metaKey;
      if (event.key === "Escape") {
        if (voiceOpenRef.current) exitVoice();
        else if (profileCardRole) setProfileCardRole(null);
        else if (modal === "voice-entry") closeVoiceEntry();
        else if (modal) closeModal();
        else if (generating) void cancelRun();
      }
      if (ctrl && event.key.toLowerCase() === "n") { event.preventDefault(); newSession(); }
      if (ctrl && event.shiftKey && event.key.toLowerCase() === "m") { event.preventDefault(); voiceOpenRef.current ? exitVoice() : openVoiceEntry(); }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [cancelRun, closeModal, closeVoiceEntry, exitVoice, generating, modal, newSession, openVoiceEntry, profileCardRole]);

  useEffect(() => () => {
    closingVoiceRef.current = true;
    voiceEntryControllerRef.current?.abort();
    stopListening(false);
    stopAudio();
    closeCaptureContext();
    closePlaybackContext();
  }, [closeCaptureContext, closePlaybackContext, stopAudio, stopListening]);

  const recentSessions = useMemo(() => {
    return [...sessions]
      .sort((left, right) => Date.parse(right.updated_at || "0") - Date.parse(left.updated_at || "0"))
      .slice(0, 40);
  }, [sessions]);
  const filteredSessions = useMemo(() => {
    const query = search.trim().toLowerCase();
    return recentSessions.filter((item) => !query
      || item.title.toLowerCase().includes(query)
      || str(item.character_name).toLowerCase().includes(query));
  }, [recentSessions, search]);

  const openSettings = useCallback((tab = "model") => {
    setSettingsInitialTab(tab);
    setModalDirty(false);
    setModal("settings");
  }, []);
  const title = sessions.find((item) => item.session_id === sessionId)?.title || "新对话";
  const userName = str(settings?.persona.user_name || "用户");
  const characterName = activeCharacter?.display_name || str(settings?.persona.character_name || "Mindspace");
  const pickerCharacters = characterPickerScope === "custom"
    ? characters.filter((item) => item.source !== "draw")
    : characters;
  const interruptedSession = sessions.find((item) => Boolean((item as SessionSummary & { interrupted?: boolean }).interrupted));

  const enterDrawMode = async () => {
    const recent = characters.find((item) => item.source === "draw");
    if (!recent) {
      navigate("draw");
      return;
    }
    if (recent.latest_session_id) {
      await openSession(recent.latest_session_id);
      return;
    }
    await createSessionForCharacter(recent);
  };

  const enterCustomMode = () => {
    setCharacterPickerScope("custom");
    setCharacterPickerOpen(true);
  };

  if (appView === "modes") {
    return <>
      <ModeLobby
        characters={characters}
        userName={userName}
        interrupted={interruptedSession}
        onDraw={() => void enterDrawMode()}
        onCustom={enterCustomMode}
        onLibrary={() => navigate("characters")}
        onResume={(id) => void openSession(id)}
      />
      <CharacterPicker
        open={characterPickerOpen}
        characters={pickerCharacters}
        title={characterPickerScope === "custom" ? "选择自定义模式角色" : "选择本次对话的角色"}
        onClose={() => setCharacterPickerOpen(false)}
        onChoose={(character) => void createSessionForCharacter(character)}
        onDraw={() => { setCharacterPickerOpen(false); navigate("draw"); }}
      />
      {toast && <div className="toast" role="status">{toast}</div>}
    </>;
  }

  if (appView === "draw") {
    return <DrawWorkshop
      defaultUserName={userName}
      onBack={() => navigate("modes")}
      onCommitted={(character) => {
        void loadCharacters().then(() => createSessionForCharacter(character));
      }}
    />;
  }

  if (appView === "characters") {
    return <CharacterLibrary
      characters={characters}
      onBack={() => navigate("modes")}
      onRefresh={async () => { await loadCharacters(); }}
      onChat={(character) => void createSessionForCharacter(character)}
      onDraw={() => navigate("draw")}
    />;
  }

  if (activeCharacter && appView === "scenes") {
    return <ScenePickerPage
      character={activeCharacter}
      sessionId={sessionId}
      current={conversationScene}
      onBack={() => navigate("chat")}
      onChanged={setConversationScene}
      notify={notify}
    />;
  }

  return <div className={`app-shell ${inspectorOpen ? "inspector-visible" : "inspector-hidden"}`}>
    <aside className={`sidebar ${sidebarOpen ? "mobile-open" : ""}`}>
      <div className="brand-row"><button className="brand-mark" onClick={() => navigate("modes")} title="返回主页" aria-label="Mindspace 主页"><img src="/assets/mindspace-brand-icon.png" alt="" /></button><div><strong>Mindspace</strong><small>PRIVATE COMPANION</small></div><button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} aria-label="关闭会话栏">×</button></div>
      <button className="new-chat home-entry" onClick={() => navigate("modes")}><span>⌂</span> 主页</button>
      <label className="search-box"><span>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索会话" aria-label="搜索会话" /></label>
      <div className="session-heading"><span>最近会话</span><small>{filteredSessions.length}</small></div>
      <nav className="session-list">
        {filteredSessions.length ? filteredSessions.map((item) => <div className={`session-item ${item.session_id === sessionId ? "active" : ""}`} key={item.session_id}><button className="session-open" onClick={() => void openSession(item.session_id)}>{item.character_avatar?.src ? <img className="session-avatar" src={item.character_avatar.src} alt="" /> : <span className="session-glyph">◌</span>}<span><strong>{item.character_name || item.title}</strong><small>{item.message_count} 条 · {formatTime(item.updated_at)}</small></span></button><button className="session-delete" aria-label={`删除会话：${item.character_name || item.title}`} title="删除会话" onClick={() => void deleteSession(item.session_id)}>×</button></div>) : <div className="empty-mini">没有匹配的会话</div>}
      </nav>
      <div className="sidebar-tools hub-navigation">
        <button className="sidebar-memory-entry" onClick={() => openModal("memory")}><span>◇</span><b>记忆</b><i>查看与修订长期记忆</i></button>
      </div>
      <div className="account-card"><PortraitAvatar role="assistant" avatars={effectiveAvatars} label={characterName} className="small" onClick={() => setProfileCardRole("assistant")} /><button className="account-settings persona-entry" aria-label="打开人设工作区" onClick={() => { setProfileEditorRole("assistant"); setModal("profile"); }}><span><strong>{characterName}</strong><small><i /> 人物、状态与关系</small></span><b>人设</b></button></div>
    </aside>

    <main className={`workspace${conversationScene?.scene ? " scene-active" : ""}`}>
      {conversationScene?.scene && <div
        key={conversationScene.scene.scene_id}
        className="chat-scene-background"
        style={{ backgroundImage: `url("${sceneAssetPath(conversationScene.scene.asset_id)}")` }}
        aria-hidden="true"
      />}
      <header className="topbar"><button className="mobile-only mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="打开会话栏">☰</button><div className="title-block"><span className="topbar-kicker">CONVERSATION</span><h1>{title}</h1><span>{characterName} · {generating ? "正在回应" : `第 ${round} 轮 · 已就绪`}</span></div><div className="top-actions"><button className="top-character-entry" onClick={() => { setProfileEditorRole("assistant"); setModal("profile"); }} title="打开人设工作区"><span className="top-character-avatar" style={avatarStyle(effectiveAvatars.assistant)} aria-hidden="true"><img src={effectiveAvatars.assistant.src} alt="" /></span><span>{characterName}</span></button><button className="top-settings-entry" onClick={() => openSettings("model")} title={llmReady ? `${str(settings?.llm.model || "API 已连接")} · 打开设置` : "API 尚未配置 · 打开设置"}><i className={llmReady ? "ready" : "warning"} />⚙ <span>设置</span></button></div></header>
      <section
        className="conversation"
        ref={conversationRef}
        onScroll={handleConversationScroll}
        onWheel={(event) => { if (event.deltaY < 0) pauseConversationFollow(); }}
        onTouchMove={pauseConversationFollow}
      >
        {!messages.length && <div className="welcome-panel companion-stage"><div className="stage-portrait"><PortraitAvatar role="assistant" avatars={effectiveAvatars} label={characterName} onClick={() => setProfileCardRole("assistant")} /></div><span className="eyebrow">{activeCharacter?.relationship_label || "PRIVATE COMPANION"}</span><h2>{userName} <i>×</i> {characterName}</h2><blockquote>“{activeCharacter?.user_alias || userName}，我在。今天想从哪里开始？”</blockquote><div className="stage-actions"><button className="stage-speak" disabled={generating} onClick={() => void sendMessage("", "primary", round, true)}><span>✦</span><b>让 {characterName} 先说</b><small>由当前人设与场景发起一句话</small></button><button onClick={() => navigate("scenes")}><span>⌑</span><b>{conversationScene?.scene?.title || "选择场景"}</b><small>改变这次见面的环境</small></button><button onClick={() => { setProfileEditorRole("assistant"); setModal("profile"); }}><span>◇</span><b>查看人设</b><small>人物、关系与运行状态</small></button></div></div>}
        <MessageList messages={messages} avatars={effectiveAvatars} userName={userName} characterName={characterName} onProfile={setProfileCardRole} onCopy={(text) => { void navigator.clipboard.writeText(text); notify("已复制回复"); }} onSpeak={speakMessage} onRegenerate={(value, targetRound) => void sendMessage(value, "regenerate", targetRound)} onInitiative={(targetRound) => void sendMessage("", "regenerate", targetRound, true)} onDelete={(messageId) => void deleteReply(messageId)} onConfigure={() => openSettings("model")} />
        <div className="conversation-tail" ref={conversationTailRef} aria-hidden="true" />
      </section>
      <section className="composer-wrap">{generating && <div className="run-strip"><span><i /> 正在回应</span><button onClick={() => void cancelRun()}>停止生成</button></div>}<div className="composer"><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void sendMessage(); } }} placeholder={`对 ${characterName} 说点什么…`} rows={1} /><div className="composer-row"><div className="composer-primary-tools"><button className="voice-entry" onClick={openVoiceEntry}><span>●</span> 实时语音</button><button className={`scene-entry${conversationScene?.scene ? " active" : ""}`} onClick={() => navigate("scenes")}><img src="/assets/archive/icons/activity-scene.svg" alt="" /><span>{conversationScene?.scene?.title || "场景"}</span></button><button className="initiative-inline" disabled={generating} onClick={() => void sendMessage("", "primary", round, true)}>✦ 让 {characterName} 先说</button><details className="composer-menu composer-add-menu"><summary aria-label="更多对话功能"><span className="visually-hidden">更多</span><b aria-hidden="true">＋</b></summary><div><button onClick={(event) => { event.currentTarget.closest("details")?.removeAttribute("open"); showFlow(); }}>会话流程与执行详情</button><button onClick={(event) => { event.currentTarget.closest("details")?.removeAttribute("open"); showContext(); }}>本轮引用 <b>{retrieval.length}</b></button><button onClick={exportSession}>导出当前会话</button><button className={`adult-entry${adultMode ? " active" : ""}`} aria-label="NSFW" aria-pressed={adultMode} onClick={toggleAdultMode}>NSFW <span>{adultMode ? "已开启" : "已关闭"}</span></button>{adultMode && <label className="r18-style-menu-label"><span>成人模式风格</span><select className="r18-style-select" value={r18StyleId} aria-label="R18 风格包" onChange={(event) => { const next = event.target.value; setR18StyleId(next); localStorage.setItem(R18_STYLE_STORAGE_KEY, next); }}><option value="high_intensity">高强度推进</option><option value="immersive_narrative">叙事沉浸</option><option value="dialogue_led">台词主导</option></select></label>}<button className="composer-clear-action" onClick={() => void clearCurrent()}>清空当前上下文</button></div></details></div><button className="send" onClick={() => generating ? void cancelRun() : void sendMessage()} disabled={!generating && !input.trim()} aria-label={generating ? "停止生成" : "发送消息"}>{generating ? "■" : "↑"}</button></div></div><div className="composer-meta"><span>Enter 发送 · Shift+Enter 换行 · Esc 打断</span><span>{adultMode ? "NSFW 已开启" : conversationScene?.scene ? `当前场景：${conversationScene.scene.title}` : "本地 · 私密 · 连续记忆"}</span></div></section>
    </main>

    <Inspector open={inspectorOpen} tab={inspectorTab} onTab={setInspectorTab} onClose={() => setInspectorOpen(false)} events={events} retrieval={retrieval} runId={inspectionRunId} />
    {modal === "settings" && settings && <SettingsDialog value={settings} avatars={avatars} initialTab={settingsInitialTab} onClose={closeModal} onDirty={setModalDirty} onOpenProfile={(role) => { setProfileEditorRole(role); setModalDirty(false); setModal("profile"); }} onOpenMemory={() => { setModalDirty(false); setModal("memory"); }} onOpenKnowledge={() => { setModalDirty(false); setModal("knowledge"); }} onOpenDiagnostics={() => { setModalDirty(false); setModal("diagnostics"); }} onSaved={(next, nextAvatars) => { setSettings(next); setAvatars(nextAvatars); setModalDirty(false); setModal(null); }} onSettingsChange={setSettings} onAvatarsChange={setAvatars} notify={notify} />}
    {modal === "knowledge" && <KnowledgeDialog onClose={closeModal} onDirty={setModalDirty} notify={notify} />}
    {modal === "memory" && <MemoryDialog characterId={activeCharacterId} onClose={closeModal} onDirty={setModalDirty} notify={notify} />}
    {modal === "profile" && <ProfileDialog characterId={activeCharacterId} initialName={profileEditorRole} onClose={closeModal} onDirty={setModalDirty} onOpenConnection={() => openSettings("model")} onSaved={() => void loadCharacters()} notify={notify} />}
    {modal === "diagnostics" && <DiagnosticsDialog onClose={closeModal} notify={notify} onCleared={() => { newSession(); void loadSessions(); }} />}
    {modal === "voice-entry" && <VoiceEntryDialog mode={voiceEntryMode} scene={voiceEntryScene} busy={voiceEntryBusy} error={voiceEntryError} onModeChange={(next) => { setVoiceEntryMode(next); setModalDirty(true); }} onSceneChange={(next) => { setVoiceEntryScene(next); setModalDirty(true); }} onClose={closeVoiceEntry} onStart={() => void startVoiceFromEntry()} />}
    {profileCardRole && <ProfileCardDialog characterId={activeCharacterId} role={profileCardRole} avatars={effectiveAvatars} displayName={profileCardRole === "user" ? userName : characterName} onClose={() => setProfileCardRole(null)} onEdit={(role) => { setProfileCardRole(null); setProfileEditorRole(role); setModal("profile"); }} />}
    {voice.open && <VoiceMode state={voice} avatar={effectiveAvatars.assistant} characterName={characterName} context={voiceInteractionRef.current} companion={{ enabled: bool(settings?.interaction?.unlimited_reply_enabled), round: companionRound, limit: Math.max(1, Math.min(50, num(settings?.interaction?.unlimited_reply_max_rounds, 10))) }} onExit={exitVoice} onRetry={retryVoice} onFallback={useBrowserVoiceFallback} />}
    <CharacterPicker open={characterPickerOpen} characters={pickerCharacters} onClose={() => setCharacterPickerOpen(false)} onChoose={(character) => void createSessionForCharacter(character)} onDraw={() => { setCharacterPickerOpen(false); navigate("draw"); }} />
    {nsfwConfirmationOpen && <NsfwAdultConfirmation seconds={nsfwConfirmationSeconds} onCancel={() => setNsfwConfirmationOpen(false)} onConfirm={confirmAdultMode} />}
    {toast && <div className="toast" role="status">{toast}</div>}
  </div>;
}

const MessageList = memo(function MessageList({ messages, avatars, userName, characterName, onProfile, onCopy, onSpeak, onRegenerate, onInitiative, onDelete, onConfigure }: {
  messages: Message[]; avatars: AvatarConfig; userName: string; characterName: string;
  onProfile: (role: Role) => void; onCopy: (text: string) => void; onSpeak: (text: string, voiceCue?: string) => void;
  onRegenerate: (text: string, round: number) => void; onInitiative: (round: number) => void; onDelete: (messageId?: string) => void; onConfigure: () => void;
}) {
  return <div className="message-list">{messages.map((message, index) => {
    const label = message.role === "user" ? userName : characterName;
    const initiativeLabel = message.initiative_trigger === "continuous_companionship" ? "· 连续陪伴" : message.kind === "initiative_response" ? "· 主动回应" : "";
    return <article className={`message ${message.role} ${message.status || "complete"}`} key={message.message_id || `${message.round}-${message.role}-${index}`}><PortraitAvatar role={message.role} avatars={avatars} label={label} onClick={() => onProfile(message.role)} /><div className="message-content"><div className="message-head"><strong>{message.status === "error" ? "Mindspace" : label}</strong><span>第 {message.round} 轮 {initiativeLabel}{message.status === "streaming" && "· 正在生成"}{message.status === "cancelled" && "· 已打断"}{message.status === "interrupted" && "· 回答在此处中断"}{message.status === "error" && "· 连接失败"}</span></div><div className="message-text">{richText(message.content || (message.status === "streaming" ? "" : "…"))}{message.status === "streaming" && <i className="stream-caret" />}</div>{message.status === "error" ? <div className="message-actions error-actions"><button className="primary" onClick={onConfigure}>立即配置 API</button><button onClick={() => { const user = messages.find((item) => item.role === "user" && item.round === message.round); if (user) onRegenerate(user.content, message.round); }}>重新尝试</button></div> : message.role === "assistant" && message.status !== "streaming" && <div className="message-actions"><button onClick={() => onCopy(message.content)}>复制</button><button onClick={() => onSpeak(message.content, message.voice_cue)}>朗读</button><button onClick={() => { if (message.kind === "initiative_response") { onInitiative(message.round); return; } const user = messages.find((item) => item.role === "user" && item.round === message.round); if (user) onRegenerate(user.content, message.round); }}>重新生成</button><button onClick={() => onDelete(message.message_id)}>删除回复</button></div>}</div></article>;
  })}</div>;
});

function Inspector({ open, tab, onTab, onClose, events, retrieval, runId }: { open: boolean; tab: InspectorTab; onTab: (tab: InspectorTab) => void; onClose: () => void; events: InspectorEvent[]; retrieval: Record<string, unknown>[]; runId: string }) {
  const [prompt, setPrompt] = useState<PromptInspection | null>(null);
  const [promptError, setPromptError] = useState("");
  const loadPrompt = useCallback(async (reveal = false) => {
    if (!runId) return;
    setPromptError("");
    try {
      setPrompt(await request<PromptInspection>(`/api/v1/runs/${encodeURIComponent(runId)}/prompt-inspection${reveal ? "?reveal=true" : ""}`));
    } catch (error) {
      setPromptError((error as Error).message);
    }
  }, [runId]);
  useEffect(() => {
    if (open && tab === "prompt") void loadPrompt(false);
  }, [loadPrompt, open, tab]);
  return <aside className={`inspector ${open ? "open" : ""}`} hidden={!open} aria-hidden={!open}><header><div><span className="eyebrow">LIVE TRACE</span><h2>执行详情</h2><small>节点、引用与模型实际输入</small></div><button onClick={onClose} aria-label="关闭执行详情">×</button></header><div className="inspector-tabs"><button className={tab === "flow" ? "active" : ""} onClick={() => onTab("flow")}>编排流程</button><button className={tab === "context" ? "active" : ""} onClick={() => onTab("context")}>本轮引用 <b>{retrieval.length}</b></button><button className={tab === "prompt" ? "active" : ""} onClick={() => onTab("prompt")}>模型输入</button></div>{tab === "flow" ? <div className="trace-list">{events.length ? events.map((item, index) => <TraceItem item={item} key={`${item.event}-${index}`} />) : <div className="empty-mini">发送消息后，这里会实时显示检索、生成、校验和写回节点。</div>}</div> : tab === "context" ? <div className="context-list">{retrieval.length ? retrieval.map((item, index) => <article key={str(item.chunk_id || index)}><header><span>{str(item.source || "召回内容")}</span><b>{num(item.weighted_score || item.score).toFixed(3)}</b></header><p>{str(item.text)}</p><small>{str(asRecord(item.metadata).source || item.session_id || "")}</small></article>) : <div className="empty-mini">本轮尚无引用内容。发送消息后，召回的知识和记忆会显示在这里。</div>}</div> : <div className="prompt-inspection">{!runId ? <div className="empty-mini">发送消息后可检查该轮模型输入。</div> : promptError ? <div className="empty-mini">{promptError}</div> : !prompt ? <div className="empty-mini">正在读取模型输入…</div> : <><header><span>{prompt.message_count} 层 · 约 {prompt.estimated_tokens} tokens</span><button onClick={() => void loadPrompt(!prompt.revealed)}>{prompt.revealed ? "恢复脱敏" : "临时显示完整内容"}</button></header>{prompt.layers.map((layer) => <details key={`${layer.index}-${layer.layer}`}><summary><b>{layer.layer}</b><span>{layer.role} · {layer.chars} 字</span></summary><pre>{layer.content}</pre></details>)}</>}</div>}</aside>;
}

function safeWebUrl(value: unknown) {
  const url = str(value).trim();
  return /^https?:\/\//i.test(url) ? url : "";
}

function TraceItem({ item }: { item: InspectorEvent }) {
  const data = asRecord(item.data);
  const capability = str(data.capability);
  const isWeb = capability.startsWith("web.");
  return <div className={`trace-item ${item.state || "done"}`}><i /><span><strong>{item.label}</strong><small>{formatTime(item.timestamp)}</small>{item.data != null && <details className="trace-details"><summary>{isWeb ? "展开联网查询与证据" : "展开节点数据"}</summary>{isWeb ? <WebTraceData data={data} /> : <pre>{JSON.stringify(item.data, null, 2)}</pre>}</details>}</span></div>;
}

function WebTraceData({ data }: { data: Record<string, unknown> }) {
  const args = asRecord(data.arguments);
  const output = asRecord(data.output);
  const coverage = asRecord(output.coverage);
  const query = str(output.query || output.related_query || args.query);
  const requestedUrl = safeWebUrl(output.requested_url || args.url);
  const items = Array.isArray(output.items) ? output.items.map(asRecord) : [];
  const documents = Array.isArray(output.documents) ? output.documents.map(asRecord) : [];
  const errors = Array.isArray(output.page_errors) ? output.page_errors.map(asRecord) : [];
  return <div className="web-trace">
    <div className="web-trace-meta">
      {query && <p><b>查询词</b><span>{query}</span></p>}
      {requestedUrl && <p><b>指定网页</b><a href={requestedUrl} target="_blank" rel="noreferrer">{requestedUrl}</a></p>}
      {str(output.engine) && <p><b>搜索引擎</b><span>{str(output.engine)}</span></p>}
      {Object.keys(coverage).length > 0 && <p><b>覆盖范围</b><span>命中 {num(coverage.search_result_count)} 条，打开原文 {num(coverage.opened_page_count)} 页，来源域名 {num(coverage.source_domain_count)} 个</span></p>}
      {bool(data.included_in_main_prompt) && <p><b>使用方式</b><span>以下已打开原文与检索结果已送入本轮主模型；搜索摘要仅用于发现来源</span></p>}
      {str(data.error) && <p className="web-error"><b>错误</b><span>{str(data.error)}</span></p>}
    </div>
    {items.length > 0 && <section><h4>搜索命中（{items.length}）</h4>{items.map((entry, index) => {
      const url = safeWebUrl(entry.url);
      return <article className="web-result" key={`${url}-${index}`}><strong>{str(entry.title || entry.source || `结果 ${index + 1}`)}</strong>{str(entry.summary) && <p>{str(entry.summary)}</p>}<small>{str(entry.source)}{str(entry.published_at) ? ` · ${str(entry.published_at)}` : ""}</small>{url && <a href={url} target="_blank" rel="noreferrer">打开来源</a>}</article>;
    })}</section>}
    {documents.length > 0 && <section><h4>已打开原文（{documents.length}）</h4>{documents.map((document, index) => {
      const url = safeWebUrl(document.url);
      return <details className="web-document" key={`${url}-${index}`}><summary>{str(document.title || document.source || `原文 ${index + 1}`)} <small>{str(document.status)}</small></summary>{url && <a href={url} target="_blank" rel="noreferrer">{url}</a>}<pre>{str(document.content || document.error || "未提取到正文")}</pre></details>;
    })}</section>}
    {errors.length > 0 && <section><h4>未能打开的页面（{errors.length}）</h4>{errors.map((error, index) => <p className="web-error" key={index}>{str(error.url)}：{str(error.error)}</p>)}</section>}
    {!query && !requestedUrl && !items.length && !documents.length && !str(data.error) && <pre>{JSON.stringify(data, null, 2)}</pre>}
  </div>;
}

function VoiceEntryDialog({ mode, scene, busy, error, onModeChange, onSceneChange, onClose, onStart }: {
  mode: VoiceInteractionMode;
  scene: string;
  busy: boolean;
  error: string;
  onModeChange: (mode: VoiceInteractionMode) => void;
  onSceneChange: (scene: string) => void;
  onClose: () => void;
  onStart: () => void;
}) {
  return <Modal title="选择互动方式" kicker="LIVE INTERACTION" onClose={onClose} dismissOnBackdrop compact className="voice-entry-card" footer={<><button className="secondary" onClick={onClose}>{busy ? "取消连接" : "取消"}</button><button className="primary" disabled={busy} onClick={onStart}>{busy ? "正在检查语音服务…" : mode === "face_to_face" ? "开始面对面互动" : "开始通话"}</button></>}>
    <div className="voice-entry-setup">
      <p className="notice">选择会保存为下次默认值。通话保持原有语音逻辑；面对面会在每轮语音中加载你保存的场景，但不会把场景自动写成人物事实或长期记忆。</p>
      {error && <p className="notice warning" role="alert">{error}</p>}
      <div className="voice-entry-options" role="group" aria-label="互动方式">
        <button type="button" className={mode === "call" ? "active" : ""} aria-pressed={mode === "call"} onClick={() => onModeChange("call")}><span>通话</span><small>默认 · 保持现有实时语音逻辑</small></button>
        <button type="button" className={mode === "face_to_face" ? "active" : ""} aria-pressed={mode === "face_to_face"} onClick={() => onModeChange("face_to_face")}><span>面对面</span><small>共享当前场景，只进行自然口语对话</small></button>
      </div>
      {mode === "face_to_face" && <label className="voice-scene-field"><span>当前场景</span><textarea aria-label="当前场景" value={scene} maxLength={2000} rows={6} placeholder="例如：深夜的客厅，只开着落地灯，窗外正在下雨；我们坐在沙发两端。" onChange={(event) => onSceneChange(event.target.value)} /><small>{scene.length} / 2000 · 可留空，角色会使用未指定的普通面对面场景</small></label>}
      <p className="voice-entry-boundary">{mode === "face_to_face" ? "场景只用于理解当下语境；AI 只说自然口语，不朗读括号、动作、神态或第一人称动作播报。" : "AI 只会知道当前正在实时语音通话，不会额外描述共同所处的物理场景。"}</p>
    </div>
  </Modal>;
}

function Modal({ title, kicker, onClose, children, footer, compact = false, className = "", dismissOnBackdrop = false }: { title: string; kicker: string; onClose: () => void; children: ReactNode; footer?: ReactNode; compact?: boolean; className?: string; dismissOnBackdrop?: boolean }) {
  const displayTitle = title === "记忆中心" ? "记忆" : title;
  const displayKicker = kicker === "MEMORY CENTER" ? "MEMORY" : kicker;
  return <div className="modal-backdrop" onMouseDown={(event) => {
    if (event.target !== event.currentTarget) return;
    if (dismissOnBackdrop) onClose();
    else event.preventDefault();
  }}><section className={`modal-card ${compact ? "compact" : ""} ${className}`.trim()} role="dialog" aria-modal="true" aria-label={displayTitle}><header><div><span className="eyebrow">{displayKicker}</span><h2>{displayTitle}</h2></div><button onClick={onClose} aria-label={`关闭${displayTitle}`}>×</button></header><div className="modal-body">{children}</div>{footer && <footer>{footer}</footer>}</section></div>;
}

function Field({ label, value, type = "text", onChange, min, max, step, placeholder }: { label: string; value: unknown; type?: string; onChange: (value: unknown) => void; min?: number; max?: number; step?: number; placeholder?: string }) {
  if (type === "checkbox") return <label className="toggle-field"><span>{label}</span><input type="checkbox" checked={bool(value)} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
  if (type === "textarea") return <label className="field wide"><span>{label}</span><textarea value={str(value)} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} rows={4} /></label>;
  return <label className="field"><span>{label}</span><input type={type} value={str(value)} placeholder={placeholder} min={min} max={max} step={step} onChange={(event) => onChange(type === "number" ? Number(event.target.value) : event.target.value)} /></label>;
}

function SelectField({ label, value, options, onChange, disabled = false }: { label: string; value: unknown; options: [string, string][]; onChange: (value: string) => void; disabled?: boolean }) {
  return <label className="field"><span>{label}</span><select value={str(value)} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{options.map(([id, name]) => <option value={id} key={id}>{name}</option>)}</select></label>;
}

function AvatarEditor({ role, entry, onChange, onUpload, busy }: { role: Role; entry: AvatarEntry; onChange: (entry: AvatarEntry) => void; onUpload: (file: File) => void; busy: boolean }) {
  const label = role === "assistant" ? "AI 头像" : "用户头像";
  return <article className="avatar-editor-card"><div className="avatar-editor-head"><div className="avatar-preview portrait-avatar" style={avatarStyle(entry)}><img src={entry.src} alt={`${label}预览`} /></div><div><strong>{label}</strong><small>{role === "assistant" ? "聊天与语音页面中的角色形象" : "聊天消息中的用户形象"}</small></div></div><div className="avatar-editor-actions"><label className="secondary upload-button">{busy ? "上传中…" : "上传图片"}<input hidden disabled={busy} type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload(file); event.currentTarget.value = ""; }} /></label><button className="secondary" onClick={() => onChange(DEFAULT_AVATARS[role])}>恢复默认</button></div><div className="avatar-controls"><SelectField label="头像比例" value={entry.aspect} options={[["2 / 3", "2:3 竖屏"], ["3 / 4", "3:4 竖屏"], ["4 / 5", "4:5 竖屏"], ["9 / 16", "9:16 长屏"], ["1 / 1", "1:1 方形"]]} onChange={(value) => onChange({ ...entry, aspect: value as AvatarEntry["aspect"] })} /><label>缩放 <b>{entry.scale.toFixed(2)}x</b><input type="range" min="0.6" max="3" step="0.01" value={entry.scale} onChange={(event) => onChange({ ...entry, scale: Number(event.target.value) })} /></label><label>横移 <b>{entry.x}%</b><input type="range" min="-80" max="80" value={entry.x} onChange={(event) => onChange({ ...entry, x: Number(event.target.value) })} /></label><label>纵移 <b>{entry.y}%</b><input type="range" min="-80" max="80" value={entry.y} onChange={(event) => onChange({ ...entry, y: Number(event.target.value) })} /></label></div></article>;
}

function SettingsDialog({ value, avatars, initialTab = "model", onClose, onDirty, onOpenProfile, onOpenMemory, onOpenKnowledge, onOpenDiagnostics, onSaved, onSettingsChange, onAvatarsChange, notify }: { value: ProductSettings; avatars: AvatarConfig; initialTab?: string; onClose: () => void; onDirty: (dirty: boolean) => void; onOpenProfile: (role: Role) => void; onOpenMemory: () => void; onOpenKnowledge: () => void; onOpenDiagnostics: () => void; onSaved: (value: ProductSettings, avatars: AvatarConfig) => void; onSettingsChange: (value: ProductSettings) => void; onAvatarsChange: (value: AvatarConfig) => void; notify: (message: string) => void }) {
  const normalizedValue: ProductSettings = {
    ...structuredClone(value),
    audio: {
      asr_listening_energy_threshold_db: -50,
      asr_listening_min_speech_ms: 120,
      asr_barge_in_energy_threshold_db: -38,
      asr_barge_in_min_speech_ms: 300,
      asr_candidate_release_ms: 280,
      asr_adaptive_noise_enabled: false,
      asr_noise_calibration_ms: 1500,
      asr_listening_noise_margin_db: 10,
      asr_barge_in_noise_margin_db: 16,
      asr_utterance_merge_ms: 1100,
      asr_deferred_during_playback: true,
      asr_hotwords_enabled: true,
      asr_dynamic_endpointing: true,
      asr_final_refinement_enabled: true,
      ...structuredClone(value.audio),
    },
    interaction: {
      idle_continuation_enabled: false,
      text_idle_seconds: 180,
      voice_idle_seconds: 30,
      unlimited_reply_enabled: false,
      unlimited_reply_interval_seconds: 10,
      unlimited_reply_max_rounds: 10,
      ...structuredClone(value.interaction || {}),
    },
    capabilities: {
      master_enabled: true,
      local_knowledge_enabled: true,
      web_search_enabled: false,
      realtime_topics_enabled: false,
      topic_expansion_enabled: true,
      proactive_hotspots_enabled: false,
      show_sources_enabled: true,
      web_timeout_seconds: 12,
      max_web_results: 10,
      max_web_pages: 6,
      max_web_content_chars: 12000,
      ...structuredClone(value.capabilities || {}),
    },
  };
  const [draft, setDraft] = useState<ProductSettings>(normalizedValue);
  const [avatarDraft, setAvatarDraft] = useState<AvatarConfig>(structuredClone(avatars));
  const [tab, setTab] = useState(initialTab);
  const [audioBusy, setAudioBusy] = useState("");
  const [audioStatus, setAudioStatus] = useState(bool(value.audio.tts_reference_configured) ? `已配置参考音频：${str(value.audio.tts_reference_name)}` : "尚未上传参考音频");
  const [providerBusy, setProviderBusy] = useState(false);
  const [providerStatus, setProviderStatus] = useState("切换链路后立即保存，无需再点击底部保存按钮");
  const [gptVoices, setGptVoices] = useState<{ active_voice: string; items: Array<{ id: string; label: string; family: string; installed: boolean; selected: boolean }> }>({ active_voice: "v4-changli", items: [] });
  const [qwenVoices, setQwenVoices] = useState<{ active_voice: string; items: Array<{ id: string; label: string; installed: boolean; selected: boolean }> }>({ active_voice: "serena", items: [] });
  const [avatarBusy, setAvatarBusy] = useState<Role | "">("");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [ttsApiKey, setTtsApiKey] = useState("");
  const [vocabulary, setVocabulary] = useState<ASRVocabularySnapshot | null>(null);
  const [vocabularyBusy, setVocabularyBusy] = useState(false);
  const [vocabularyQuery, setVocabularyQuery] = useState("");
  const [vocabularyTerm, setVocabularyTerm] = useState("");
  const [vocabularyAliases, setVocabularyAliases] = useState("");
  const [vocabularyPriority, setVocabularyPriority] = useState<ASRVocabularyEntry["priority"]>("high");
  const [vocabularyTest, setVocabularyTest] = useState("");
  const [vocabularyTestResult, setVocabularyTestResult] = useState("");
  const initial = useRef(JSON.stringify({ value: normalizedValue, avatars }));
  const update = (group: keyof ProductSettings, key: string, next: unknown) => setDraft((current) => ({ ...current, [group]: { ...(current[group] as Record<string, unknown>), [key]: next } }));
  const dirty = Boolean(llmApiKey || ttsApiKey) || JSON.stringify({ value: draft, avatars: avatarDraft }) !== initial.current;
  const externalAudioSelection = JSON.stringify({
    provider: value.audio.tts_provider,
    gpt: value.audio.tts_gpt_sovits_voice,
    qwen: value.audio.tts_qwen3_vllm_voice,
    auto: value.audio.auto_tts,
  });
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  useEffect(() => {
    if (providerBusy) return;
    const externalAudio = {
      tts_provider: value.audio.tts_provider,
      tts_gpt_sovits_voice: value.audio.tts_gpt_sovits_voice,
      tts_qwen3_vllm_voice: value.audio.tts_qwen3_vllm_voice,
      auto_tts: value.audio.auto_tts,
    };
    setDraft((current) => ({ ...current, audio: { ...current.audio, ...externalAudio } }));
    const baseline = JSON.parse(initial.current) as { value: ProductSettings; avatars: AvatarConfig };
    baseline.value = { ...baseline.value, audio: { ...baseline.value.audio, ...externalAudio } };
    initial.current = JSON.stringify(baseline);
  }, [externalAudioSelection, providerBusy, value.audio]);
  useEffect(() => {
    request<{ active_voice: string; items: Array<{ id: string; label: string; family: string; installed: boolean; selected: boolean }> }>("/api/v1/audio/tts/voices")
      .then(setGptVoices)
      .catch(() => undefined);
    request<{ active_voice: string; items: Array<{ id: string; label: string; installed: boolean; selected: boolean }> }>("/api/v1/audio/tts/qwen3/voices")
      .then(setQwenVoices)
      .catch(() => undefined);
    request<ASRVocabularySnapshot>("/api/v1/audio/asr/vocabulary")
      .then(setVocabulary)
      .catch(() => undefined);
  }, []);

  const saveManualVocabulary = async (entries: ASRVocabularyEntry[]) => {
    setVocabularyBusy(true);
    try {
      const result = await request<ASRVocabularySnapshot>("/api/v1/audio/asr/vocabulary", {
        method: "PUT",
        body: JSON.stringify({ entries: entries.map((item) => ({
          id: item.id, term: item.term, aliases: item.aliases, priority: item.priority,
          scope: item.scope, category: item.category, source_field: item.source_field,
          enabled: item.enabled, hit_count: item.hit_count, updated_at: item.updated_at,
        })) }),
      });
      setVocabulary(result);
      notify("识别词表已更新，下一段语音立即生效");
    } catch (error) {
      notify((error as Error).message);
    } finally {
      setVocabularyBusy(false);
    }
  };
  const addVocabularyEntry = async () => {
    const term = vocabularyTerm.trim();
    if (!term) { notify("请填写标准写法"); return; }
    const manual = (vocabulary?.entries || []).filter((item) => item.source === "manual");
    if (manual.some((item) => item.term.toLowerCase() === term.toLowerCase())) { notify("这个标准词已经存在"); return; }
    const entry: ASRVocabularyEntry = {
      id: uid(), term, aliases: vocabularyAliases.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean),
      priority: vocabularyPriority, weight: vocabularyPriority === "critical" ? 100 : vocabularyPriority === "high" ? 90 : vocabularyPriority === "medium" ? 65 : 30,
      scope: "global", category: "个人词表", source: "manual", source_field: "", enabled: true,
      hit_count: 0, updated_at: new Date().toISOString(), read_only: false,
    };
    await saveManualVocabulary([...manual, entry]);
    setVocabularyTerm(""); setVocabularyAliases("");
  };
  const testVocabulary = async () => {
    if (!vocabularyTest.trim()) return;
    setVocabularyBusy(true);
    try {
      const result = await request<{ corrected_text: string; matches: Array<{ from: string; to: string }> }>("/api/v1/audio/asr/vocabulary/test", { method: "POST", body: JSON.stringify({ text: vocabularyTest }) });
      setVocabularyTestResult(result.matches.length ? `${result.corrected_text}（${result.matches.map((item) => `${item.from}→${item.to}`).join("、")}）` : `${result.corrected_text}（未命中明确映射）`);
    } catch (error) { notify((error as Error).message); } finally { setVocabularyBusy(false); }
  };

  const persistSettings = async () => {
    const payload = structuredClone(draft);
    payload.llm.mode = "openai";
    if (llmApiKey.trim()) payload.llm.api_key = llmApiKey.trim();
    if (ttsApiKey.trim()) payload.audio.tts_siliconflow_api_key = ttsApiKey.trim();
    const result = await request<{ settings: ProductSettings }>("/api/v1/settings", { method: "PUT", body: JSON.stringify(payload) });
    setDraft(result.settings); setLlmApiKey(""); setTtsApiKey(""); onSettingsChange(result.settings); return result.settings;
  };
  const switchTtsProvider = async (next: string) => {
    const provider = ["browser", "cosyvoice", "gpt-sovits", "qwen3-vllm", "siliconflow"].includes(next) ? next : "browser";
    const previous = str(draft.audio.tts_provider || "qwen3-vllm");
    const previousAutoTts = bool(draft.audio.auto_tts);
    if (provider === previous || providerBusy) return;
    setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: provider, auto_tts: provider !== "browser" } }));
    setProviderBusy(true);
    setProviderStatus(provider === "browser" ? "正在关闭 TTS…" : provider === "cosyvoice" ? "正在切换到本地 CosyVoice…" : provider === "gpt-sovits" ? "正在切换到独立 GPT-SoVITS…" : provider === "qwen3-vllm" ? "正在切换到 Qwen3 实时语音…" : "正在切换到 SiliconFlow API…");
    try {
      const result = await request<{ settings: ProductSettings }>("/api/v1/settings", { method: "PUT", body: JSON.stringify({ audio: { tts_provider: provider, auto_tts: provider !== "browser" } }) });
      const confirmed = str(result.settings.audio.tts_provider);
      setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: confirmed } }));
      const baseline = JSON.parse(initial.current) as { value: ProductSettings; avatars: AvatarConfig };
      baseline.value = { ...baseline.value, audio: { ...baseline.value.audio, tts_provider: confirmed, auto_tts: confirmed !== "browser" } };
      initial.current = JSON.stringify(baseline);
      onSettingsChange(result.settings);
      const label = confirmed === "browser" ? "关闭声音（仅文字）" : confirmed === "cosyvoice" ? "本地 CosyVoice" : confirmed === "gpt-sovits" ? "本地 GPT-SoVITS" : confirmed === "qwen3-vllm" ? "Qwen3 实时语音" : "SiliconFlow API";
      setProviderStatus(`已切换并保存：${label}`);
      notify(`TTS 链路已切换为${label}`);
    } catch (error) {
      setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: previous, auto_tts: previousAutoTts } }));
      setProviderStatus(`切换失败，已保持原链路：${(error as Error).message}`);
      notify((error as Error).message);
    } finally {
      setProviderBusy(false);
    }
  };
  const switchGptVoice = async (voiceId: string) => {
    if (providerBusy) return;
    const previous = str(draft.audio.tts_gpt_sovits_voice || "v4-changli");
    setProviderBusy(true);
    setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: "gpt-sovits", tts_gpt_sovits_voice: voiceId } }));
    setProviderStatus("正在切换 GPT-SoVITS 音色…");
    try {
      const result = await request<{ ok: boolean; pending_worker?: boolean; message?: string; settings: Record<string, unknown> }>("/api/v1/audio/tts/voice/select", { method: "POST", body: JSON.stringify({ voice_id: voiceId }) });
      const next = { ...draft, audio: result.settings };
      setDraft(next); onSettingsChange(next);
      setGptVoices((current) => ({ ...current, active_voice: voiceId, items: current.items.map((item) => ({ ...item, selected: item.id === voiceId })) }));
      const pendingMessage = result.message || "音色已保存，但 Worker 暂未完成切换";
      setProviderStatus(result.pending_worker ? pendingMessage : "音色已切换并热加载");
      notify(result.pending_worker ? pendingMessage : "GPT-SoVITS 音色切换完成");
    } catch (error) {
      setDraft((current) => ({ ...current, audio: { ...current.audio, tts_gpt_sovits_voice: previous } }));
      setProviderStatus(`音色切换失败：${(error as Error).message}`); notify((error as Error).message);
    } finally { setProviderBusy(false); }
  };
  const switchQwenVoice = async (voiceId: string) => {
    if (providerBusy) return;
    setProviderBusy(true);
    try {
      const result = await request<{ settings: ProductSettings }>("/api/v1/settings", {
        method: "PUT",
        body: JSON.stringify({ audio: { tts_provider: "qwen3-vllm", tts_qwen3_vllm_voice: voiceId, tts_qwen3_vllm_task_type: "CustomVoice" } }),
      });
      setDraft((current) => ({ ...current, audio: result.settings.audio }));
      onSettingsChange(result.settings);
      setQwenVoices((current) => ({ ...current, active_voice: voiceId, items: current.items.map((item) => ({ ...item, selected: item.id === voiceId })) }));
      setProviderStatus("Qwen3 音色已保存；下一段语音立即生效");
    } catch (error) {
      setProviderStatus(`Qwen3 音色切换失败：${(error as Error).message}`);
      notify((error as Error).message);
    } finally { setProviderBusy(false); }
  };
  const save = async () => {
    try {
      const next = await persistSettings();
      const avatarResult = await request<{ config: AvatarConfig }>("/api/v1/avatar/config", { method: "PUT", body: JSON.stringify(avatarDraft) });
      notify("设置和头像已保存并立即生效"); onSaved(next, normalizeAvatarConfig(avatarResult.config));
    } catch (error) { notify((error as Error).message); }
  };
  const uploadReference = async (file: File) => {
    if (file.size > 20 * 1024 * 1024) { notify("参考音频不能超过 20 MiB"); return; }
    setAudioBusy("upload"); setAudioStatus(`正在优化并上传 ${file.name}…`);
    try {
      let prepared = file;
      try { prepared = await normalizeReferenceAudio(file); } catch { /* Server-side decoding remains available. */ }
      const form = new FormData(); form.append("file", prepared); form.append("transcript", str(draft.audio.tts_reference_text));
      const result = await request<{ reference: Record<string, unknown>; settings: Record<string, unknown> }>("/api/v1/audio/tts/reference", { method: "POST", body: form });
      const uploaded = { ...draft, audio: result.settings }; setDraft(uploaded); onSettingsChange(uploaded);
      setAudioBusy("recognize"); setAudioStatus("音频已保存，正在识别实际说话内容…");
      try {
        const recognized = await request<{ transcript: string; duration?: number; settings: Record<string, unknown> }>("/api/v1/audio/tts/reference/transcribe", { method: "POST" });
        const next = { ...uploaded, audio: recognized.settings }; setDraft(next); onSettingsChange(next);
        const duration = recognized.duration ? ` · ${recognized.duration.toFixed(1)} 秒` : "";
        setAudioStatus(`识别完成${duration}，请核对下方文字`); notify("参考音频已上传并识别，请核对参考文本");
      } catch (error) {
        setAudioStatus(`音频已保存，但自动识别失败：${(error as Error).message}`); notify("音频已上传，请手动填写或重新识别参考文本");
      }
    } catch (error) { setAudioStatus((error as Error).message); notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const recognizeReference = async () => {
    setAudioBusy("recognize"); setAudioStatus("正在识别参考音频中的实际文字…");
    try {
      const result = await request<{ transcript: string; duration?: number; settings: Record<string, unknown> }>("/api/v1/audio/tts/reference/transcribe", { method: "POST" });
      const next = { ...draft, audio: result.settings }; setDraft(next); onSettingsChange(next);
      const duration = result.duration ? ` · ${result.duration.toFixed(1)} 秒` : "";
      setAudioStatus(`识别完成${duration}，请核对后保存`); notify("识别结果已填入参考文本");
    } catch (error) { setAudioStatus((error as Error).message); notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const clearReference = async () => {
    if (!(await styledConfirm({ title: "清除参考音频？", message: "当前参考音频和参考文本都会被清除。", confirmLabel: "清除参考", danger: true }))) return;
    setAudioBusy("clear");
    try {
      const result = await request<{ settings: Record<string, unknown> }>("/api/v1/audio/tts/reference", { method: "DELETE" });
      const next = { ...draft, audio: result.settings }; setDraft(next); onSettingsChange(next); setAudioStatus("尚未上传参考音频"); notify("参考音频已清除");
    } catch (error) { notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const playTtsTest = async (next: ProductSettings) => {
    const status = await request<Record<string, unknown>>("/api/v1/audio/status");
    if (!bool(status.tts_ready)) throw new Error(str(status.tts_error || "TTS 服务尚未就绪"));
    if (str(next.audio.tts_provider) === "cosyvoice" && !bool(next.audio.tts_reference_configured)) throw new Error("请先上传参考音频");
    if (str(next.audio.tts_provider) === "siliconflow" && !bool(next.audio.tts_siliconflow_credentials_configured)) throw new Error("请先填写 SiliconFlow API 密钥");
    const response = await fetch("/api/v1/audio/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "这是 Mindspace 语音测试。", speed: num(next.audio.tts_speed, 1), request_id: uid() }) });
    if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(str(detail.detail || "测试语音生成失败")); }
    const blob = await response.blob();
    if (!blob.size) throw new Error("TTS 接口未返回音频数据");
    const url = URL.createObjectURL(blob); const audio = new Audio(url); audio.onended = () => URL.revokeObjectURL(url); await audio.play();
  };
  const testApiConnections = async () => {
    setAudioBusy("api-check"); setAudioStatus("正在同步检查 LLM 与 TTS API…");
    try {
      const next = await persistSettings();
      const llm = await request<Record<string, unknown>>("/api/v1/settings/test", { method: "POST" });
      if (!bool(llm.ok)) throw new Error(`LLM 自检失败：${str(llm.error || "连接失败")}`);
      await playTtsTest(next);
      const llmDetail = "LLM API 正常";
      const ttsDetail = str(next.audio.tts_provider) === "siliconflow" ? "云端 TTS API 正常" : "本地 TTS 正常";
      setAudioStatus(`${ttsDetail}，测试音频已播放`); notify(`自检完成：${llmDetail}；${ttsDetail}`);
    } catch (error) { setAudioStatus((error as Error).message); notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const testTts = async () => {
    setAudioBusy("test"); setAudioStatus("正在检查语音服务并生成测试语音…");
    try {
      const next = await persistSettings();
      await playTtsTest(next); setAudioStatus("测试语音生成并播放成功");
    } catch (error) { setAudioStatus((error as Error).message); notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const ttsProvider = str(draft.audio.tts_provider || "qwen3-vllm");
  const uploadAvatar = async (role: Role, file: File) => {
    if (file.size > 5 * 1024 * 1024) { notify("头像不能超过 5 MiB"); return; }
    setAvatarBusy(role);
    try {
      const form = new FormData(); form.append("file", file);
      const result = await request<{ config: AvatarConfig }>(`/api/v1/avatar/upload/${role}`, { method: "POST", body: form });
      const normalized = normalizeAvatarConfig(result.config); setAvatarDraft(normalized); onAvatarsChange(normalized); notify(`${role === "assistant" ? "AI" : "用户"}头像上传成功`);
    } catch (error) { notify((error as Error).message); } finally { setAvatarBusy(""); }
  };
  const settingGroups = [
    { id: "connection", label: "连接", tabs: [["model", "模型与 API"], ["capabilities", "自动能力"]] },
    { id: "memory", label: "记忆", tabs: [["rag", "记忆与检索"]] },
    { id: "voice", label: "声音", tabs: [["audio", "实时语音"], ["vocabulary", "识别词表"], ["rhythm", "陪伴频率"]] },
    { id: "interface", label: "界面", tabs: [["avatar", "人物头像"], ["appearance", "显示偏好"]] },
    { id: "advanced", label: "高级", tabs: [["protocol", "协议与诊断"]] },
  ] as const;
  const activeGroup = settingGroups.find((group) => group.tabs.some(([id]) => id === tab)) || settingGroups[0];
  return <Modal title="设置中心" kicker="SETTINGS HUB" onClose={onClose} footer={<><button className="secondary" onClick={onClose}>取消</button><button className="primary" onClick={() => void save()}>保存设置</button></>}>
    <div className="settings-layout settings-hub-layout">
      <nav>{settingGroups.map((group) => <button key={group.id} className={activeGroup.id === group.id ? "active" : ""} onClick={() => setTab(group.tabs[0][0])}><span aria-hidden="true">{group.id === "connection" ? "⌁" : group.id === "memory" ? "◇" : group.id === "voice" ? "◉" : group.id === "interface" ? "▣" : "⌘"}</span>{group.label}</button>)}</nav>
      <div className="settings-panel">
        <div className="settings-subnav">
          {activeGroup.tabs.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
          {activeGroup.id === "memory" && <><button onClick={onOpenMemory}>记忆内容 ↗</button><button onClick={onOpenKnowledge}>知识库 ↗</button></>}
          {activeGroup.id === "advanced" && <button onClick={onOpenDiagnostics}>系统诊断 ↗</button>}
        </div>
    {tab === "model" && <>
      <h3>人物与状态档案</h3>
      <p className="notice">这里同时管理人物设定和 API。完整结构化档案可单独编辑，保存后会继续沿用当前人物的长期关系与状态。</p>
      <div className="persona-config-actions"><button type="button" onClick={() => onOpenProfile("user")}>编辑用户档案</button><button type="button" onClick={() => onOpenProfile("assistant")}>编辑 AI 人设档案</button></div>
      <h3>语言模型 API</h3>
      <p className={`notice ${bool(draft.llm.credentials_configured) ? "" : "warning"}`}>{bool(draft.llm.credentials_configured) ? "真实 LLM API 已启用；保存后立即用于下一轮对话。" : "尚未配置 LLM API 密钥。未配置时会阻止发送，不会生成演示回复。"}</p>
      <div className="form-grid"><SelectField label="运行模式" value="openai" disabled options={[["openai", "真实 API（OpenAI 兼容）"]]} onChange={() => undefined} /><Field label="模型" value={draft.llm.model} onChange={(next) => update("llm", "model", next)} /><Field label="API 地址" value={draft.llm.base_url} onChange={(next) => update("llm", "base_url", next)} /><Field label="新 API 密钥（留空保持）" value={llmApiKey} type="password" placeholder={bool(draft.llm.credentials_configured) ? "已配置；输入新密钥可替换" : "输入 API 密钥"} onChange={(next) => setLlmApiKey(str(next))} /><Field label="温度" value={draft.llm.temperature} type="number" min={0} max={2} step={0.05} onChange={(next) => update("llm", "temperature", next)} /><Field label="最大 token" value={draft.llm.max_tokens} type="number" min={64} max={32768} onChange={(next) => update("llm", "max_tokens", next)} /></div>
      <h3>语音合成 API</h3>
      <p className="notice">上线版本默认使用云端流式 TTS，不随安装包分发本地 CosyVoice 模型；本地链路仍可在“实时语音”中切换。</p>
      <div className="form-grid"><Field label="SiliconFlow API 地址" value={draft.audio.tts_siliconflow_base_url} onChange={(next) => update("audio", "tts_siliconflow_base_url", next)} /><Field label="新 TTS API 密钥（留空保持）" value={ttsApiKey} type="password" placeholder={bool(draft.audio.tts_siliconflow_credentials_configured) ? "已配置；输入新密钥可替换" : "输入 SiliconFlow API 密钥"} onChange={(next) => setTtsApiKey(str(next))} /><SelectField label="云端模型" value={draft.audio.tts_siliconflow_model} options={[["fnlp/MOSS-TTSD-v0.5", "MOSS-TTSD v0.5"], ["FunAudioLLM/CosyVoice2-0.5B", "CosyVoice2 0.5B"]]} onChange={(next) => setDraft((current) => ({ ...current, audio: { ...current.audio, tts_siliconflow_model: next, tts_siliconflow_voice: next === "fnlp/MOSS-TTSD-v0.5" ? "fnlp/MOSS-TTSD-v0.5:alex" : "FunAudioLLM/CosyVoice2-0.5B:alex" } }))} /><Field label="音色 ID" value={draft.audio.tts_siliconflow_voice} onChange={(next) => update("audio", "tts_siliconflow_voice", next)} /><SelectField label="PCM 采样率" value={draft.audio.tts_siliconflow_sample_rate} options={[["16000", "16 kHz"], ["24000", "24 kHz（推荐）"], ["32000", "32 kHz"], ["44100", "44.1 kHz"]]} onChange={(next) => update("audio", "tts_siliconflow_sample_rate", Number(next))} /><Field label="增益 dB" value={draft.audio.tts_siliconflow_gain} type="number" min={-10} max={10} step={0.5} onChange={(next) => update("audio", "tts_siliconflow_gain", next)} /></div>
      <button className="inline-action" disabled={Boolean(audioBusy)} onClick={() => void testApiConnections()}>{audioBusy === "api-check" ? "正在自检…" : "自检 LLM + TTS API"}</button>
      <h3>人物设定</h3><div className="form-grid"><Field label="用户称呼" value={draft.persona.user_name} onChange={(next) => update("persona", "user_name", next)} /><Field label="角色名称" value={draft.persona.character_name} onChange={(next) => update("persona", "character_name", next)} /><Field label="用户设定" value={draft.persona.user_persona} type="textarea" onChange={(next) => update("persona", "user_persona", next)} /><Field label="角色系统提示" value={draft.persona.system_prompt} type="textarea" onChange={(next) => update("persona", "system_prompt", next)} /></div>
    </>}
    {tab === "avatar" && <><h3>人物头像</h3><p className="notice">上传图片并调整裁剪。聊天、人物卡和实时语音会立即使用同一份头像配置。</p><div className="avatar-settings-grid">{(["user", "assistant"] as Role[]).map((role) => <AvatarEditor key={role} role={role} entry={avatarDraft[role]} busy={avatarBusy === role} onUpload={(file) => void uploadAvatar(role, file)} onChange={(entry) => setAvatarDraft((current) => ({ ...current, [role]: entry }))} />)}</div></>}
      {tab === "rhythm" && <><h3>时间感知</h3><p className="notice">文字与语音对话都会记录服务端 UTC 时间、当地时区以及与上次真实用户消息的时间差。时间只作为本轮运行事实，不会自行修改人物档案。</p><h3>连续陪伴</h3><div className="toggle-grid"><Field label="无限制回复" value={draft.interaction?.unlimited_reply_enabled} type="checkbox" onChange={(next) => update("interaction", "unlimited_reply_enabled", next)} /></div><div className="form-grid"><Field label="连续陪伴轮次上限" value={draft.interaction?.unlimited_reply_max_rounds} type="number" min={1} max={50} step={1} onChange={(next) => update("interaction", "unlimited_reply_max_rounds", next)} /></div><p className="notice">仅在实时语音中生效，衔接间隔固定为 10 秒。每次 TTS 完整朗读结束后，角色会自主规划并继续话题；默认你只想听，不会催促回复。你随时可以插话，插话会改变后续话题方向，但不会关闭连续陪伴或清零轮次。进度只显示在语音页面，到达上限后自动停止。</p><h3>沉默后主动续接</h3><div className="toggle-grid"><Field label="允许 AI 在沉默后自然续接" value={draft.interaction?.idle_continuation_enabled} type="checkbox" onChange={(next) => update("interaction", "idle_continuation_enabled", next)} /></div><div className="form-grid"><Field label="文字对话等待秒数" value={draft.interaction?.text_idle_seconds} type="number" min={10} max={3600} step={10} onChange={(next) => update("interaction", "text_idle_seconds", next)} /><Field label="语音通话等待秒数" value={draft.interaction?.voice_idle_seconds} type="number" min={5} max={600} step={5} onChange={(next) => update("interaction", "voice_idle_seconds", next)} /></div><p className="notice">普通主动续接每个静默阶段最多说一次；连续陪伴开启时，语音模式优先使用上面的多轮逻辑。</p></>}
      {tab === "capabilities" && <>
        <h3>只读自动能力</h3>
        <p className="notice">总开关开启后，AI 可自行调用你允许的读取能力，不再逐次弹窗确认。一次查询、必要的补充模型调用和最终回答始终合并为同一轮回复。</p>
        <div className="toggle-grid"><Field label="允许只读自动能力" value={draft.capabilities?.master_enabled} type="checkbox" onChange={(next) => update("capabilities", "master_enabled", next)} /><Field label="自动查询本地知识" value={draft.capabilities?.local_knowledge_enabled} type="checkbox" onChange={(next) => update("capabilities", "local_knowledge_enabled", next)} /><Field label="允许联网搜索" value={draft.capabilities?.web_search_enabled} type="checkbox" onChange={(next) => update("capabilities", "web_search_enabled", next)} /><Field label="允许实时热点" value={draft.capabilities?.realtime_topics_enabled} type="checkbox" onChange={(next) => update("capabilities", "realtime_topics_enabled", next)} /><Field label="自然扩展相关话题" value={draft.capabilities?.topic_expansion_enabled} type="checkbox" onChange={(next) => update("capabilities", "topic_expansion_enabled", next)} /><Field label="沉默续接可参考热点" value={draft.capabilities?.proactive_hotspots_enabled} type="checkbox" onChange={(next) => update("capabilities", "proactive_hotspots_enabled", next)} /><Field label="回答中展示网页来源" value={draft.capabilities?.show_sources_enabled} type="checkbox" onChange={(next) => update("capabilities", "show_sources_enabled", next)} /></div>
        <h3>联网边界</h3><div className="form-grid"><Field label="联网超时秒数" value={draft.capabilities?.web_timeout_seconds} type="number" min={2} max={30} step={1} onChange={(next) => update("capabilities", "web_timeout_seconds", next)} /><Field label="搜索结果上限" value={draft.capabilities?.max_web_results} type="number" min={1} max={20} step={1} onChange={(next) => update("capabilities", "max_web_results", next)} /><Field label="打开原文上限" value={draft.capabilities?.max_web_pages} type="number" min={0} max={10} step={1} onChange={(next) => update("capabilities", "max_web_pages", next)} /><Field label="每页正文字符" value={draft.capabilities?.max_web_content_chars} type="number" min={2000} max={30000} step={1000} onChange={(next) => update("capabilities", "max_web_content_chars", next)} /></div>
        <p className="notice warning">该权限仅允许现有知识检索和公开网页 GET 读取。AI 无权读取本机配置、硬件、进程或服务健康状态，也不能执行命令、修改文件、上传资料、登录网站、发送消息、结束进程或读取密钥。网页内容不能修改人物 JSON，也不能作为用户偏好证据。</p>
      </>}
    {tab === "rag" && <><h3>检索开关</h3><div className="toggle-grid"><Field label="启用 RAG" value={draft.retrieval.rag_enabled} type="checkbox" onChange={(next) => update("retrieval", "rag_enabled", next)} /><Field label="知识库召回" value={draft.retrieval.knowledge_enabled} type="checkbox" onChange={(next) => update("retrieval", "knowledge_enabled", next)} /><Field label="会话记忆召回" value={draft.retrieval.chat_enabled} type="checkbox" onChange={(next) => update("retrieval", "chat_enabled", next)} /><Field label="JSON 字段记忆" value={draft.retrieval.structured_memory_enabled} type="checkbox" onChange={(next) => update("retrieval", "structured_memory_enabled", next)} /><Field label="BM25+ 词法召回" value={draft.retrieval.bm25_enabled} type="checkbox" onChange={(next) => update("retrieval", "bm25_enabled", next)} /><Field label="向量召回" value={draft.retrieval.vector_enabled} type="checkbox" onChange={(next) => update("retrieval", "vector_enabled", next)} /><Field label="本地精排（需模型）" value={draft.retrieval.reranker_enabled} type="checkbox" onChange={(next) => update("retrieval", "reranker_enabled", next)} /><Field label="公平曝光保护" value={draft.retrieval.fairness_enabled} type="checkbox" onChange={(next) => update("retrieval", "fairness_enabled", next)} /><Field label="时间衰减" value={draft.retrieval.temporal_enabled} type="checkbox" onChange={(next) => update("retrieval", "temporal_enabled", next)} /></div><h3>召回参数</h3><div className="form-grid"><Field label="知识召回数" value={draft.retrieval.knowledge_k} type="number" onChange={(next) => update("retrieval", "knowledge_k", next)} /><Field label="记忆召回数" value={draft.retrieval.chat_k} type="number" onChange={(next) => update("retrieval", "chat_k", next)} /><Field label="相似度阈值" value={draft.retrieval.similarity_threshold} type="number" step={0.05} onChange={(next) => update("retrieval", "similarity_threshold", next)} /><Field label="RRF 常数" value={draft.retrieval.rrf_k} type="number" onChange={(next) => update("retrieval", "rrf_k", next)} /><Field label="候选放大倍数" value={draft.retrieval.candidate_multiplier} type="number" onChange={(next) => update("retrieval", "candidate_multiplier", next)} /><Field label="精排候选数" value={draft.retrieval.reranker_top_n} type="number" onChange={(next) => update("retrieval", "reranker_top_n", next)} /><Field label="轮次衰减" value={draft.retrieval.decay_rounds} type="number" onChange={(next) => update("retrieval", "decay_rounds", next)} /><Field label="低曝光保留比例" value={draft.retrieval.low_exposure_ratio} type="number" step={0.05} onChange={(next) => update("retrieval", "low_exposure_ratio", next)} /><Field label="同字段族上限" value={draft.retrieval.memory_family_limit} type="number" onChange={(next) => update("retrieval", "memory_family_limit", next)} /><Field label="饥饿保护轮次" value={draft.retrieval.starvation_rounds} type="number" onChange={(next) => update("retrieval", "starvation_rounds", next)} /></div><p className="notice">BM25+ 与向量先独立排序，再由 RRF 融合；Boost 有总上限。本地精排模型缺失时会安全退回 RRF，不会在线下载。无 JSON 标签文本只进入限额候选池。</p><h3>知识分块</h3><div className="form-grid"><Field label="子块长度" value={draft.knowledge.child_size} type="number" onChange={(next) => update("knowledge", "child_size", next)} /><Field label="父块长度" value={draft.knowledge.parent_size} type="number" onChange={(next) => update("knowledge", "parent_size", next)} /><Field label="重叠字符" value={draft.knowledge.overlap} type="number" onChange={(next) => update("knowledge", "overlap", next)} /></div></>}
    {tab === "protocol" && <><h3>生成与 JSON 写回</h3><div className="form-grid"><Field label="协议模式" value={draft.protocol.mode} onChange={(next) => update("protocol", "mode", next)} /><Field label="角色审计模型（留空复用主模型）" value={draft.llm.role_audit_model} onChange={(next) => update("llm", "role_audit_model", next)} /></div><div className="toggle-grid"><Field label="自动结构修复" value={draft.protocol.auto_repair} type="checkbox" onChange={(next) => update("protocol", "auto_repair", next)} /><Field label="显示写回诊断" value={draft.protocol.diagnostics} type="checkbox" onChange={(next) => update("protocol", "diagnostics", next)} /><Field label="复杂角色异步审计" value={draft.llm.role_audit_enabled} type="checkbox" onChange={(next) => update("llm", "role_audit_enabled", next)} /></div><p className="notice">回复立即流式展示；JSON 每轮最多写入三个经过路径、证据和 revision 校验的叶子 Patch。复杂角色审计只在本轮完成后运行，不能替换已显示或已朗读的内容，严重偏移只影响下一轮。</p></>}
    {tab === "audio" && <>
      <h3>语音合成</h3>
      <div className="form-grid"><SelectField label="TTS 链路" value={draft.audio.tts_provider} disabled={providerBusy} options={[["browser", "关闭声音（仅文字）"], ["gpt-sovits", "GPT-SoVITS 二次元声线"], ["cosyvoice", "本地 CosyVoice 声音克隆"], ["qwen3-vllm", "Qwen3 高质量活人感"], ["siliconflow", "SiliconFlow 云端流式 TTS"]]} onChange={(next) => void switchTtsProvider(next)} /><Field label="速度" value={draft.audio.tts_speed} type="number" min={0.5} max={2} step={0.1} onChange={(next) => update("audio", "tts_speed", next)} /></div>
      <p className={`notice ${providerStatus.startsWith("切换失败") ? "warning" : ""}`}>{providerStatus}</p>
      {ttsProvider === "cosyvoice" && <><div className="form-grid"><Field label="CosyVoice Worker" value={draft.audio.tts_worker_url} onChange={(next) => update("audio", "tts_worker_url", next)} /><Field label="识别出的参考文本（请校对）" value={draft.audio.tts_reference_text} type="textarea" onChange={(next) => update("audio", "tts_reference_text", next)} placeholder="上传后自动识别；必须与参考音频实际说出的内容一致" /></div><p className="notice warning">本地 CosyVoice 是可选链路，上线安装包不包含其模型。参考文本必须与音频逐字匹配；实时语音只输出并朗读角色亲口说出的自然口语，不使用括号动作旁白。</p><div className="reference-panel"><div><strong>本地参考音频</strong><small>{audioStatus}</small></div><div><label className="secondary upload-button">{audioBusy === "upload" ? "上传中…" : bool(draft.audio.tts_reference_configured) ? "替换音频" : "选择并上传"}<input hidden disabled={Boolean(audioBusy)} type="file" accept=".wav,.mp3,.flac,.m4a,.ogg,audio/*" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadReference(file); event.currentTarget.value = ""; }} /></label><button className="secondary" disabled={Boolean(audioBusy) || !bool(draft.audio.tts_reference_configured)} onClick={() => void recognizeReference()}>{audioBusy === "recognize" ? "识别中…" : "识别音频文字"}</button><button className="secondary" disabled={Boolean(audioBusy) || !bool(draft.audio.tts_reference_configured)} onClick={() => void clearReference()}>清除</button></div></div></>}
      {ttsProvider === "gpt-sovits" && <><div className="form-grid"><SelectField label="GPT-SoVITS 音色" value={draft.audio.tts_gpt_sovits_voice || gptVoices.active_voice} disabled={providerBusy} options={gptVoices.items.length ? gptVoices.items.map((voice) => [voice.id, `${voice.label}${voice.installed ? " · 已安装" : " · 需在启动器安装"}`] as [string, string]) : [["v4-changli", "V4-长离"], ["v4-yae-miko", "V4-八重神子"], ["v2proplus-kafka", "V2ProPlus-卡芙卡"]]} onChange={(next) => void switchGptVoice(next)} /><Field label="GPT-SoVITS Worker" value={draft.audio.tts_gpt_sovits_worker_url || "http://127.0.0.1:5055"} onChange={(next) => update("audio", "tts_gpt_sovits_worker_url", next)} /></div><p className="notice warning">音色模型与原 CosyVoice 完全分离，由启动器按需安装。V4 原生输出 48 kHz；卡芙卡实际为 V2ProPlus。第三方角色音色仅用于本地非商业验证，正式上线前必须取得对应权利方授权。</p></>}
      {ttsProvider === "qwen3-vllm" && <><div className="form-grid"><SelectField label="Qwen3 音色" value={draft.audio.tts_qwen3_vllm_voice || qwenVoices.active_voice} disabled={providerBusy} options={qwenVoices.items.length ? qwenVoices.items.map((voice) => [voice.id, voice.label] as [string, string]) : [["serena", "Serena · 温柔成年女声（运行时未就绪）"]]} onChange={(next) => void switchQwenVoice(next)} /><Field label="Qwen3 服务地址" value={draft.audio.tts_qwen3_vllm_url || "http://127.0.0.1:8091"} onChange={(next) => update("audio", "tts_qwen3_vllm_url", next)} /><Field label="模型名" value={draft.audio.tts_qwen3_vllm_model || "mindspace-qwen3-tts"} onChange={(next) => update("audio", "tts_qwen3_vllm_model", next)} /></div><p className="notice">Qwen3 使用 CustomVoice 固定 Serena speaker、固定随机种子和整篇单次合成。正文完成并通过格式清理后立即提交，不等待落库收尾；语气指令只控制语速、笑声、换气和情绪，不重新描述或改变音色。</p></>}
      {ttsProvider === "browser" && <p className="notice">当前关闭声音，只保留文字对话。启动器与应用内会显示相同状态，也不会加载本地 TTS 或占用额外显存。</p>}
      {ttsProvider === "siliconflow" && <p className="notice">云端 API 参数已集中到“模型与角色”。此处只选择链路与播放速度；逐句流式播放、首句抢跑和插话打断与本地链路一致，实时语音只输出可直接朗读的自然口语。</p>}
      <div className="row-actions"><button className="primary" disabled={Boolean(audioBusy)} onClick={() => void testTts()}>{audioBusy === "test" ? "生成中…" : "生成并试听 TTS"}</button></div>
      <Field label="回复时自动朗读" value={draft.audio.auto_tts} type="checkbox" onChange={(next) => update("audio", "auto_tts", next)} />
      <h3>实时识别与环境噪声</h3>
      <div className="toggle-grid"><Field label="启用人物与 JSON 动态词表" value={draft.audio.asr_hotwords_enabled} type="checkbox" onChange={(next) => update("audio", "asr_hotwords_enabled", next)} /><Field label="含糊停顿动态断句" value={draft.audio.asr_dynamic_endpointing} type="checkbox" onChange={(next) => update("audio", "asr_dynamic_endpointing", next)} /><Field label="Nano 整句复核" value={draft.audio.asr_final_refinement_enabled} type="checkbox" onChange={(next) => update("audio", "asr_final_refinement_enabled", next)} /></div>
      <div className="form-grid"><Field label="ASR 提供方" value={draft.audio.asr_provider} onChange={(next) => update("audio", "asr_provider", next)} /><Field label="ASR 模型" value={draft.audio.asr_model} onChange={(next) => update("audio", "asr_model", next)} /><Field label="静音断句毫秒" value={draft.audio.asr_silence_ms} type="number" min={250} max={3000} onChange={(next) => update("audio", "asr_silence_ms", next)} /><Field label="多段话合并窗口毫秒" value={draft.audio.asr_utterance_merge_ms} type="number" min={300} max={3000} step={50} onChange={(next) => update("audio", "asr_utterance_merge_ms", next)} /></div><p className="notice">语音入口不再等待环境噪声校准；浏览器只保留回声消除和自动增益，FunASR VAD 负责判断真实人声。Paraformer 保持实时字幕，Nano 仅在整句结束时低优先级复核。</p>
      <h3>语音情绪感知 · 实验性</h3>
      <div className="toggle-grid"><Field label="情绪侧链接口（暂时停用）" value={false} type="checkbox" onChange={() => undefined} /></div>
      <p className="advanced-note">情绪分析在本轮回复完成后后台执行，不再等待或延迟当前回复；完成后的状态仅供下一轮语音调整语气。</p>
      <p className="notice">当前版本不加载情绪模型，也不执行声学或文本情绪分析；仅保留后端接口，便于后续按需接入。</p>
      <h3>AI 播放完：短回复优先</h3><div className="form-grid"><Field label="监听最低门槛 dBFS" value={draft.audio.asr_listening_energy_threshold_db} type="number" min={-60} max={-15} step={1} onChange={(next) => update("audio", "asr_listening_energy_threshold_db", next)} /><Field label="监听最短发声毫秒" value={draft.audio.asr_listening_min_speech_ms} type="number" min={60} max={1000} step={20} onChange={(next) => update("audio", "asr_listening_min_speech_ms", next)} /></div>
      <h3>AI 播放中：三重确认后打断</h3><div className="form-grid"><Field label="插话最低门槛 dBFS" value={draft.audio.asr_barge_in_energy_threshold_db} type="number" min={-60} max={-15} step={1} onChange={(next) => update("audio", "asr_barge_in_energy_threshold_db", next)} /><Field label="插话最短发声毫秒" value={draft.audio.asr_barge_in_min_speech_ms} type="number" min={120} max={1500} step={20} onChange={(next) => update("audio", "asr_barge_in_min_speech_ms", next)} /><Field label="疑似声音释放毫秒" value={draft.audio.asr_candidate_release_ms} type="number" min={80} max={1000} step={20} onChange={(next) => update("audio", "asr_candidate_release_ms", next)} /></div>
      <div className="toggle-grid"><Field label="未达到打断条件的有效文字稍后发送" value={draft.audio.asr_deferred_during_playback} type="checkbox" onChange={(next) => update("audio", "asr_deferred_during_playback", next)} /><Field label="合并结束后自动发送" value={draft.audio.asr_auto_send} type="checkbox" onChange={(next) => update("audio", "asr_auto_send", next)} /></div>
      <p className="notice">候选噪声只降低播放音量；能量、FSMN-VAD 与有效识别共同确认后才打断。AI 尚未出声时，后续语音会合并进同一用户轮次；播放中未达到打断条件但识别出有效文字时，会在播放结束后统一发送。</p>
    </>}
    {tab === "vocabulary" && <>
      <h3>新增个人词条</h3>
      <p className="notice">词表只参与本地 ASR 解码与确定性纠偏，不进入 Prompt，也不会触发额外 LLM 调用。人物名称和专有名词使用高强化；三份 JSON 的有效字段会按 revision 自动生成轻度词条。</p>
      <div className="form-grid"><Field label="标准写法" value={vocabularyTerm} onChange={(next) => setVocabularyTerm(str(next))} placeholder="例如：长离" /><Field label="常见误识别（逗号分隔）" value={vocabularyAliases} onChange={(next) => setVocabularyAliases(str(next))} placeholder="例如：长利，常离" /><SelectField label="强化等级" value={vocabularyPriority} options={[["critical", "最高 · 明确纠偏"], ["high", "高 · 人名/专名"], ["medium", "中 · 当前实体"], ["low", "轻 · 普通字段"]]} onChange={(next) => setVocabularyPriority(next as ASRVocabularyEntry["priority"])} /></div>
      <div className="row-actions"><button className="primary" disabled={vocabularyBusy || !vocabularyTerm.trim()} onClick={() => void addVocabularyEntry()}>{vocabularyBusy ? "保存中…" : "新增并立即生效"}</button></div>
      <h3>词表测试</h3><div className="vocabulary-test"><Field label="输入一段可能识别错误的文字" value={vocabularyTest} onChange={(next) => setVocabularyTest(str(next))} placeholder="例如：我想换成长利的声音" /><button className="secondary" disabled={vocabularyBusy || !vocabularyTest.trim()} onClick={() => void testVocabulary()}>测试纠偏</button></div>{vocabularyTestResult && <p className="notice">{vocabularyTestResult}</p>}
      <h3>当前词表</h3>
      <div className="vocabulary-summary"><span>个人 <b>{num(vocabulary?.counts.manual)}</b></span><span>JSON 自动 <b>{num(vocabulary?.counts.profile)}</b></span><span>系统 <b>{num(vocabulary?.counts.system)}</b></span><span>解码热词 <b>{vocabulary?.decoder_hotwords.length || 0}</b></span><small>revision {vocabulary?.revision || "读取中"}</small></div>
      <label className="search-box vocabulary-search"><span>⌕</span><input value={vocabularyQuery} onChange={(event) => setVocabularyQuery(event.target.value)} placeholder="搜索标准词、别名、来源字段" /></label>
      <div className="vocabulary-list">{(vocabulary?.entries || []).filter((item) => !vocabularyQuery.trim() || `${item.term} ${item.aliases.join(" ")} ${item.source_field} ${item.category}`.toLowerCase().includes(vocabularyQuery.trim().toLowerCase())).slice(0, 160).map((item) => <article key={item.id} className={!item.enabled ? "disabled" : ""}><div><strong>{item.term}</strong><span className={`priority ${item.priority}`}>{item.priority === "critical" ? "最高" : item.priority === "high" ? "高" : item.priority === "medium" ? "中" : "轻"}</span><small>{item.category} · {item.source === "manual" ? "个人" : item.source === "profile" ? "JSON 自动" : "系统"}</small>{item.aliases.length > 0 && <p>易错：{item.aliases.join("、")}</p>}{item.source_field && <p className="source-field">{item.source_field}</p>}</div>{item.source === "manual" ? <div className="vocabulary-actions"><button className="secondary" disabled={vocabularyBusy} onClick={() => void saveManualVocabulary((vocabulary?.entries || []).filter((entry) => entry.source === "manual").map((entry) => entry.id === item.id ? { ...entry, enabled: !entry.enabled } : entry))}>{item.enabled ? "停用" : "启用"}</button><button className="danger-text" disabled={vocabularyBusy} onClick={async () => { if (await styledConfirm({ title: `删除词条“${item.term}”？`, message: "删除后，该词不会再作为个人识别词参与语音解码。", confirmLabel: "删除词条", danger: true })) await saveManualVocabulary((vocabulary?.entries || []).filter((entry) => entry.source === "manual" && entry.id !== item.id)); }}>删除</button></div> : <span className="read-only-badge">自动</span>}</article>)}</div>
      {(vocabulary?.entries.length || 0) > 160 && !vocabularyQuery && <p className="notice">自动词条较多，当前只展示前 160 条；使用搜索可定位其余词条。</p>}
    </>}
    {tab === "appearance" && <><h3>界面偏好</h3><div className="form-grid"><SelectField label="主题" value={draft.appearance.theme} options={[["mindscape", "Mindscape 暖色"], ["dark", "深色研究界面"]]} onChange={(next) => update("appearance", "theme", next)} /><SelectField label="界面密度" value={draft.appearance.density} options={[["chat", "舒适对话"], ["research", "紧凑研究"]]} onChange={(next) => update("appearance", "density", next)} /><SelectField label="字体大小" value={draft.appearance.font_scale ?? 1.3} options={[["1", "标准（100%）"], ["1.15", "较大（115%）"], ["1.3", "默认大字（130%）"], ["1.45", "更大（145%）"], ["1.6", "特大（160%）"]]} onChange={(next) => update("appearance", "font_scale", Number(next))} /><Field label="语言" value={draft.appearance.language} onChange={(next) => update("appearance", "language", next)} /></div><p className="notice">全屏或大屏窗口会在所选字号上自动再放大，缩回普通窗口后恢复；设置保存后立即生效。</p></>}
  </div></div></Modal>;
}

function KnowledgeDialog({ onClose, onDirty, notify }: { onClose: () => void; onDirty: (dirty: boolean) => void; notify: (message: string) => void }) {
  const [items, setItems] = useState<KnowledgeItem[]>([]); const [query, setQuery] = useState(""); const [text, setText] = useState(""); const [source, setSource] = useState("手动录入"); const [loading, setLoading] = useState(false);
  const load = useCallback(async () => { setLoading(true); try { const result = await request<{ items: KnowledgeItem[] }>(`/api/v1/knowledge?query=${encodeURIComponent(query)}`); setItems(result.items); } catch (error) { notify((error as Error).message); } finally { setLoading(false); } }, [notify, query]);
  useEffect(() => { void load(); }, [load]); useEffect(() => { onDirty(Boolean(text.trim())); return () => onDirty(false); }, [onDirty, text]);
  const add = async () => { try { const result = await request<{ count: number }>("/api/v1/knowledge", { method: "POST", body: JSON.stringify({ text, source }) }); setText(""); notify(`已写入 ${result.count} 个知识块`); await load(); } catch (error) { notify((error as Error).message); } };
  const upload = async (file: File) => { const form = new FormData(); form.append("file", file); try { const result = await request<{ count: number }>("/api/v1/knowledge/upload", { method: "POST", body: form }); notify(`已从 ${file.name} 导入 ${result.count} 个知识块`); await load(); } catch (error) { notify((error as Error).message); } };
  return <Modal title="全局知识库" kicker="KNOWLEDGE BASE" onClose={onClose}><div className="knowledge-layout"><section className="knowledge-compose"><h3>新增资料</h3><Field label="来源名称" value={source} onChange={(next) => setSource(str(next))} /><Field label="知识内容" value={text} type="textarea" onChange={(next) => setText(str(next))} placeholder="粘贴文本，空行会成为自然分块边界" /><div className="row-actions"><label className="upload-button">上传 TXT / MD / JSON<input hidden type="file" accept=".txt,.md,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.currentTarget.value = ""; }} /></label><button className="primary" disabled={!text.trim()} onClick={() => void add()}>保存知识</button></div></section><section className="knowledge-manage"><div className="manage-head"><h3>知识块 <b>{items.length}</b></h3><label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索内容或来源" /></label></div>{loading ? <div className="empty-mini">正在读取知识库…</div> : <div className="knowledge-list">{items.length ? items.map((item) => <article key={item.chunk_id}><header><span>{item.source}</span><button onClick={async () => { if (!(await styledConfirm({ title: "删除这个知识块？", message: "删除后，该内容不会再参与知识检索。", confirmLabel: "删除知识", danger: true }))) return; await request(`/api/v1/knowledge/${item.chunk_id}`, { method: "DELETE" }); notify("知识块已删除"); await load(); }}>删除</button></header><p>{item.text}</p><small>{item.chunk_id} · {formatTime(item.created_at)}</small></article>) : <div className="empty-mini">知识库中暂无匹配内容</div>}</div>}</section></div></Modal>;
}

function MemoryDialog({ characterId, onClose, onDirty, notify }: { characterId: string; onClose: () => void; onDirty: (dirty: boolean) => void; notify: (message: string) => void }) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [includeHistory, setIncludeHistory] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [editingKey, setEditingKey] = useState("");
  const [draft, setDraft] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await request<{ items: MemoryItem[] }>(`/api/v1/memory/items?include_history=${includeHistory ? "true" : "false"}&character_id=${encodeURIComponent(characterId)}`);
      setItems(result.items);
    } catch (error) { notify((error as Error).message); }
    finally { setLoading(false); }
  }, [characterId, includeHistory, notify]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { onDirty(Boolean(editingKey)); return () => onDirty(false); }, [editingKey, onDirty]);
  const filtered = items.filter((item) => !query.trim() || `${item.category} ${item.display_name} ${item.value} ${item.source_text || ""}`.toLowerCase().includes(query.trim().toLowerCase()));
  const save = async (item: MemoryItem) => {
    try {
      await request(`/api/v1/memory/items/${encodeURIComponent(item.memory_key)}`, { method: "PUT", body: JSON.stringify({ value: draft }) });
      setEditingKey(""); setDraft(""); notify("记忆已更新，并同步到权威档案"); await load();
    } catch (error) { notify((error as Error).message); }
  };
  const remove = async (item: MemoryItem) => {
    if (!(await styledConfirm({ title: `删除“${item.display_name}”？`, message: String(item.value), detail: "权威档案会同步更新，这条内容也会退出后续召回。", confirmLabel: "删除记忆", danger: true }))) return;
    try { await request(`/api/v1/memory/items/${encodeURIComponent(item.memory_key)}`, { method: "DELETE" }); notify("记忆已删除并退出召回"); await load(); }
    catch (error) { notify((error as Error).message); }
  };
  const restore = async (item: MemoryItem) => {
    try { await request("/api/v1/memory/restore", { method: "POST", body: JSON.stringify({ memory_key: item.memory_key }) }); notify("记忆已恢复并同步到权威档案"); await load(); }
    catch (error) { notify((error as Error).message); }
  };
  return <Modal title="记忆中心" kicker="MEMORY CENTER" onClose={onClose}><div className="memory-toolbar"><label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索分类、内容或来源" /></label><label className="memory-history-toggle"><input type="checkbox" checked={includeHistory} onChange={(event) => setIncludeHistory(event.target.checked)} />显示已失效记忆</label></div><p className="advanced-note">这里展示由已提交 JSON 字段形成的记忆。修改、删除和恢复会同步权威档案；技术标签与排序权重不会进入对话 Prompt。</p>{loading ? <div className="empty-mini">正在读取记忆…</div> : <div className="memory-list">{filtered.length ? filtered.map((item) => <article className={item.status === "invalidated" ? "invalidated" : ""} key={`${item.status}-${item.memory_key}-${item.invalidated_at || ""}`}><header><div><span>{item.category}</span><strong>{item.display_name}</strong></div><small>{item.status === "active" ? "当前有效" : "已失效"} · {formatTime(item.updated_at || item.invalidated_at)}</small></header>{editingKey === item.memory_key && item.status === "active" ? <div className="memory-edit"><input autoFocus value={draft} onChange={(event) => setDraft(event.target.value)} /><button className="secondary" onClick={() => { setEditingKey(""); setDraft(""); }}>取消</button><button className="primary" disabled={!draft.trim()} onClick={() => void save(item)}>保存</button></div> : <p className="memory-value">{friendlyValue(item.value)}</p>}<details><summary>为什么记住</summary><p>{item.source_text || "来自用户在记忆中心的明确操作"}</p>{item.session_id && <small>来源会话：{item.session_id}</small>}</details><footer>{item.status === "active" ? <><button onClick={() => { setEditingKey(item.memory_key); setDraft(String(item.value)); }}>修改</button><button className="danger-text" onClick={() => void remove(item)}>删除</button></> : <button onClick={() => void restore(item)}>恢复这条记忆</button>}</footer></article>) : <div className="empty-mini">暂无匹配的结构化记忆。只有成功写入 JSON 的字段会出现在这里。</div>}</div>}</Modal>;
}

const PROFILE_FIELD_LABELS: Record<string, string> = {
  identity: "身份", preferred_name: "常用称呼", real_name: "真实姓名", gender: "第一认同性别", occupation: "职业", language: "语言",
  name: "角色名称", self_description: "角色自述", relationship_to_user: "与用户关系",
  communication_preferences: "交流偏好", preferred_tone: "偏好语气", response_length: "回复长度",
  explanation_depth: "解释深度", preferred_names: "喜欢的称呼", disliked_expressions: "不喜欢的表达",
  stable_preferences: "稳定偏好", likes: "喜欢", dislikes: "不喜欢", interests: "兴趣", habits: "习惯",
  background: "经历", important_experiences: "重要经历", behavior_requirements: "用户行为要求",
  personality: "角色性格", core_traits: "核心性格", speech_style: "表达风格",
  relationship_rules: "关系规则", relationship_definition: "关系定义", preferred_interactions: "偏好互动",
  conflict_behavior: "冲突处理", repair_behavior: "关系修复", behavior_rules: "角色行为规则",
  always_apply: "始终执行", contextual_rules: "情境规则", avoid: "避免行为", hard_boundaries: "硬性边界",
  continuity: "关系延续", important_shared_experiences: "共同经历", persistent_attitudes: "持续态度",
  long_term_goals: "长期目标", relationship_state: "当前关系", current_stage: "当前阶段",
  roleplay: "角色演绎", selfhood: "角色自我", values: "价值取向", personal_opinions: "个人看法",
  flaws: "缺点", contradictions: "内在矛盾", private_interests: "私人兴趣", personal_goals: "个人目标",
  agency: "自主性", initiative_sources: "主动话题来源", self_directed_choices: "自主选择方式",
  attention_triggers: "注意力触发", boredom_triggers: "厌倦触发", default_conflict_posture: "默认分歧立场",
  voice: "角色语言", cadence: "语言节奏", preferred_vocabulary: "常用词", disliked_phrases: "禁用套话",
  humor_style: "幽默方式", action_dialogue_balance: "动作与台词比例", scenario_baseline: "常态场景",
  post_history_note: "历史后角色校准", r18_protocol: "用户私有 R18 描写协议",
  examples: "分类对话示例", casual: "日常示例",
  disagreement: "分歧示例", initiative: "主动表达示例", scene_transition: "转场示例",
  intimate: "亲密互动示例", roleplay_state: "角色场景状态", scene: "当前场景",
  location: "地点", time_anchor: "时间锚点", character_outfit: "角色穿着",
  character_posture: "角色姿态", character_activity: "角色正在做的事", active_objects: "场景物件",
  open_threads: "未完互动", last_transition: "最近转场", updated_round: "更新轮次",
  agent_drive: "角色当前驱动力", current_intent: "角色当前意图", own_activity: "角色自身活动",
  unresolved_choice: "角色未决选择", initiative_type_history: "主动类型历史",
  current_tone: "当前氛围", recent_conflicts: "近期冲突", recent_positive_events: "近期积极事件",
  unresolved_issues: "未解决事项", user_state: "用户当前状态", current_goal: "当前目标",
  current_task: "当前任务", current_topic: "当前话题", temporary_preferences: "临时偏好",
  current_emotional_cues: "当前情绪线索", ai_state: "AI 当前状态", pending_responses: "待回应事项",
  current_intentions: "当前意图", session_state: "会话状态", session_summary: "会话摘要",
  open_questions: "开放问题", pending_actions: "待办事项", active_entities: "当前实体",
};
const PROFILE_TECHNICAL_FIELDS = new Set(["schema_version", "profile_type", "revision", "updated_at"]);

function ProfileFieldEditor({ fieldKey, value, path, onChange }: { fieldKey: string; value: unknown; path: string[]; onChange: (path: string[], value: unknown) => void }) {
  const label = PROFILE_FIELD_LABELS[fieldKey] || fieldKey;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return <fieldset className="profile-form-section"><legend>{label}</legend><div className="profile-form-grid">{Object.entries(value as Record<string, unknown>).filter(([key]) => !PROFILE_TECHNICAL_FIELDS.has(key)).map(([key, item]) => <ProfileFieldEditor key={`${path.join(".")}.${key}`} fieldKey={key} value={item} path={[...path, key]} onChange={onChange} />)}</div></fieldset>;
  }
  if (Array.isArray(value)) {
    const isExample = path.includes("examples");
    return <label className="profile-form-field profile-form-list"><span>{label}</span><textarea aria-label={label} value={value.map(String).join("\n")} placeholder={isExample ? "每行一例，例如：用户：…… → 角色：……" : "每行一项；留空表示暂无记录"} onChange={(event) => onChange(path, event.target.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean))} />{isExample && <small>只会按当前情境选取最多两条，不会整份塞入 Prompt。</small>}</label>;
  }
  if (typeof value === "boolean") {
    return <label className="profile-form-field profile-form-check"><input aria-label={label} type="checkbox" checked={value} onChange={(event) => onChange(path, event.target.checked)} /><span>{label}</span></label>;
  }
  if (fieldKey === "gender" && path.includes("identity")) {
    return <label className="profile-form-field"><span>{label}</span><select aria-label={label} value={String(value)} onChange={(event) => onChange(path, event.target.value)}><option value="男">男</option><option value="女">女</option></select><small>用户手动保存后作为模型最高优先级身份；AI 不能自行改写。</small></label>;
  }
  return <label className="profile-form-field"><span>{label}</span><input aria-label={label} type={typeof value === "number" ? "number" : "text"} value={value == null ? "" : String(value)} onChange={(event) => onChange(path, typeof value === "number" ? Number(event.target.value) : event.target.value)} /></label>;
}

function ProfileDialog({ characterId, initialName, onClose, onDirty, onOpenConnection, onSaved, notify }: { characterId: string; initialName: Role | "state"; onClose: () => void; onDirty: (dirty: boolean) => void; onOpenConnection: () => void; onSaved: () => void; notify: (message: string) => void }) {
  const [name, setName] = useState(initialName); const [document, setDocument] = useState(""); const [savedDocument, setSavedDocument] = useState(""); const [history, setHistory] = useState<ProfileHistoryItem[]>([]); const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [mode, setMode] = useState<"form" | "json">("form"); const [error, setError] = useState("");
  const parsed = useMemo(() => { try { const value = JSON.parse(document); return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null; } catch { return null; } }, [document]);
  const characterQuery = name === "user" || !characterId ? "" : `?character_id=${encodeURIComponent(characterId)}`;
  const load = useCallback(async () => { setLoading(true); setError(""); try { const [value, versions] = await Promise.all([request<Record<string, unknown>>(`/api/v1/profiles/${name}${characterQuery}`), request<{ items: ProfileHistoryItem[] }>(`/api/v1/profiles/${name}/history${characterQuery}`).catch(() => ({ items: [] }))]); const serialized = JSON.stringify(value, null, 2); setDocument(serialized); setSavedDocument(serialized); setHistory(versions.items); } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); } finally { setLoading(false); } }, [characterQuery, name, notify]);
  useEffect(() => { void load(); }, [load]); useEffect(() => { onDirty(document !== savedDocument); return () => onDirty(false); }, [document, onDirty, savedDocument]);
  const updateValue = useCallback((path: string[], value: unknown) => { if (!parsed) return; const next = structuredClone(parsed); let cursor: Record<string, unknown> = next; path.slice(0, -1).forEach((key) => { cursor = cursor[key] as Record<string, unknown>; }); cursor[path[path.length - 1]] = value; setDocument(JSON.stringify(next, null, 2)); setError(""); }, [parsed]);
  const save = async () => { if (!parsed) { setError("JSON 格式无效，请修正后再保存。"); return; } setSaving(true); setError(""); try { const result = await request<{ document: Record<string, unknown> }>(`/api/v1/profiles/${name}${characterQuery}`, { method: "PUT", body: JSON.stringify(parsed) }); const serialized = JSON.stringify(result.document, null, 2); setDocument(serialized); setSavedDocument(serialized); onSaved(); notify("档案已保存，人物名称与后续对话将使用新版本"); } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); } finally { setSaving(false); } };
  const restorePrevious = async () => { const previous = history[0]; if (!previous || !parsed) return; if (!(await styledConfirm({ title: `恢复修订 ${previous.revision}？`, message: "当前版本仍会保留在历史中，并会生成一个新的修订版本。", confirmLabel: "恢复版本" }))) return; setSaving(true); setError(""); try { const result = await request<{ document: Record<string, unknown> }>(`/api/v1/profiles/${name}/restore${characterQuery}`, { method: "POST", body: JSON.stringify({ version_id: previous.version_id, expected_revision: parsed.revision }) }); const serialized = JSON.stringify(result.document, null, 2); setDocument(serialized); setSavedDocument(serialized); notify("已恢复上一版本，并生成新的修订"); await load(); } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); } finally { setSaving(false); } };
  const switchProfile = async (id: Role | "state") => { if (document !== savedDocument && !(await styledConfirm({ title: "放弃未保存的修改？", message: "切换档案后，本页尚未保存的编辑会丢失。", confirmLabel: "继续切换", danger: true }))) return; setName(id); };
  return <Modal
    title="人设工作区"
    kicker="PERSONA WORKSPACE"
    onClose={onClose}
    footer={<>
      <button className="secondary" disabled={loading || saving || !history.length} onClick={() => void restorePrevious()}>恢复上一版本</button>
      <button className="secondary" disabled={loading || saving} onClick={() => void load()}>放弃修改并重载</button>
      <button className="primary" disabled={loading || saving || !parsed || document === savedDocument} onClick={() => void save()}>{saving ? "正在保存…" : "保存档案"}</button>
    </>}
  >
    <div className="profile-tabs persona-workspace-tabs">
      {([["user", "用户档案"], ["assistant", "AI 档案"], ["state", "运行状态"]] as Array<[Role | "state", string]>).map(([id, label]) => <button className={name === id ? "active" : ""} key={id} onClick={() => switchProfile(id)}>{label}</button>)}
      <button className="profile-connection-tab" onClick={onOpenConnection}>API 连接 <span>↗</span></button>
    </div>
    <div className="profile-editor-toolbar">
      <p className="advanced-note">用户修改直接生效并生成新 revision；AI 后续写回必须基于该 revision。当前保留 {history.length} 个可恢复版本。</p>
      <div><button className={mode === "form" ? "active" : ""} onClick={() => setMode("form")}>表单编辑</button><button className={mode === "json" ? "active" : ""} onClick={() => setMode("json")}>高级 JSON</button></div>
    </div>
    {error && <div className="profile-editor-error" role="alert">{error}</div>}
    {loading ? <div className="empty-mini">正在载入档案…</div> : mode === "json" ? <textarea aria-label="高级 JSON 编辑器" className="json-editor" value={document} onChange={(event) => { setDocument(event.target.value); setError(""); }} spellCheck={false} /> : parsed ? <div className="profile-form">{Object.entries(parsed).filter(([key]) => !PROFILE_TECHNICAL_FIELDS.has(key)).map(([key, value]) => <ProfileFieldEditor key={key} fieldKey={key} value={value} path={[key]} onChange={updateValue} />)}</div> : <div className="profile-editor-error" role="alert">JSON 格式无效，请切换到高级 JSON 修正。</div>}
  </Modal>;
}

function ProfileCardDialog({ characterId, role, avatars, displayName, onClose, onEdit }: { characterId: string; role: Role; avatars: AvatarConfig; displayName: string; onClose: () => void; onEdit: (role: Role) => void }) {
  const [card, setCard] = useState<ProfileCardData | null>(null); const [error, setError] = useState("");
  useEffect(() => { const query = role === "user" || !characterId ? "" : `?character_id=${encodeURIComponent(characterId)}`; request<ProfileCardData>(`/api/v1/profiles/${role}/card${query}`).then(setCard).catch((reason: Error) => setError(reason.message)); }, [characterId, role]);
  const blocks: [string, Record<string, unknown>][] = card ? [["身份信息", card.identity], ["人物性格", card.personality], ["角色演绎", card.roleplay || {}], ["近期关系", card.relationship]] : [];
  return <Modal title={`${displayName} · 人物卡`} kicker="CHARACTER PROFILE" onClose={onClose} compact footer={<button className="primary" onClick={() => onEdit(role)}>编辑这份档案</button>}><div className="profile-card-hero"><PortraitAvatar role={role} avatars={avatars} label={displayName} /><div><h3>{displayName}</h3><p>{role === "assistant" ? "AI 角色设定与当前关系状态" : "用户设定与偏好"}</p></div></div>{error ? <div className="profile-card-empty">{error}</div> : !card ? <div className="profile-card-empty">正在读取人物关键字段…</div> : <div className="profile-card-blocks">{blocks.map(([title, value]) => <section className="profile-card-block" key={title}><h3>{title}</h3>{Object.keys(value).length ? Object.entries(value).map(([key, item]) => <div className="profile-card-row" key={key}><span>{key}</span><strong>{friendlyValue(item)}</strong></div>) : <div className="profile-card-empty">暂无记录</div>}</section>)}<small className="profile-revision">修订 {card.revision} · {formatTime(card.updated_at)}</small></div>}</Modal>;
}

function DiagnosticsDialog({ onClose, notify, onCleared }: { onClose: () => void; notify: (message: string) => void; onCleared: () => void }) {
  const [report, setReport] = useState<DiagnosticReport | null>(null); const [loading, setLoading] = useState(true);
  const load = useCallback(() => { setLoading(true); request<DiagnosticReport>("/api/v1/diagnostics").then(setReport).catch((error: Error) => notify(error.message)).finally(() => setLoading(false)); }, [notify]);
  useEffect(() => { load(); }, [load]);
  const clear = async (scope: "knowledge" | "sessions" | "all") => { const phrase = { knowledge: "CLEAR KNOWLEDGE", sessions: "CLEAR SESSIONS", all: "CLEAR ALL" }[scope]; if (!(await styledConfirm({ title: "危险数据操作", message: phrase, detail: "此操作会清除对应的本地运行数据，无法撤销。", confirmLabel: "确认清除", danger: true }))) return; await request("/api/v1/data/clear", { method: "POST", body: JSON.stringify({ scope, confirmation: phrase }) }); notify("数据清理完成"); onCleared(); load(); };
  return <Modal title="系统诊断与数据管理" kicker="SYSTEM HEALTH" onClose={onClose}>{loading ? <div className="empty-mini">正在检查服务状态…</div> : <><div className="diagnostic-grid"><article><span>主服务</span><strong>{report?.ok ? "正常" : "异常"}</strong><small>{str(report?.app.version)}</small></article><article><span>会话</span><strong>{num(report?.counts.sessions)}</strong><small>SQLite 权威存储 · JSON 投影</small></article><article><span>知识块</span><strong>{num(report?.counts.chunks)}</strong><small>{num(report?.counts.characters)} 字符</small></article><article><span>语音</span><strong>{bool(report?.audio.asr_ready) ? "ASR 就绪" : "ASR 降级"}</strong><small>{str(report?.audio.asr_provider)}</small></article></div><details className="report-json"><summary>查看完整诊断报告</summary><pre>{JSON.stringify(report, null, 2)}</pre></details><section className="danger-zone"><h3>危险数据操作</h3><p>这些操作只影响当前新项目的 runtime，不会修改原 Mindscape 数据。</p><div><button onClick={() => void clear("knowledge")}>清空知识库</button><button onClick={() => void clear("sessions")}>清空会话</button><button className="danger" onClick={() => void clear("all")}>清空全部运行数据</button></div></section></>}</Modal>;
}

function VoiceMode({ state, avatar, characterName, context, companion, onExit, onRetry, onFallback }: { state: VoiceSessionState; avatar: AvatarEntry; characterName: string; context: VoiceInteractionContext; companion: { enabled: boolean; round: number; limit: number }; onExit: () => void; onRetry: () => void; onFallback: () => void }) {
  const intensity = Math.max(0.08, state.level);
  const faceToFace = context.mode === "face_to_face";
  return <section className={`voice-mode phase-${state.phase}`} style={{ "--voice-level": intensity, "--voice-avatar": `url("${avatar.src}")` } as CSSProperties} aria-label="沉浸式实时语音"><div className="voice-background" /><div className="voice-shade" /><button className="voice-exit" onClick={onExit}>退出语音</button><div className="voice-stage"><span className="voice-kicker">{faceToFace ? "FACE TO FACE" : "LIVE CONVERSATION"}</span>{faceToFace && <div className="voice-scene-chip" title={context.scene || "普通面对面场景"}><span>面对面</span><small>{context.scene || "未指定具体场景"}</small></div>}{companion.enabled && <div className={`voice-companion ${companion.round >= companion.limit ? "complete" : ""}`} role="status"><span>连续陪伴</span><strong>{companion.round} / {companion.limit}</strong><small>{companion.round >= companion.limit ? "已到本次上限" : "朗读结束 10 秒后继续 · 可随时插话"}</small></div>}<div className="voice-portrait-shell"><i className="voice-ring ring-one" /><i className="voice-ring ring-two" /><div className="voice-portrait portrait-avatar" style={avatarStyle(avatar)}><img src={avatar.src} alt={`${characterName}头像`} /></div></div><h1>{characterName}</h1><div className="voice-status"><i />{VOICE_LABELS[state.phase]}</div><div className="voice-wave" aria-hidden="true">{Array.from({ length: 18 }, (_, index) => <i key={index} style={{ "--bar": (index % 5) + 1 } as CSSProperties} />)}</div><div className="voice-caption"><small>{state.reply ? `${characterName} 正在回应` : "你刚刚说"}</small><p>{state.reply || state.transcript || ((state.phase === "error" || state.phase === "preparing") ? state.error : "直接开始说话，我会自动识别、发送并回应。")}</p></div>{(state.phase === "error" || state.phase === "preparing") && <div className="voice-error"><span>{state.error}</span><button onClick={onRetry}>重试原生采集</button><button onClick={onFallback}>切换备用采集</button></div>}<span className="voice-tip">连续说话确认后才会打断 · 插话会重定向话题 · Ctrl+Shift+M 切换 · Esc 退出</span></div></section>;
}

export default App;

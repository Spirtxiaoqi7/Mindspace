import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { DrawWorkshop } from "./DestinyCanvas";
import type { CSSProperties, ReactNode } from "react";
import {
  getAudioStatus,
  rawRequest,
  request,
} from "./api";
import {
  clearActiveRun,
  clearTurnRequestSnapshots,
  composerAction,
  hasMissingAttachmentContent,
  hydrateTurnRequestSnapshots,
  mergeAttachmentFiles,
  modelAttemptInspectorEvent,
  modelSummaryInspectorEvent,
  parseModelDiagnostics,
  providerToolCapability,
  readActiveRun,
  requestAttachments,
  shouldShowComposerAction,
} from "./chat-contract";
import {
  estimateDeliveredPrefix,
  hasSpeakableContent,
  SpeechSegmenter,
  stripLeadingTtsFiller,
} from "./speech";
import {
  CharacterLibrary,
  CharacterPicker,
  ModeLobby,
} from "./characters/CharacterExperience";
import type { AppView } from "./characters/CharacterExperience";
import { Composer } from "./chat/Composer";
import { ExecutionInspector } from "./chat/ExecutionInspector";
import { MessageList } from "./chat/MessageList";
import { useConversation } from "./chat/useConversation";
import { Modal } from "./settings/Modal";
import { Field, SettingsWorkspace } from "./settings/SettingsWorkspace";
import { styledConfirm } from "./ui/styledConfirm";
import { avatarStyle, DEFAULT_AVATARS, normalizeAvatarConfig, PortraitAvatar } from "./ui/avatar";
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
  EventMemoryItem,
  EventMemorySnapshot,
  MemoryItem,
  Message,
  ProductSettings,
  InteractionTag,
  ChatAttachment,
  ChatTurnRequest,
  ProviderHttpAttempt,
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

function appViewFromHash(hash = window.location.hash): AppView {
  if (hash.startsWith("#/characters")) return "characters";
  if (hash.startsWith("#/fate")) return "draw";
  if (hash.startsWith("#/modes")) return "modes";
  if (/^#\/chat\/[^/]+\/scenes/.test(hash)) return "scenes";
  return "chat";
}

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
    && voiceOpen;
}

export function shouldAutomaticallyQueueSpeech(voiceOpen: boolean): boolean {
  return voiceOpen;
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

function formatTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? ""
    : new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
}

function friendlyValue(value: unknown): string {
  if (value == null || value === "") return "暂无";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(friendlyValue).join("、") || "暂无";
  return Object.entries(asRecord(value)).map(([key, item]) => `${key}：${friendlyValue(item)}`).join("；") || "暂无";
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
  const [conversationScene, setConversationScene] = useState<ConversationScene | null>(null);
  const [appView, setAppView] = useState<AppView>(() => appViewFromHash());
  const [characterPickerOpen, setCharacterPickerOpen] = useState(false);
  const [characterPickerIntent, setCharacterPickerIntent] = useState<"resume" | "new">("resume");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState(localStorage.getItem("mindspace.session") || uid());
  const [settingsInitialTab, setSettingsInitialTab] = useState("model");
  const [messages, setMessages] = useState<Message[]>([]);
  const [round, setRound] = useState(1);
  const [input, setInput] = useState("");
  const [pendingInteractions, setPendingInteractions] = useState<InteractionTag[]>([]);
  const [pendingAttachments, setPendingAttachments] = useState<ChatAttachment[]>([]);
  const [regenerationDraft, setRegenerationDraft] = useState<{ round: number; request: ChatTurnRequest } | null>(null);
  const [replyTarget, setReplyTarget] = useState<Message | null>(null);
  const [interactionOpen, setInteractionOpen] = useState(false);
  const [interactionBranch, setInteractionBranch] = useState<"root" | "touch" | "kiss">("root");
  const [customInteraction, setCustomInteraction] = useState("");
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [asrReady, setAsrReady] = useState(false);
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
  const sendMessageRef = useRef<((text?: string, mode?: "primary" | "regenerate", targetRound?: number, initiative?: boolean, initiativeTrigger?: InitiativeTrigger, initiativeSequence?: number, initiativeSequenceLimit?: number, replayRequest?: ChatTurnRequest) => Promise<void>) | null>(null);
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
    return result.sessions;
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
        ? "/fate"
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
    void loadConversationScene(id);
    const loadedMessages = hydrateTurnRequestSnapshots(id, value.messages || []);
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
      const requestedView = appViewFromHash();
      const routeSessionId = window.location.hash.match(/^#\/chat\/([^/]+)/)?.[1] || "";
      const rememberedId = localStorage.getItem("mindspace.session");
      const preferred = usableSessions.find((item) => item.session_id === routeSessionId)
        || usableSessions.find((item) => item.session_id === rememberedId)
        || usableSessions[0];
      if (preferred) await openSession(preferred.session_id);
      else {
        setAppView("modes");
        window.history.replaceState(null, "", "#/modes");
      }
      if (requestedView !== "chat") {
        setAppView(requestedView);
        const restoredPath = requestedView === "modes"
          ? "/modes"
          : requestedView === "draw"
            ? "/fate"
            : requestedView === "characters"
              ? "/characters"
              : `/chat/${preferred?.session_id || sessionId}/scenes`;
        window.history.replaceState(null, "", `#${restoredPath}`);
      }
      setInitialDataLoaded(true);
    }).catch((error: Error) => notify(error.message));
  }, [notify]);

  useEffect(() => {
    if (!initialDataLoaded) return;
    let disposed = false;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const status = await getAudioStatus(controller.signal);
        if (!disposed) setAsrReady(Boolean(status.asr_ready));
      } catch {
        if (!disposed) setAsrReady(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [initialDataLoaded, settings?.audio.asr_endpoint, settings?.audio.asr_model, settings?.audio.asr_provider]);

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
        response = await rawRequest("/api/v1/audio/tts/stream", {
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
    // Text chat never starts TTS implicitly. Manual replay remains available
    // through force=true, while live voice owns automatic synthesis.
    if ((!force && !shouldAutomaticallyQueueSpeech(voiceOpenRef.current)) || !text.trim()) return;
    const speech = ttsResponseStartedRef.current ? text.trim() : stripLeadingTtsFiller(text);
    if (!hasSpeakableContent(speech)) return;
    ttsResponseStartedRef.current = true;
    audioQueueRef.current.push({ id: uid(), text: speech, voiceCue });
    // One VoiceIntent has one local synthesis request at a time.  Keeping only
    // text in this queue avoids a hidden stack of HTTP streams behind the
    // GPT-SoVITS lock when a streamed answer arrives quickly.
    if (!audioPlayingRef.current) void playQueue();
  }, [playQueue]);

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
    } else if (event.event === "model.attempt") {
      const rawStatus = str(data.status);
      const status: ProviderHttpAttempt["status"] = ["success", "http_error", "transport_error", "empty", "error"].includes(rawStatus)
        ? rawStatus as ProviderHttpAttempt["status"]
        : "error";
      const attempt: ProviderHttpAttempt = {
        attempt: Math.max(1, num(data.attempt, 1)),
        request_kind: str(data.request_kind),
        status,
        elapsed_ms: Math.max(0, num(data.elapsed_ms)),
        http_status: data.http_status == null ? null : num(data.http_status),
        compatibility_variant: str(data.compatibility_variant),
        retry_reason: str(data.retry_reason),
        error: str(data.error),
      };
      addEvent(modelAttemptInspectorEvent(attempt, event.timestamp, event.seq));
    } else if (event.event.startsWith("tool.")) {
      if (event.event === "tool.hinted") return;
      const tool = str(data.tool || "tool");
      const state = event.event === "tool.failed" ? "error" : ["tool.requested", "tool.started"].includes(event.event) ? "active" : "done";
      const toolNames: Record<string, string> = { web: "联网", memory: "记忆", task: "任务" };
      const summary = str(data.parameter_summary);
      const labels: Record<string, string> = {
        "tool.requested": `请求${toolNames[tool] || tool}${summary ? ` · ${summary}` : ""}`,
        "tool.reviewed": `任务审查${bool(data.allowed) ? "通过" : "拒绝"}`,
        "tool.started": `正在执行${toolNames[tool] || tool}${summary ? ` · ${summary}` : ""}`,
        "tool.completed": `${toolNames[tool] || tool}执行完成`,
        "tool.failed": `${toolNames[tool] || tool}执行失败`,
      };
      const callId = str(data.call_id);
      const eventId = callId ? `tool:${callId}` : `${event.event}:${event.seq}`;
      if (callId) {
        setMessages((items) => items.map((message) => message.role === "assistant" && message.status === "streaming" ? {
          ...message,
          tool_execution: {
            call_id: callId,
            tool: (tool === "memory" || tool === "task" ? tool : "web"),
            level: num(data.level, 3) === 2 ? 2 : 3,
            status: event.event === "tool.failed" ? (str(data.status) === "denied" ? "denied" : "failed") : event.event === "tool.completed" ? "success" : event.event === "tool.reviewed" ? "reviewing" : event.event === "tool.started" ? "running" : "requested",
            parameter_summary: summary || message.tool_execution?.parameter_summary || "",
            started_at: str(data.started_at || message.tool_execution?.started_at || (event.event === "tool.started" ? event.timestamp : "")),
            completed_at: str(data.completed_at || message.tool_execution?.completed_at || (["tool.completed", "tool.failed"].includes(event.event) ? event.timestamp : "")),
            elapsed_ms: num(data.elapsed_ms, message.tool_execution?.elapsed_ms || 0),
            source_count: num(data.source_count, message.tool_execution?.source_count || 0),
            data: asRecord(data.data || message.tool_execution?.data),
            error: str(data.error || message.tool_execution?.error),
            receipt: asRecord(data.receipt || message.tool_execution?.receipt),
          },
        } : message));
      }
      if (callId && event.event !== "tool.requested") {
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
      const completedPresentation = str(response.presentation_mode);
      const modelDiagnostics = parseModelDiagnostics(response.model, num(response.llm_call_count));
      const modelSummary = modelSummaryInspectorEvent(modelDiagnostics, event.timestamp);
      setEvents((items) => [...items.filter((item) => item.event !== "model.summary"), modelSummary]);
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
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, message_id: str(response.assistant_message_id) || item.message_id, content: str(response.reply || item.content), presentation_mode: completedPresentation === "scene" ? "scene" : "dialogue", status: "complete" as const } : item));
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
      const modelDiagnostics = parseModelDiagnostics(response.model || data.model, num(response.llm_call_count));
      const modelSummary = modelSummaryInspectorEvent(modelDiagnostics, event.timestamp, true);
      setEvents((items) => [...items.filter((item) => item.event !== "model.summary"), modelSummary]);
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

  const { executeTurn, cancelRun, stageRegeneration, completeRegeneration } = useConversation({
    sessionId,
    initialDataLoaded,
    generatingRef,
    runIdRef,
    abortRef,
    setGenerating,
    setRunId,
    setMessages,
    handleStreamEvent,
    notify,
    onBeforeRecovery: stopAudio,
    onConversationJump: () => {
      followConversationRef.current = true;
      pendingConversationJumpRef.current = true;
    },
    onCancelEffects: () => {
      flushResponseDelta();
      stopAudio();
      setVoiceInputLocked(false, "run_cancelled");
      if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "listening", reply: "", level: 0, error: "" }));
    },
    onRequestFailure: (error) => {
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      setVoiceInputLocked(false, "request_failed");
      if (voiceOpenRef.current) {
        setVoice((current) => ({ ...current, phase: audioPlayingRef.current ? "assistant-speaking" : "listening", error: error.message, level: 0 }));
      }
    },
  });

  const sendMessage = useCallback(async (
    text = input,
    mode: "primary" | "regenerate" = "primary",
    targetRound = round,
    initiative = false,
    initiativeTrigger: InitiativeTrigger = initiative ? "manual" : "none",
    initiativeSequence = 0,
    initiativeSequenceLimit = 0,
    replayRequest?: ChatTurnRequest,
  ) => {
    const replay = mode === "regenerate" ? replayRequest : undefined;
    const content = replay?.message ?? (initiative ? "请求 AI 主动回复" : text.trim());
    const turnInteractions = replay?.interactions ?? (initiative ? [] : pendingInteractions);
    const turnAttachments = replay?.attachments ?? (initiative ? [] : pendingAttachments);
    const turnReplyTargetId = replay?.reply_to_message_id ?? (initiative ? "" : replyTarget?.message_id || "");
    const asrEvidence = replay?.input_evidence?.asr ?? (!initiative && voiceOpenRef.current
      ? pendingASREvidenceRef.current
      : null);
    if (!replay) pendingASREvidenceRef.current = null;
    if (!content && !turnInteractions.length && !turnAttachments.length) { notify("请输入消息、选择互动或添加附件"); return; }
    if (hasMissingAttachmentContent(turnAttachments)) { notify("原回合附件正文未保存在本地，请重新附加全部标记文件后再生成"); return; }
    if (!activeCharacterId) {
      notify("请先为当前会话选择角色");
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
    if (!initiative) {
      setPendingInteractions([]);
      setPendingAttachments([]);
      setReplyTarget(null);
      setRegenerationDraft(null);
      setInteractionOpen(false);
      setInteractionBranch("root");
    }
    setEvents([]);
    setRetrieval([]);
    if (voiceOpenRef.current) setVoice((current) => ({ ...current, transcript: initiative ? current.transcript : content, reply: "", phase: "thinking", error: "" }));
    if (voiceOpenRef.current && !initiative) {
      activeVoiceTurnTextRef.current = content;
      activeVoiceTurnRoundRef.current = targetRound;
    }
    const requestId = uid();
    activeInitiativeRef.current = { trigger: initiativeTrigger, sequence: initiativeSequence };
    const clientSentAt = replay?.client_sent_at || new Date().toISOString();
    const persona = settings?.persona;
    const retrievalSettings = settings?.retrieval;
    const llm = settings?.llm;
    const payload: ChatTurnRequest = replay ? {
      ...structuredClone(replay),
      mode: "regenerate",
      round: targetRound,
      attachments: requestAttachments(turnAttachments),
    } : {
      message: content,
      session_id: sessionId,
      character_id: activeCharacterId,
      reply_to_message_id: turnReplyTargetId,
      interactions: turnInteractions.map((item) => ({ ...item })),
      attachments: requestAttachments(turnAttachments),
      activity_session_id: "",
      session_mode: activeCharacter?.source === "draw" ? "draw" : "custom",
      round: targetRound,
      mode,
      interaction_mode: voiceOpenRef.current ? "voice" : "text",
      presentation_mode: "auto",
      adult_mode: adultMode,
      r18_style_id: r18StyleId,
      initiative,
      initiative_trigger: initiativeTrigger,
      initiative_sequence: initiativeSequence,
      initiative_sequence_limit: initiativeSequenceLimit,
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
      user_name: str(persona?.user_name || "用户"),
      user_persona: str(persona?.user_persona),
      reply_length_preference: str(persona?.reply_length_preference),
      character_name: str(persona?.character_name || "Mindspace"),
      system_prompt: str(persona?.system_prompt),
      api: { temperature: num(llm?.temperature, 0.7), max_tokens: num(llm?.max_tokens, 2000) },
      retrieval: {
        rag_enabled: bool(retrievalSettings?.rag_enabled ?? true),
        knowledge_enabled: bool(retrievalSettings?.knowledge_enabled ?? true),
        chat_enabled: bool(retrievalSettings?.chat_enabled ?? true),
        structured_memory_enabled: bool(retrievalSettings?.structured_memory_enabled ?? true),
        temporal_enabled: bool(retrievalSettings?.temporal_enabled ?? true),
        knowledge_k: num(retrievalSettings?.knowledge_k, 2),
        chat_k: num(retrievalSettings?.chat_k, 3),
        history_k: num(retrievalSettings?.history_k, 3),
        similarity_threshold: num(retrievalSettings?.similarity_threshold, 0.5),
        decay_rounds: num(retrievalSettings?.decay_rounds, 20),
        decay_hours: num(retrievalSettings?.decay_hours, 168),
        fairness_enabled: bool(retrievalSettings?.fairness_enabled ?? true),
        low_exposure_ratio: num(retrievalSettings?.low_exposure_ratio, 0.2),
        memory_family_limit: num(retrievalSettings?.memory_family_limit, 2),
        starvation_rounds: num(retrievalSettings?.starvation_rounds, 6),
        starvation_boost: num(retrievalSettings?.starvation_boost, 0.12),
        bm25_enabled: bool(retrievalSettings?.bm25_enabled ?? true),
        vector_enabled: bool(retrievalSettings?.vector_enabled ?? true),
        rrf_k: num(retrievalSettings?.rrf_k, 60),
        candidate_multiplier: num(retrievalSettings?.candidate_multiplier, 4),
        max_total_boost: num(retrievalSettings?.max_total_boost, 0.25),
        reranker_enabled: bool(retrievalSettings?.reranker_enabled ?? false),
        reranker_top_n: num(retrievalSettings?.reranker_top_n, 12),
        boosts: retrievalSettings?.boosts || {},
      },
    };
    const user: Message = { role: "user", content, round: targetRound, status: "complete", timestamp: clientSentAt, reply_to_message_id: turnReplyTargetId || undefined, interactions: turnInteractions, attachments: turnAttachments.map((item) => ({ ...item })), request_snapshot: payload };
    const assistant: Message = { role: "assistant", content: "", round: targetRound, status: "streaming", kind: initiative ? "initiative_response" : "message", initiative_trigger: initiativeTrigger };
    const outgoing = initiative ? [assistant] : [user, assistant];
    if (voiceOpenRef.current) voiceDeliveryRef.current = null;
    await executeTurn({ requestId, payload, outgoing, mode, targetRound });
  }, [activeCharacter, activeCharacterId, adultMode, cancelIdleContinuation, cancelRun, captureVoiceInterruption, clearPendingResponseDelta, executeTurn, generating, input, llmReady, notify, pendingAttachments, pendingInteractions, replyTarget, r18StyleId, round, sessionId, settings, stopAudio]);

  useEffect(() => { sendMessageRef.current = sendMessage; }, [sendMessage]);

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

  const startNewSessionForCharacter = useCallback(async (character: CharacterSummary | CharacterRecord) => {
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
    await loadConversationScene(id);
    setMessages([]); setRound(1); setEvents([]); setRetrieval([]); setSidebarOpen(false);
    setCharacterPickerOpen(false);
    setAppView("chat");
    window.history.replaceState(null, "", `#/chat/${id}`);
    await Promise.all([loadSessions(), loadCharacters()]);
    notify("已创建新对话");
  }, [cancelIdleContinuation, cancelRun, generating, loadCharacters, loadConversationScene, loadSessions, notify]);

  const resumeCharacterSession = useCallback(async (character: CharacterSummary | CharacterRecord) => {
    const matchingSessions = sessions
      .filter((item) => item.character_id === character.character_id
        || (!item.character_id && str(item.character_name).trim() === character.display_name.trim()))
      .sort((left, right) => Date.parse(right.updated_at || "0") - Date.parse(left.updated_at || "0"));
    const existing = matchingSessions[0]?.session_id;
    if (existing) {
      setCharacterPickerOpen(false);
      await openSession(existing);
      return;
    }
    await startNewSessionForCharacter(character);
  }, [openSession, sessions, startNewSessionForCharacter]);

  const newSession = useCallback(() => {
    setCharacterPickerIntent("new");
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
    if (localStorage.getItem("mindspace.session") === id) {
      localStorage.removeItem("mindspace.session");
    }
    const [remaining, refreshedCharacters] = await Promise.all([loadSessions(), loadCharacters()]);
    if (id === sessionId) {
      const deletedCharacterId = str(target.character_id);
      const next = remaining.find((item) => item.character_id === deletedCharacterId);
      if (next) await openSession(next.session_id);
      else {
        setMessages([]);
        setRound(1);
        setEvents([]);
        setRetrieval([]);
        setConversationScene(null);
        const characterStillExists = refreshedCharacters.some(
          (item) => item.character_id === deletedCharacterId && item.status === "active",
        );
        if (characterStillExists) {
          setActiveCharacterId(deletedCharacterId);
          navigate("characters");
        } else {
          setActiveCharacterId("");
          navigate("modes");
        }
      }
    }
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
    clearTurnRequestSnapshots(sessionId);
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
  const interactionTargets = useMemo(() => {
    const normal = ["头发", "额头", "脸颊", "肩膀", "手", "后背"];
    if (!adultMode) return { normal, intimate: [] as string[] };
    const gender = str(activeCharacter?.gender || "不指定");
    if (gender === "女") return { normal, intimate: ["胸部", "乳头", "阴蒂", "阴部"] };
    if (gender === "男") return { normal, intimate: ["胸膛", "阴茎", "龟头"] };
    return { normal, intimate: [] as string[] };
  }, [activeCharacter?.gender, adultMode]);
  const addInteraction = useCallback((category: InteractionTag["category"], action: string, target = "", sensitivity: InteractionTag["sensitivity"] = "normal") => {
    const id = `${category}:${action}:${target}`;
    setPendingInteractions((items) => items.some((item) => item.id === id) ? items : [...items, { id, category, level: category === "daily" ? 0 : category === "touch" ? 1 : category === "kiss" ? 2 : 9, action, target, sensitivity }]);
  }, []);
  const handleAttachmentFiles = useCallback(async (files: FileList | null) => {
    if (!files?.length) return;
    const result = await mergeAttachmentFiles(pendingAttachments, Array.from(files), Boolean(regenerationDraft));
    setPendingAttachments(result.attachments);
    if (result.feedback.length) notify(result.feedback.join("；"));
  }, [notify, pendingAttachments, regenerationDraft]);
  const regenerateMessage = useCallback((message: Message, targetRound: number) => {
    const staged = stageRegeneration(message, targetRound);
    if (!staged) {
      notify("这个旧回合没有完整请求快照，无法保证一致重放；请作为新消息发送");
      return;
    }
    const { snapshot, preparation } = staged;
    if (preparation.request) {
      void sendMessage(snapshot.message, "regenerate", targetRound, false, snapshot.initiative_trigger, snapshot.initiative_sequence, snapshot.initiative_sequence_limit, preparation.request);
      return;
    }
    setRegenerationDraft({ round: targetRound, request: snapshot });
    setInput(snapshot.message);
    setPendingInteractions(snapshot.interactions.map((item) => ({ ...item })));
    setPendingAttachments(preparation.stagedAttachments);
    setReplyTarget(snapshot.reply_to_message_id
      ? messages.find((item) => item.message_id === snapshot.reply_to_message_id) || null
      : null);
    notify(`请重新附加 ${preparation.missingAttachments.length} 个原附件；补齐前不会发送`);
  }, [messages, notify, sendMessage, stageRegeneration]);
  const cancelRegenerationDraft = useCallback(() => {
    setRegenerationDraft(null);
    setInput("");
    setPendingInteractions([]);
    setPendingAttachments([]);
    setReplyTarget(null);
    notify("已取消附件重附和重新生成");
  }, [notify]);
  const sendRegenerationDraft = useCallback(() => {
    if (!regenerationDraft) return;
    const preparation = completeRegeneration(regenerationDraft.request, pendingAttachments);
    if (!preparation.request) {
      notify(`仍有 ${preparation.missingAttachments.length} 个附件需要重新附加`);
      return;
    }
    void sendMessage(
      preparation.request.message,
      "regenerate",
      regenerationDraft.round,
      false,
      preparation.request.initiative_trigger,
      preparation.request.initiative_sequence,
      preparation.request.initiative_sequence_limit,
      preparation.request,
    );
  }, [completeRegeneration, notify, pendingAttachments, regenerationDraft, sendMessage]);
  const loadAvailableModels = useCallback(async () => {
    if (modelsLoading) return;
    setModelsLoading(true);
    try { const result = await request<{ models: string[] }>("/api/v1/models/available"); setAvailableModels(result.models || []); }
    catch (error) { notify((error as Error).message); }
    finally { setModelsLoading(false); }
  }, [modelsLoading, notify]);
  const chooseModel = useCallback(async (model: string) => {
    try {
      const result = await request<{ settings: ProductSettings }>("/api/v1/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ llm: { model } }) });
      setSettings(result.settings); notify(`已切换到 ${model}`);
    } catch (error) { notify((error as Error).message); }
  }, [notify]);
  const composerHasPayload = Boolean(input.trim() || pendingInteractions.length || pendingAttachments.length);
  const composerActionKind = composerAction(generating, composerHasPayload, asrReady);
  const toolCapability = providerToolCapability(llmBaseUrl);
  const pickerCharacters = characters;
  const interruptedSession = sessions.find((item) => Boolean((item as SessionSummary & { interrupted?: boolean }).interrupted));

  const enterDrawMode = () => navigate("draw");

  const enterCustomMode = () => {
    setCharacterPickerIntent("resume");
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
        title="选择本次对话的角色"
        onClose={() => setCharacterPickerOpen(false)}
        onChoose={(character) => void (characterPickerIntent === "new"
          ? startNewSessionForCharacter(character)
          : resumeCharacterSession(character))}
        onDraw={() => { setCharacterPickerOpen(false); navigate("draw"); }}
      />
      {toast && <div className="toast" role="status">{toast}</div>}
    </>;
  }

  if (appView === "draw") {
    return <DrawWorkshop
      defaultUserName={userName}
      onBack={() => navigate("modes")}
      onCommitted={async (character) => {
        await loadCharacters();
        await startNewSessionForCharacter(character);
      }}
    />;
  }

  if (appView === "characters") {
    return <CharacterLibrary
      characters={characters}
      initialCharacterId={activeCharacterId}
      onBack={() => navigate("modes")}
      onRefresh={async () => { await loadCharacters(); }}
      onChat={(character) => void resumeCharacterSession(character)}
      onNewChat={(character) => void startNewSessionForCharacter(character)}
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
      <div className="brand-row"><button className="brand-mark" onClick={() => navigate("modes")} title="返回主页" aria-label="Mindspace 主页"><img src={`${import.meta.env.BASE_URL}assets/mindspace-brand-icon.png?v=0.8.1`} alt="" /></button><div><strong>Mindspace</strong><small>PRIVATE COMPANION</small></div><button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} aria-label="关闭会话栏">×</button></div>
      <button className="new-chat home-entry" onClick={() => navigate("modes")}><span>⌂</span> 主页</button>
      <label className="search-box"><span>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索会话" aria-label="搜索会话" /></label>
      <div className="session-heading"><span>最近会话</span><small>{filteredSessions.length}</small></div>
      <nav className="session-list">
        {filteredSessions.length ? filteredSessions.map((item) => <div className={`session-item ${item.session_id === sessionId ? "active" : ""}`} key={item.session_id}><button className="session-open" onClick={() => void openSession(item.session_id)}>{item.character_avatar?.src ? <img className="session-avatar" src={item.character_avatar.src} alt="" onError={(event) => { event.currentTarget.src = DEFAULT_AVATARS.assistant.src; }} /> : <span className="session-glyph">◌</span>}<span><strong>{item.character_name || item.title}</strong><small>{item.message_count} 条 · {formatTime(item.updated_at)}</small></span></button><button className="session-delete" aria-label={`删除会话：${item.character_name || item.title}`} title="删除会话" onClick={() => void deleteSession(item.session_id)}>×</button></div>) : <div className="empty-mini">没有匹配的会话</div>}
      </nav>
      <div className="sidebar-tools hub-navigation">
        <button className="sidebar-memory-entry" onClick={() => openModal("memory")}><span>◇</span><b>记忆</b><i>事件与长期记忆</i></button>
      </div>
      <div className="account-card"><PortraitAvatar role="assistant" avatars={effectiveAvatars} label={characterName} className="small" onClick={() => setProfileCardRole("assistant")} /><button className="account-settings persona-entry" aria-label="打开人设工作区" onClick={() => { setProfileEditorRole("assistant"); setModal("profile"); }}><span><strong>{characterName}</strong><small><i /> 人物、状态与关系</small></span><b>人设</b></button></div>
    </aside>

    <main className={`workspace${conversationScene?.scene ? " scene-active" : ""}`}>
      {conversationScene?.scene && <div
        key={conversationScene.scene.scene_id}
        className="chat-scene-background"
        style={{ backgroundImage: `url("${sceneAssetPath(conversationScene.scene)}")` }}
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
        <MessageList messages={messages} avatars={effectiveAvatars} userName={userName} characterName={characterName} onProfile={setProfileCardRole} onCopy={(text) => { void navigator.clipboard.writeText(text); notify("已复制回复"); }} onSpeak={speakMessage} onRegenerate={regenerateMessage} onInitiative={(targetRound) => void sendMessage("", "regenerate", targetRound, true)} onDelete={(messageId) => void deleteReply(messageId)} onConfigure={() => openSettings("model")} onReply={(message) => { if (!message.message_id) { notify("消息正在保存，请稍后再引用"); return; } setReplyTarget(message); }} onInteract={(message) => { if (message.message_id) setReplyTarget(message); setInteractionOpen(true); setInteractionBranch("root"); }} />
        <div className="conversation-tail" ref={conversationTailRef} aria-hidden="true" />
      </section>
      <Composer
        generating={generating}
        characterName={characterName}
        input={input}
        onInput={setInput}
        onSend={() => { void sendMessage(); }}
        onCancel={() => { void cancelRun(); }}
        onOpenVoice={openVoiceEntry}
        asrReady={asrReady}
        hasPayload={composerHasPayload}
        replyTarget={replyTarget}
        onClearReply={() => setReplyTarget(null)}
        regenerationDraft={Boolean(regenerationDraft)}
        onCancelRegeneration={cancelRegenerationDraft}
        onSendRegeneration={sendRegenerationDraft}
        pendingInteractions={pendingInteractions}
        onRemoveInteraction={(id) => setPendingInteractions((items) => items.filter((candidate) => candidate.id !== id))}
        pendingAttachments={pendingAttachments}
        onRemoveAttachment={(attachment) => { setPendingAttachments((items) => items.filter((candidate) => candidate.attachment_id !== attachment.attachment_id)); notify(`已移除附件 ${attachment.name}`); }}
        onAttachmentFiles={handleAttachmentFiles}
        interactionOpen={interactionOpen}
        interactionBranch={interactionBranch}
        onInteractionOpen={setInteractionOpen}
        onInteractionBranch={setInteractionBranch}
        interactionTargets={interactionTargets}
        onAddInteraction={addInteraction}
        customInteraction={customInteraction}
        onCustomInteraction={setCustomInteraction}
        round={round}
        onInitiative={() => { void sendMessage("", "primary", round, true); }}
        sceneTitle={conversationScene?.scene?.title || ""}
        onOpenScenes={() => navigate("scenes")}
        onShowFlow={showFlow}
        onShowContext={showContext}
        retrievalCount={retrieval.length}
        onExportSession={exportSession}
        adultMode={adultMode}
        onToggleAdultMode={toggleAdultMode}
        r18StyleId={r18StyleId}
        onR18StyleId={(next) => { setR18StyleId(next); localStorage.setItem(R18_STYLE_STORAGE_KEY, next); }}
        model={str(settings?.llm.model)}
        modelBaseUrl={llmBaseUrl}
        modelToolLabel={toolCapability.label}
        modelsLoading={modelsLoading}
        availableModels={availableModels}
        onLoadModels={() => { void loadAvailableModels(); }}
        onChooseModel={(model) => { void chooseModel(model); }}
        onClearCurrent={() => { void clearCurrent(); }}
      />
    </main>

    <ExecutionInspector open={inspectorOpen} tab={inspectorTab} onTab={setInspectorTab} onClose={() => setInspectorOpen(false)} events={events} retrieval={retrieval} runId={inspectionRunId} />
    {modal === "settings" && settings && <SettingsWorkspace value={settings} avatars={avatars} initialTab={settingsInitialTab} onClose={closeModal} onDirty={setModalDirty} onOpenProfile={(role) => { setProfileEditorRole(role); setModalDirty(false); setModal("profile"); }} onOpenMemory={() => { setModalDirty(false); setModal("memory"); }} onOpenKnowledge={() => { setModalDirty(false); setModal("knowledge"); }} onOpenDiagnostics={() => { setModalDirty(false); setModal("diagnostics"); }} onSaved={(next, nextAvatars) => { setSettings(next); setAvatars(nextAvatars); setModalDirty(false); setModal(null); }} onSettingsChange={setSettings} onAvatarsChange={setAvatars} notify={notify} />}
    {modal === "knowledge" && <KnowledgeDialog onClose={closeModal} onDirty={setModalDirty} notify={notify} />}
    {modal === "memory" && <MemoryDialog characterId={activeCharacterId} onClose={closeModal} onDirty={setModalDirty} notify={notify} />}
    {modal === "profile" && <ProfileDialog characterId={activeCharacterId} initialName={profileEditorRole} onClose={closeModal} onDirty={setModalDirty} onOpenConnection={() => openSettings("model")} onOpenMemory={() => { setModalDirty(false); setModal("memory"); }} onSaved={() => void loadCharacters()} notify={notify} />}
    {modal === "diagnostics" && <DiagnosticsDialog onClose={closeModal} notify={notify} onCleared={() => { newSession(); void loadSessions(); }} />}
    {modal === "voice-entry" && <VoiceEntryDialog mode={voiceEntryMode} scene={voiceEntryScene} busy={voiceEntryBusy} error={voiceEntryError} onModeChange={(next) => { setVoiceEntryMode(next); setModalDirty(true); }} onSceneChange={(next) => { setVoiceEntryScene(next); setModalDirty(true); }} onClose={closeVoiceEntry} onStart={() => void startVoiceFromEntry()} />}
    {profileCardRole && <ProfileCardDialog characterId={activeCharacterId} role={profileCardRole} avatars={effectiveAvatars} displayName={profileCardRole === "user" ? userName : characterName} onClose={() => setProfileCardRole(null)} onEdit={(role) => { setProfileCardRole(null); setProfileEditorRole(role); setModal("profile"); }} />}
    {voice.open && <VoiceMode state={voice} avatar={effectiveAvatars.assistant} characterName={characterName} context={voiceInteractionRef.current} companion={{ enabled: bool(settings?.interaction?.unlimited_reply_enabled), round: companionRound, limit: Math.max(1, Math.min(50, num(settings?.interaction?.unlimited_reply_max_rounds, 10))) }} onExit={exitVoice} onRetry={retryVoice} onFallback={useBrowserVoiceFallback} />}
    <CharacterPicker open={characterPickerOpen} characters={pickerCharacters} onClose={() => setCharacterPickerOpen(false)} onChoose={(character) => void (characterPickerIntent === "new" ? startNewSessionForCharacter(character) : resumeCharacterSession(character))} onDraw={() => { setCharacterPickerOpen(false); navigate("draw"); }} />
    {nsfwConfirmationOpen && <NsfwAdultConfirmation seconds={nsfwConfirmationSeconds} onCancel={() => setNsfwConfirmationOpen(false)} onConfirm={confirmAdultMode} />}
    {toast && <div className="toast" role="status">{toast}</div>}
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
  const [events, setEvents] = useState<EventMemorySnapshot>({
    schema_version: "1.0.0", character_id: characterId, revision: 0, pending: [],
    subjects: { user_related: null, ai_related: null, relationship_related: null },
    history: [], updated_at: "",
  });
  const [includeHistory, setIncludeHistory] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [editingKey, setEditingKey] = useState("");
  const [draft, setDraft] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [memoryResult, eventResult] = await Promise.all([
        request<{ items: MemoryItem[] }>(`/api/v1/memory/items?include_history=${includeHistory ? "true" : "false"}&character_id=${encodeURIComponent(characterId)}`),
        request<EventMemorySnapshot>(`/api/v1/memory/events?character_id=${encodeURIComponent(characterId)}`),
      ]);
      setItems(memoryResult.items);
      setEvents(eventResult);
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
  const completeEvent = async (item: EventMemoryItem) => {
    try { await request(`/api/v1/memory/events/${encodeURIComponent(item.id)}/complete?character_id=${encodeURIComponent(characterId)}`, { method: "POST" }); notify(`已完成：${item.title}`); await load(); }
    catch (error) { notify((error as Error).message); }
  };
  const removeEvent = async (item: EventMemoryItem) => {
    if (!(await styledConfirm({ title: `删除事件“${item.title}”？`, message: item.summary, detail: "删除后不再参与中期上下文，但不会影响长期 RAG。", confirmLabel: "删除事件", danger: true }))) return;
    try { await request(`/api/v1/memory/events/${encodeURIComponent(item.id)}?character_id=${encodeURIComponent(characterId)}`, { method: "DELETE" }); notify("事件记忆已删除"); await load(); }
    catch (error) { notify((error as Error).message); }
  };
  const categoryLabels = { user_related: "用户相关", ai_related: "AI 相关", relationship_related: "关系相关" } as const;
  const renderEvent = (item: EventMemoryItem | null, label: string, index: number, completable = false) => <article className={`event-memory-card${item ? " filled" : " empty"}`} key={`${label}-${item?.id || index}`}><header><span>{label}</span>{item?.due_at && <time>{new Date(item.due_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>}</header>{item ? <><strong>{item.title}</strong><p>{item.summary}</p><footer>{completable && <button onClick={() => void completeEvent(item)}>完成</button>}<button className="danger-text" onClick={() => void removeEvent(item)}>删除</button></footer></> : <p>空槽位</p>}</article>;
  const pendingSlots = Array.from({ length: 3 }, (_, index) => events.pending[index] || null);
  return <Modal title="记忆中心" kicker="MEMORY CENTER" onClose={onClose}><section className="event-memory-panel"><header className="event-memory-heading"><div><span>EVENT LEDGER</span><h3>事件记忆</h3><p>承接近期事项与重要变化，最多六条；不进入长期 RAG。</p></div><button onClick={() => void load()}>刷新</button></header>{loading ? <div className="empty-mini">正在读取事件记忆…</div> : <div className="event-memory-lanes"><section><header><strong>近期 / 待办</strong><small>{events.pending.length} / 3</small></header><div>{pendingSlots.map((item, index) => renderEvent(item, `待办 ${index + 1}`, index, true))}</div></section><section><header><strong>主体事件</strong><small>{Object.values(events.subjects).filter(Boolean).length} / 3</small></header><div>{(["user_related", "ai_related", "relationship_related"] as const).map((category, index) => renderEvent(events.subjects[category], categoryLabels[category], index))}</div></section></div>}</section><div className="memory-toolbar"><label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索长期记忆" /></label><label className="memory-history-toggle"><input type="checkbox" checked={includeHistory} onChange={(event) => setIncludeHistory(event.target.checked)} />显示已失效记忆</label></div><p className="advanced-note">下方是长期结构化记忆。修改、删除和恢复会同步权威档案；事件区与长期 RAG 相互独立。</p>{loading ? <div className="empty-mini">正在读取长期记忆…</div> : <div className="memory-list">{filtered.length ? filtered.map((item) => <article className={item.status === "invalidated" ? "invalidated" : ""} key={`${item.status}-${item.memory_key}-${item.invalidated_at || ""}`}><header><div><span>{item.category}</span><strong>{item.display_name}</strong></div><small>{item.status === "active" ? "当前有效" : "已失效"} · {formatTime(item.updated_at || item.invalidated_at)}</small></header>{editingKey === item.memory_key && item.status === "active" ? <div className="memory-edit"><input autoFocus value={draft} onChange={(event) => setDraft(event.target.value)} /><button className="secondary" onClick={() => { setEditingKey(""); setDraft(""); }}>取消</button><button className="primary" disabled={!draft.trim()} onClick={() => void save(item)}>保存</button></div> : <p className="memory-value">{friendlyValue(item.value)}</p>}<details><summary>为什么记住</summary><p>{item.source_text || "来自用户在记忆中心的明确操作"}</p>{item.session_id && <small>来源会话：{item.session_id}</small>}</details><footer>{item.status === "active" ? <><button onClick={() => { setEditingKey(item.memory_key); setDraft(String(item.value)); }}>修改</button><button className="danger-text" onClick={() => void remove(item)}>删除</button></> : <button onClick={() => void restore(item)}>恢复这条记忆</button>}</footer></article>) : <div className="empty-mini">暂无匹配的长期结构化记忆。</div>}</div>}</Modal>;
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
  description: "角色基础信息", scenario: "关系与日常情境", first_mes: "首次开场",
  alternate_greetings: "备用开场", mes_example: "对话示例", memory: "长期记忆",
  appearance: "外表设定", height_cm: "身高（cm）", body_shape: "体型", body_features: "身体特征",
  face: "面部特征", hair: "发型发色", eyes: "眼睛", skin: "肤色与质感",
  distinguishing_features: "辨识特征", signature_outfit: "标志穿着", intimate_features: "亲密身体特征",
  preferences: "偏好记忆", tasks: "任务记忆", relationship: "关系类型",
  relationship_context: "关系补充", user_alias: "AI 对你的称呼",
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
  if (fieldKey === "gender") {
    return <label className="profile-form-field"><span>{label}</span><select aria-label={label} value={String(value)} onChange={(event) => onChange(path, event.target.value)}><option value="男">男</option><option value="女">女</option><option value="不指定">不指定</option></select><small>用户手动保存后作为模型最高优先级身份；AI 不能自行改写。通用代词始终使用TA。</small></label>;
  }
  if (["description", "personality", "scenario", "first_mes", "mes_example", "relationship_context"].includes(fieldKey)) {
    return <label className="profile-form-field profile-form-list"><span>{label}</span><textarea aria-label={label} value={value == null ? "" : String(value)} onChange={(event) => onChange(path, event.target.value)} /></label>;
  }
  return <label className="profile-form-field"><span>{label}</span><input aria-label={label} type={typeof value === "number" ? "number" : "text"} value={value == null ? "" : String(value)} onChange={(event) => onChange(path, typeof value === "number" ? Number(event.target.value) : event.target.value)} /></label>;
}

function profileObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function profileStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item || "").trim()).filter(Boolean) : [];
}

function v2ProfileEditorDocument(record: Record<string, unknown>): Record<string, unknown> {
  const card = profileObject(record.card);
  const data = profileObject(card.data);
  const extensions = profileObject(data.extensions);
  const mindspace = profileObject(extensions.mindspace);
  const memory = profileObject(record.memory);
  const cardMemory = profileObject(data.memory);
  return {
    revision: Number(record.revision || 0),
    name: str(data.name),
    gender: str(mindspace.gender || record.gender || "不指定"),
    user_alias: str(record.user_alias || mindspace.user_alias),
    relationship: str(mindspace.relationship || record.relationship_label),
    relationship_context: str(mindspace.relationship_context),
    appearance: profileObject(mindspace.appearance),
    description: str(data.description),
    personality: str(data.personality),
    scenario: str(data.scenario),
    first_mes: str(data.first_mes),
    alternate_greetings: profileStringList(data.alternate_greetings),
    mes_example: str(data.mes_example),
    memory: {
      preferences: profileStringList(memory.preferences || cardMemory.preferences),
      tasks: profileStringList(memory.tasks || cardMemory.tasks),
    },
  };
}

function ProfileDialog({ characterId, initialName, onClose, onDirty, onOpenConnection, onOpenMemory, onSaved, notify }: { characterId: string; initialName: Role | "state"; onClose: () => void; onDirty: (dirty: boolean) => void; onOpenConnection: () => void; onOpenMemory: () => void; onSaved: () => void; notify: (message: string) => void }) {
  const [name, setName] = useState(initialName); const [document, setDocument] = useState(""); const [savedDocument, setSavedDocument] = useState(""); const [history, setHistory] = useState<ProfileHistoryItem[]>([]); const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [mode, setMode] = useState<"form" | "json">("form"); const [error, setError] = useState("");
  const [v2Card, setV2Card] = useState<Record<string, unknown> | null>(null);
  const [characterUsesV2, setCharacterUsesV2] = useState(false);
  const parsed = useMemo(() => { try { const value = JSON.parse(document); return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null; } catch { return null; } }, [document]);
  const characterQuery = name === "user" || !characterId ? "" : `?character_id=${encodeURIComponent(characterId)}`;
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      if (name === "assistant" && characterId) {
        const [record, versions] = await Promise.all([
          request<Record<string, unknown>>(`/api/v1/characters/${encodeURIComponent(characterId)}`),
          request<{ items: ProfileHistoryItem[] }>(`/api/v1/characters/${encodeURIComponent(characterId)}/history`).catch(() => ({ items: [] })),
        ]);
        const card = profileObject(record.card);
        if (Object.keys(card).length) {
          const serialized = JSON.stringify(v2ProfileEditorDocument(record), null, 2);
          setV2Card(card); setCharacterUsesV2(true); setDocument(serialized); setSavedDocument(serialized); setHistory(versions.items);
          return;
        }
        setV2Card(null); setCharacterUsesV2(false);
      } else {
        setV2Card(null);
      }
      const [value, versions] = await Promise.all([
        request<Record<string, unknown>>(`/api/v1/profiles/${name}${characterQuery}`),
        request<{ items: ProfileHistoryItem[] }>(`/api/v1/profiles/${name}/history${characterQuery}`).catch(() => ({ items: [] })),
      ]);
      const serialized = JSON.stringify(value, null, 2); setDocument(serialized); setSavedDocument(serialized); setHistory(versions.items);
    } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); }
    finally { setLoading(false); }
  }, [characterId, characterQuery, name, notify]);
  useEffect(() => { void load(); }, [load]); useEffect(() => { onDirty(document !== savedDocument); return () => onDirty(false); }, [document, onDirty, savedDocument]);
  const updateValue = useCallback((path: string[], value: unknown) => { if (!parsed) return; const next = structuredClone(parsed); let cursor: Record<string, unknown> = next; path.slice(0, -1).forEach((key) => { cursor = cursor[key] as Record<string, unknown>; }); cursor[path[path.length - 1]] = value; setDocument(JSON.stringify(next, null, 2)); setError(""); }, [parsed]);
  const save = async () => {
    if (!parsed) { setError("JSON 格式无效，请修正后再保存。"); return; }
    setSaving(true); setError("");
    try {
      if (v2Card && name === "assistant" && characterId) {
        const roleName = str(parsed.name).trim();
        if (!roleName) throw new Error("角色名称不能为空");
        const baseData = profileObject(v2Card.data);
        const baseExtensions = profileObject(baseData.extensions);
        const baseMindspace = profileObject(baseExtensions.mindspace);
        const parsedMemory = profileObject(parsed.memory);
        const memory = {
          preferences: profileStringList(parsedMemory.preferences),
          tasks: profileStringList(parsedMemory.tasks),
        };
        const relationship = str(parsed.relationship).trim();
        const userAlias = str(parsed.user_alias).trim();
        const appearance = profileObject(parsed.appearance);
        const nextCard = {
          ...v2Card,
          data: {
            ...baseData,
            name: roleName,
            description: str(parsed.description),
            personality: str(parsed.personality),
            scenario: str(parsed.scenario),
            first_mes: str(parsed.first_mes),
            alternate_greetings: profileStringList(parsed.alternate_greetings),
            mes_example: str(parsed.mes_example),
            memory,
            extensions: {
              ...baseExtensions,
              mindspace: {
                ...baseMindspace,
                gender: str(parsed.gender || "不指定"),
                relationship,
                relationship_context: str(parsed.relationship_context),
                user_alias: userAlias,
                appearance,
              },
            },
          },
        };
        const result = await request<{ character: Record<string, unknown> }>(`/api/v1/characters/${encodeURIComponent(characterId)}`, {
          method: "PUT",
          body: JSON.stringify({ revision: Number(parsed.revision || 0), card: nextCard, memory, user_alias: userAlias, relationship_label: relationship }),
        });
        const serialized = JSON.stringify(v2ProfileEditorDocument(result.character), null, 2);
        setV2Card(profileObject(result.character.card)); setDocument(serialized); setSavedDocument(serialized);
        const versions = await request<{ items: ProfileHistoryItem[] }>(`/api/v1/characters/${encodeURIComponent(characterId)}/history`).catch(() => ({ items: [] }));
        setHistory(versions.items); onSaved(); notify("V2 角色卡已保存，后续对话将使用新版本");
      } else {
        const payload = name === "user" ? {
          schema_version: "1.3.0",
          profile_type: "user",
          revision: Number(parsed.revision || 0),
          identity: {
            preferred_name: str(profileObject(parsed.identity).preferred_name).trim(),
            gender: str(profileObject(parsed.identity).gender || "不指定"),
          },
          custom_profile: str(parsed.custom_profile).trim(),
        } : parsed;
        if (name === "user" && !str(profileObject(payload.identity).preferred_name).trim()) throw new Error("用户名字不能为空");
        const result = await request<{ document: Record<string, unknown> }>(`/api/v1/profiles/${name}${characterQuery}`, { method: "PUT", body: JSON.stringify(payload) });
        const serialized = JSON.stringify(result.document, null, 2); setDocument(serialized); setSavedDocument(serialized); onSaved(); notify("档案已保存，人物名称与后续对话将使用新版本");
      }
    } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); }
    finally { setSaving(false); }
  };
  const restorePrevious = async () => {
    const previous = history[0]; if (!previous || !parsed) return;
    if (!(await styledConfirm({ title: `恢复修订 ${previous.revision}？`, message: "当前版本仍会保留在历史中，并会生成一个新的修订版本。", confirmLabel: "恢复版本" }))) return;
    setSaving(true); setError("");
    try {
      if (v2Card && name === "assistant" && characterId) {
        await request(`/api/v1/characters/${encodeURIComponent(characterId)}/restore`, { method: "POST", body: JSON.stringify({ version_id: previous.version_id, expected_revision: Number(parsed.revision || 0) }) });
      } else {
        await request(`/api/v1/profiles/${name}/restore${characterQuery}`, { method: "POST", body: JSON.stringify({ version_id: previous.version_id, expected_revision: parsed.revision }) });
      }
      notify("已恢复上一版本，并生成新的修订"); await load(); onSaved();
    } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); }
    finally { setSaving(false); }
  };
  const switchProfile = async (id: Role | "state") => { if (document !== savedDocument && !(await styledConfirm({ title: "放弃未保存的修改？", message: "切换档案后，本页尚未保存的编辑会丢失。", confirmLabel: "继续切换", danger: true }))) return; if (id === "user") setMode("form"); setName(id); };
  const openMemory = async () => { if (document !== savedDocument && !(await styledConfirm({ title: "先放弃未保存的修改？", message: "进入长期记忆后，本页尚未保存的输入会丢失。", confirmLabel: "进入长期记忆", danger: true }))) return; onOpenMemory(); };
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
      {(characterUsesV2 ? [["user", "用户档案"], ["assistant", "V2 角色卡"]] : [["user", "用户档案"], ["assistant", "AI 档案"], ["state", "运行状态"]]).map(([id, label]) => <button className={name === id ? "active" : ""} key={id} onClick={() => switchProfile(id as Role | "state")}>{label}</button>)}
      <button className="profile-connection-tab" onClick={onOpenConnection}>API 连接 <span>↗</span></button>
    </div>
    <div className="profile-editor-toolbar">
      <p className="advanced-note">{name === "user" ? "这里只保存你的称呼、性别和手动补充资料；AI 与自动记忆不能改写。" : v2Card ? "这里直接编辑标准 chara_card_v2；名称、性别、关系、角色文本和偏好/任务记忆保存后立即进入后续对话。" : "用户修改直接生效并生成新 revision；AI 后续写回必须基于该 revision。"} 当前保留 {history.length} 个可恢复版本。</p>
      {name !== "user" && <div><button className={mode === "form" ? "active" : ""} onClick={() => setMode("form")}>表单编辑</button><button className={mode === "json" ? "active" : ""} onClick={() => setMode("json")}>高级 JSON</button></div>}
    </div>
    {error && <div className="profile-editor-error" role="alert">{error}</div>}
    {loading ? <div className="empty-mini">正在载入档案…</div> : name === "user" && parsed ? <div className="user-profile-compact">
      <section className="user-profile-card">
        <header><span>ABOUT YOU</span><h3>你的基础资料</h3><p>这些内容会作为稳定身份用于称呼、代词和角色互动。</p></header>
        <div className="user-profile-fields">
          <label><span>用户名字</span><input autoComplete="nickname" maxLength={80} value={str(profileObject(parsed.identity).preferred_name)} onChange={(event) => updateValue(["identity", "preferred_name"], event.target.value)} /></label>
          <label><span>用户性别</span><select value={str(profileObject(parsed.identity).gender || "不指定")} onChange={(event) => updateValue(["identity", "gender"], event.target.value)}><option value="男">男</option><option value="女">女</option><option value="不指定">不指定</option></select></label>
          <label className="user-profile-custom"><span>补充资料 <small>{str(parsed.custom_profile).length} / 500</small></span><textarea maxLength={500} value={str(parsed.custom_profile)} placeholder="可选：用自然语言填写职业、习惯、偏好或希望角色了解的稳定信息。" onChange={(event) => updateValue(["custom_profile"], event.target.value)} /></label>
        </div>
      </section>
      <button className="memory-jump-card" onClick={() => void openMemory()}>
        <span className="memory-jump-mark" aria-hidden="true">忆</span><span><strong>长期记忆</strong><small>查看和管理对话中形成的偏好、经历与重要事实</small></span><b aria-hidden="true">→</b>
      </button>
    </div> : mode === "json" ? <textarea aria-label="高级 JSON 编辑器" className="json-editor" value={document} onChange={(event) => { setDocument(event.target.value); setError(""); }} spellCheck={false} /> : parsed ? <div className="profile-form">{Object.entries(parsed).filter(([key]) => !PROFILE_TECHNICAL_FIELDS.has(key)).map(([key, value]) => <ProfileFieldEditor key={key} fieldKey={key} value={value} path={[key]} onChange={updateValue} />)}</div> : <div className="profile-editor-error" role="alert">JSON 格式无效，请切换到高级 JSON 修正。</div>}
  </Modal>;
}

function ProfileCardDialog({ characterId, role, avatars, displayName, onClose, onEdit }: { characterId: string; role: Role; avatars: AvatarConfig; displayName: string; onClose: () => void; onEdit: (role: Role) => void }) {
  const [card, setCard] = useState<ProfileCardData | null>(null); const [error, setError] = useState("");
  useEffect(() => { if (role === "assistant" && characterId) { request<{ data: Record<string, unknown> }>(`/api/v1/characters/${encodeURIComponent(characterId)}/card`).then((value) => { const data = value.data || {}; setCard({ name: "assistant", identity: { name: data.name, description: data.description }, personality: { personality: data.personality }, relationship: { scenario: data.scenario }, roleplay: { first_mes: data.first_mes, alternate_greetings: data.alternate_greetings, mes_example: data.mes_example }, revision: 0, updated_at: "" }); }).catch((reason: Error) => setError(reason.message)); return; } const query = role === "user" || !characterId ? "" : `?character_id=${encodeURIComponent(characterId)}`; request<ProfileCardData>(`/api/v1/profiles/${role}/card${query}`).then(setCard).catch((reason: Error) => setError(reason.message)); }, [characterId, role]);
  const blocks: [string, Record<string, unknown>][] = card ? [["身份信息", card.identity], ["人物性格", card.personality], ["角色演绎", card.roleplay || {}], ["近期关系", card.relationship]] : [];
  return <Modal title={`${displayName} · 人物卡`} kicker="CHARACTER CARD V2" onClose={onClose} compact footer={role === "user" ? <button className="primary" onClick={() => onEdit(role)}>编辑用户资料</button> : undefined}><div className="profile-card-hero"><PortraitAvatar role={role} avatars={avatars} label={displayName} /><div><h3>{displayName}</h3><p>{role === "assistant" ? "V2 角色卡；基础资料请在角色库编辑。" : "用户设定与偏好"}</p></div></div>{error ? <div className="profile-card-empty">{error}</div> : !card ? <div className="profile-card-empty">正在读取人物关键字段…</div> : <div className="profile-card-blocks">{blocks.map(([title, value]) => <section className="profile-card-block" key={title}><h3>{title}</h3>{Object.keys(value).length ? Object.entries(value).map(([key, item]) => <div className="profile-card-row" key={key}><span>{key}</span><strong>{friendlyValue(item)}</strong></div>) : <div className="profile-card-empty">暂无记录</div>}</section>)}</div>}</Modal>;
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

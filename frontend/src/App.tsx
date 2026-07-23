import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { consumeResumableEventStream, request } from "./api";
import { estimateDeliveredPrefix, segmentSpeechText, SpeechSegmenter, stripLeadingTtsFiller } from "./speech";
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
} from "./types";

type ModalName = "settings" | "knowledge" | "memory" | "profile" | "diagnostics" | "voice-entry" | null;

interface SpeechQueueItem {
  id: string;
  text: string;
  prepared?: Promise<PCMStreamHandle>;
}

interface PCMStreamHandle {
  sampleRate: number;
  chunks: ArrayBuffer[];
  done: boolean;
  error: Error | null;
  waiters: Set<() => void>;
  pump: Promise<void>;
  totalInputSamples: number;
}

const DEFAULT_AVATARS: AvatarConfig = {
  user: { src: "/assets/avatar-user-default.webp", aspect: "2 / 3", scale: 1.08, x: -12, y: 0 },
  assistant: { src: "/assets/avatar-ai-default.webp", aspect: "2 / 3", scale: 1, x: 0, y: 0 },
};

const VOICE_LABELS: Record<VoicePhase, string> = {
  idle: "准备开始",
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
const ACTIVE_RUN_STORAGE_KEY = "mindspace.active_run";

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

export function voiceMergeDelay(text: string, configured: unknown) {
  const normalDelay = Math.max(300, Math.min(3000, num(configured, 350)));
  return /(?:[。！？!?]|…{1,3})$/.test(text.trim()) ? Math.min(160, normalDelay) : normalDelay;
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

function App() {
  const [settings, setSettings] = useState<ProductSettings | null>(null);
  const [avatars, setAvatars] = useState<AvatarConfig>(DEFAULT_AVATARS);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState(localStorage.getItem("mindspace.session") || uid());
  const [messages, setMessages] = useState<Message[]>([]);
  const [round, setRound] = useState(1);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [runId, setRunId] = useState("");
  const [initialDataLoaded, setInitialDataLoaded] = useState(false);
  const [inspectionRunId, setInspectionRunId] = useState("");
  const runIdRef = useRef("");
  const [generating, setGenerating] = useState(false);
  const [modal, setModal] = useState<ModalName>(null);
  const [modalDirty, setModalDirty] = useState(false);
  const [profileCardRole, setProfileCardRole] = useState<Role | null>(null);
  const [profileEditorRole, setProfileEditorRole] = useState<Role | "state">("user");
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("flow");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [events, setEvents] = useState<InspectorEvent[]>([]);
  const [retrieval, setRetrieval] = useState<Record<string, unknown>[]>([]);
  const [toast, setToast] = useState("");
  const [voice, setVoice] = useState<VoiceSessionState>({ open: false, phase: "idle", transcript: "", reply: "", level: 0, error: "" });
  const [voiceEntryMode, setVoiceEntryMode] = useState<VoiceInteractionMode>("call");
  const [voiceEntryScene, setVoiceEntryScene] = useState("");
  const [voiceEntryBusy, setVoiceEntryBusy] = useState(false);
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
  const silentMonitorRef = useRef<GainNode | null>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const voiceSessionGenerationRef = useRef(0);
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
  const currentAssistantIdRef = useRef("");
  const lastVoiceRunIdRef = useRef("");
  const ttsWorkletLoadedRef = useRef(false);
  const playbackGenerationRef = useRef(0);
  const speechSegmenterRef = useRef(new SpeechSegmenter());
  const ttsResponseStartedRef = useRef(false);
  const partialRenderRef = useRef(0);
  const pendingResponseDeltaRef = useRef("");
  const responseFrameRef = useRef<number | null>(null);
  const closingVoiceRef = useRef(false);
  const idleTimerRef = useRef<number | null>(null);
  const idleContinuationSentRef = useRef(false);
  const voiceMergeTimerRef = useRef<number | null>(null);
  const voiceSegmentsRef = useRef<string[]>([]);
  const deferredVoiceSegmentsRef = useRef<string[]>([]);
  const voiceEmotionTokensRef = useRef<string[]>([]);
  const deferredEmotionTokensRef = useRef<string[]>([]);
  const activeVoiceEmotionTokensRef = useRef<string[]>([]);
  const activeVoiceTurnTextRef = useRef("");
  const activeVoiceTurnRoundRef = useRef(0);
  const pendingASREvidenceRef = useRef<{ uncertain_segments: Array<{ text: string; reason: string }>; decision_reasons: string[] } | null>(null);
  const lastBargeCommitAtRef = useRef(0);
  const bargeCommittedRef = useRef(false);
  const bargeBackoffRef = useRef({ level: 0, until: 0 });
  const recentVoiceTextsRef = useRef<Map<string, number>>(new Map());
  const queueVoiceSegmentRef = useRef<((text: string, deferred?: boolean, emotionToken?: string) => void) | null>(null);
  const noiseFloorRef = useRef(-60);
  const noiseReportRef = useRef({ value: -60, at: 0 });
  const inputRef = useRef("");
  const generatingRef = useRef(false);
  const roundRef = useRef(1);
  const sendMessageRef = useRef<((text?: string, mode?: "primary" | "regenerate", targetRound?: number, initiative?: boolean, initiativeTrigger?: InitiativeTrigger, voiceEmotionTokens?: string[], initiativeSequence?: number, initiativeSequenceLimit?: number) => Promise<void>) | null>(null);
  const llmMode = str(settings?.llm.mode || "openai");
  const llmBaseUrl = str(settings?.llm.base_url);
  const llmLocalEndpoint = /^https?:\/\/(?:127\.0\.0\.1|localhost)(?::|\/|$)/i.test(llmBaseUrl);
  const llmReady = llmMode === "openai" && (bool(settings?.llm.credentials_configured) || llmLocalEndpoint);

  const notify = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 3400);
  }, []);

  useEffect(() => { inputRef.current = input; }, [input]);
  useEffect(() => { generatingRef.current = generating; }, [generating]);
  useEffect(() => { roundRef.current = round; }, [round]);

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
        [],
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
    gain.gain.setTargetAtTime(ducked ? 0.25 : 1, context.currentTime, 0.035);
  }, []);

  const publishPlaybackState = useCallback((playing: boolean) => {
    const socket = voiceSocketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      const now = performance.now();
      if (bargeBackoffRef.current.until <= now) bargeBackoffRef.current = { level: 0, until: 0 };
      socket.send(JSON.stringify({
        action: "playback_state",
        playing,
        noise_floor_db: noiseFloorRef.current,
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
    const loadedMessages = value.messages || [];
    setMessages(loadedMessages);
    idleContinuationSentRef.current = loadedMessages.at(-1)?.initiative_trigger === "idle_continuation";
    setRound(Math.max(0, ...loadedMessages.map((item) => item.round || 0)) + 1);
    setSidebarOpen(false);
  }, [cancelIdleContinuation]);

  useEffect(() => {
    Promise.all([
      request<ProductSettings>("/api/v1/settings"),
      request<{ sessions: SessionSummary[] }>("/api/v1/sessions"),
      request<AvatarConfig>("/api/v1/avatar/config"),
    ]).then(async ([config, sessionResult, avatarResult]) => {
      setSettings(config);
      setSessions(sessionResult.sessions);
      setAvatars(normalizeAvatarConfig(avatarResult));
      const existing = sessionResult.sessions.find((item) => item.session_id === sessionId);
      if (existing) await openSession(existing.session_id);
      setInitialDataLoaded(true);
    }).catch((error: Error) => notify(error.message));
  }, [notify, openSession, sessionId]);

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
    ttsControllersRef.current.forEach((controller) => controller.abort());
    ttsControllersRef.current.clear();
    currentPlaybackNodeRef.current?.port.postMessage({ type: "stop" });
    currentPlaybackNodeRef.current?.disconnect();
    currentPlaybackNodeRef.current = null;
    currentPlaybackGainRef.current?.disconnect();
    currentPlaybackGainRef.current = null;
    currentPlaybackDoneRef.current?.();
    currentPlaybackDoneRef.current = null;
    void playbackContextRef.current?.close().catch(() => undefined);
    playbackContextRef.current = null;
    ttsWorkletLoadedRef.current = false;
    audioPlayingRef.current = false;
    currentSpeechRef.current = null;
    completedSpeechRef.current = [];
    setVoice((current) => ({ ...current, level: 0 }));
  }, [publishPlaybackState]);

  const playbackContext = useCallback(async () => {
    let context = playbackContextRef.current;
    if (!context || context.state === "closed") {
      context = new AudioContext({ latencyHint: "interactive" });
      playbackContextRef.current = context;
      ttsWorkletLoadedRef.current = false;
    }
    if (context.state === "suspended") await context.resume();
    if (!ttsWorkletLoadedRef.current) {
      await context.audioWorklet.addModule("/assets/tts-playback-worklet.js");
      ttsWorkletLoadedRef.current = true;
    }
    return context;
  }, []);

  const prepareSpeech = useCallback((item: SpeechQueueItem) => {
    if (item.prepared) return item.prepared;
    const controller = new AbortController();
    ttsControllersRef.current.add(controller);
    item.prepared = (async () => {
      const speed = num(settings?.audio.tts_speed, 1);
      const response = await fetch("/api/v1/audio/tts/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: item.text, request_id: runIdRef.current || uid(), speed }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(str(detail.detail || "语音合成失败"));
      }
      if (!response.body) throw new Error("浏览器不支持流式语音响应");
      const handle: PCMStreamHandle = {
        sampleRate: num(response.headers.get("X-Audio-Sample-Rate"), 24000),
        chunks: [], done: false, error: null, waiters: new Set(), pump: Promise.resolve(), totalInputSamples: 0,
      };
      const wake = () => {
        handle.waiters.forEach((resolve) => resolve());
        handle.waiters.clear();
      };
      const reader = response.body.getReader();
      handle.pump = (async () => {
        try {
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            if (value?.byteLength) {
              handle.totalInputSamples += Math.floor(value.byteLength / 2);
              handle.chunks.push(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
              wake();
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

  const prepareNextSpeech = useCallback(() => {
    const next = audioQueueRef.current.find((item) => !item.prepared);
    if (next) void prepareSpeech(next).catch(() => undefined);
  }, [prepareSpeech]);

  const playPCMStream = useCallback(async (item: SpeechQueueItem, handle: PCMStreamHandle, generation: number) => {
    const context = await playbackContext();
    if (generation !== playbackGenerationRef.current) return;
    const node = new AudioWorkletNode(context, "mindspace-tts-playback", {
      numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1],
    });
    currentPlaybackNodeRef.current = node;
    const gain = context.createGain();
    currentPlaybackGainRef.current = gain;
    node.connect(gain);
    gain.connect(context.destination);
    node.port.postMessage({ type: "configure", sampleRate: handle.sampleRate, prebufferMs: 120 });
    let resolveEnded: () => void = () => undefined;
    const ended = new Promise<void>((resolve) => { resolveEnded = resolve; });
    currentPlaybackDoneRef.current = resolveEnded;
    currentSpeechRef.current = { item, playedMs: 0, totalMs: 0, complete: false };
    node.port.onmessage = (event: MessageEvent<{ type: string; value?: number; playedFrames?: number; outputSampleRate?: number }>) => {
      if (event.data.type === "started" && voiceOpenRef.current) {
        publishPlaybackState(true);
        setVoiceInputLocked(false, "tts_started");
        setVoice((current) => ({ ...current, phase: "assistant-speaking", error: "" }));
      } else if (event.data.type === "level") {
        const playedMs = num(event.data.playedFrames) / Math.max(1, num(event.data.outputSampleRate, context.sampleRate)) * 1000;
        const receivedMs = handle.totalInputSamples / Math.max(1, handle.sampleRate) * 1000;
        const estimatedMs = Math.max(receivedMs, item.text.length * 180);
        currentSpeechRef.current = { item, playedMs, totalMs: estimatedMs, complete: handle.done };
        setVoice((current) => ({ ...current, level: num(event.data.value) }));
      } else if (event.data.type === "ended") {
        resolveEnded();
      }
    };
    try {
      while (generation === playbackGenerationRef.current) {
        while (handle.chunks.length) {
          const chunk = handle.chunks.shift()!;
          node.port.postMessage({ type: "push", pcm: chunk }, [chunk]);
        }
        if (handle.done) break;
        await new Promise<void>((resolve) => handle.waiters.add(resolve));
      }
      if (generation !== playbackGenerationRef.current) return;
      if (handle.error) throw handle.error;
      node.port.postMessage({ type: "end" });
      await ended;
    } finally {
      node.disconnect();
      gain.disconnect();
      if (currentPlaybackNodeRef.current === node) currentPlaybackNodeRef.current = null;
      if (currentPlaybackGainRef.current === gain) currentPlaybackGainRef.current = null;
      if (currentPlaybackDoneRef.current === resolveEnded) currentPlaybackDoneRef.current = null;
    }
  }, [playbackContext, publishPlaybackState, setVoiceInputLocked]);

  const playQueue = useCallback(async () => {
    if (audioPlayingRef.current || !audioQueueRef.current.length) return;
    audioPlayingRef.current = true;
    const generation = playbackGenerationRef.current;
    let playbackFailed = false;
    while (audioQueueRef.current.length) {
      const item = audioQueueRef.current[0];
      try {
        const stream = await prepareSpeech(item);
        if (generation !== playbackGenerationRef.current) return;
        audioQueueRef.current.shift();
        void stream.pump.then(prepareNextSpeech);
        await playPCMStream(item, stream, generation);
        if (generation === playbackGenerationRef.current) completedSpeechRef.current.push(item.text);
      } catch (error) {
        playbackFailed = true;
        if ((error as Error).name !== "AbortError") {
          const message = (error as Error).message;
          setVoiceInputLocked(false, "tts_failed");
          if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "error", error: message, level: 0 }));
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
      const deferredTokens = deferredEmotionTokensRef.current.splice(0);
      if (deferred.length) {
        deferred.forEach((text, index) => queueVoiceSegmentRef.current?.(text, false, deferredTokens[index]));
      } else if (!playbackFailed) {
        companionArmedRef.current = true;
        scheduleIdleContinuation("voice", true);
      }
    }
  }, [notify, playPCMStream, prepareNextSpeech, prepareSpeech, publishPlaybackState, scheduleIdleContinuation, setVoiceInputLocked]);

  const enqueueSpeech = useCallback((text: string, force = false) => {
    if ((!force && !voiceOpenRef.current && !bool(settings?.audio.auto_tts)) || !text.trim()) return;
    const speech = ttsResponseStartedRef.current ? text.trim() : stripLeadingTtsFiller(text);
    if (!speech) return;
    ttsResponseStartedRef.current = true;
    audioQueueRef.current.push({ id: uid(), text: speech });
    if (audioPlayingRef.current) prepareNextSpeech();
    else void playQueue();
  }, [playQueue, prepareNextSpeech, settings]);

  const acceptSpeechDelta = useCallback((delta: string, flush = false) => {
    const sentences = speechSegmenterRef.current.feed(delta, flush);
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
    if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "interrupted", reply: "", level: 0 }));
  }, [flushResponseDelta, setVoiceInputLocked, stopAudio]);

  const addEvent = useCallback((event: InspectorEvent) => setEvents((items) => [...items.slice(-79), event]), []);

  const handleStreamEvent = useCallback((event: StreamEnvelope) => {
    const data = asRecord(event.data);
    if (event.event === "run.accepted") {
      runIdRef.current = event.run_id;
      setInspectionRunId(event.run_id);
      if (voiceOpenRef.current) lastVoiceRunIdRef.current = event.run_id;
      setRunId(event.run_id);
      voiceSocketRef.current?.send(JSON.stringify({ action: "start", run_id: event.run_id }));
      if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "thinking", reply: "", error: "" }));
    } else if (event.event === "node.started") {
      addEvent({ event: str(data.node), label: str(data.label || data.node), timestamp: event.timestamp, state: "active" });
    } else if (event.event === "node.completed") {
      setEvents((items) => items.map((item) => item.event === str(data.node) && item.state === "active" ? { ...item, state: data.error ? "error" : "done" } : item));
    } else if (event.event === "response.delta") {
      const delta = str(data.delta);
      if (voiceOpenRef.current) voiceReplyRef.current += delta;
      acceptSpeechDelta(delta);
      scheduleResponseDelta(delta);
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
      if (!processRecovery) {
        stopAudio();
        if (voiceOpenRef.current) setVoiceInputLocked(true, "response_replaced");
      }
      const content = str(data.content);
      if (voiceOpenRef.current) voiceReplyRef.current = content;
      speechSegmenterRef.current.reset();
      if (!processRecovery) acceptSpeechDelta(content, true);
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
      acceptSpeechDelta("", true);
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, message_id: str(response.assistant_message_id) || item.message_id, content: str(response.reply || item.content), status: "complete" as const } : item));
      if (voiceOpenRef.current) setVoice((current) => ({ ...current, reply: str(response.reply || current.reply), phase: audioPlayingRef.current || audioQueueRef.current.length ? "assistant-speaking" : "listening" }));
      activeVoiceTurnTextRef.current = "";
      activeVoiceTurnRoundRef.current = 0;
      activeVoiceEmotionTokensRef.current = [];
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
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, status: "cancelled" as const } : item));
      setGenerating(false);
      runIdRef.current = "";
      setRunId("");
      clearActiveRun(event.run_id);
      setVoiceInputLocked(false, "run_cancelled_event");
    } else if (event.event === "run.interrupted") {
      flushResponseDelta();
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
          phase: "interrupted",
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
      if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "error", error: errors || "生成失败" }));
      activeVoiceTurnTextRef.current = "";
      activeVoiceTurnRoundRef.current = 0;
      activeVoiceEmotionTokensRef.current = [];
    }
  }, [acceptSpeechDelta, addEvent, clearPendingResponseDelta, flushResponseDelta, loadSessions, scheduleIdleContinuation, scheduleResponseDelta, setVoiceInputLocked, stopAudio]);

  const sendMessage = useCallback(async (
    text = input,
    mode: "primary" | "regenerate" = "primary",
    targetRound = round,
    initiative = false,
    initiativeTrigger: InitiativeTrigger = initiative ? "manual" : "none",
    voiceEmotionTokens: string[] = [],
    initiativeSequence = 0,
    initiativeSequenceLimit = 0,
  ) => {
    const content = initiative ? "请求 AI 主动回复" : text.trim();
    const asrEvidence = !initiative && voiceOpenRef.current
      ? pendingASREvidenceRef.current
      : null;
    pendingASREvidenceRef.current = null;
    if (!content) { notify("请输入消息内容"); return; }
    if (!llmReady) {
      notify("请先在设置中填写并保存 LLM API 配置");
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
    if (voiceOpenRef.current) setVoiceInputLocked(true, "turn_committed");
    speechSegmenterRef.current.reset();
    clearPendingResponseDelta();
    voiceReplyRef.current = "";
    currentAssistantIdRef.current = "";
    completedSpeechRef.current = [];
    setInput("");
    setEvents([]);
    setRetrieval([]);
    if (voiceOpenRef.current) setVoice((current) => ({ ...current, transcript: initiative ? current.transcript : content, reply: "", phase: "thinking", error: "" }));
    if (voiceOpenRef.current && !initiative) {
      activeVoiceTurnTextRef.current = content;
      activeVoiceTurnRoundRef.current = targetRound;
      activeVoiceEmotionTokensRef.current = [...voiceEmotionTokens];
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
    setMessages((items) => [...(mode === "regenerate" ? items.filter((item) => item.round !== targetRound) : items), ...outgoing]);
    const persona = settings?.persona || {};
    const retrievalSettings = settings?.retrieval || {};
    const llm = settings?.llm || {};
    // 这里只提交公开的人格、检索和采样参数。API key、base URL 和模型名由服务端覆盖，
    // 防止前端状态或请求重放改变真正使用的 provider 凭据。
    const payload = {
      message: content, session_id: sessionId, round: targetRound, mode, interaction_mode: voiceOpenRef.current ? "voice" : "text", initiative, initiative_trigger: initiativeTrigger,
      initiative_sequence: initiativeSequence, initiative_sequence_limit: initiativeSequenceLimit,
      client_sent_at: clientSentAt,
      client_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      client_utc_offset_minutes: -new Date().getTimezoneOffset(),
      voice_delivery: voiceOpenRef.current ? voiceDeliveryRef.current : null,
      voice_context: voiceOpenRef.current ? voiceInteractionRef.current : null,
      voice_emotion_tokens: voiceOpenRef.current ? voiceEmotionTokens.slice(0, 8) : [],
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
        setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content: (error as Error).message, status: "error" as const } : item));
        if (voiceOpenRef.current) setVoice((current) => ({ ...current, phase: "error", error: (error as Error).message }));
      }
      setGenerating(false);
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      setVoiceInputLocked(false, "request_failed");
    } finally {
      abortRef.current = null;
    }
  }, [cancelIdleContinuation, cancelRun, captureVoiceInterruption, clearPendingResponseDelta, generating, handleStreamEvent, input, llmReady, notify, round, sessionId, setVoiceInputLocked, settings, stopAudio]);

  useEffect(() => { sendMessageRef.current = sendMessage; }, [sendMessage]);

  useEffect(() => {
    if (!initialDataLoaded || generatingRef.current) return;
    const active = readActiveRun();
    if (!active || active.session_id !== sessionId) return;
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
      handleStreamEvent,
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
  }, [handleStreamEvent, initialDataLoaded, notify, sessionId]);

  const flushVoiceSegments = useCallback(async () => {
    voiceMergeTimerRef.current = null;
    const pending = voiceSegmentsRef.current.splice(0);
    const pendingEmotionTokens = voiceEmotionTokensRef.current.splice(0);
    if (!pending.length) return;
    const supplement = mergeVoiceText(pending);
    const hasActiveTurn = Boolean(activeVoiceTurnTextRef.current);
    const targetRound = hasActiveTurn ? activeVoiceTurnRoundRef.current : roundRef.current;
    const content = hasActiveTurn
      ? mergeVoiceText([activeVoiceTurnTextRef.current, supplement])
      : supplement;
    const emotionTokens = [...activeVoiceEmotionTokensRef.current, ...pendingEmotionTokens]
      .filter((token, index, values) => token && values.indexOf(token) === index)
      .slice(0, 8);
    if (!content) return;
    if (generatingRef.current) {
      await cancelRun();
      setMessages((items) => items.filter((item) => item.round !== targetRound));
    }
    setInput("");
    setVoice((current) => ({ ...current, transcript: content, phase: "thinking", error: "" }));
    await sendMessageRef.current?.(content, "primary", targetRound, false, "none", emotionTokens);
  }, [cancelRun]);

  const queueVoiceSegment = useCallback((text: string, deferred = false, emotionToken = "") => {
    const cleaned = text.trim();
    if (!cleaned) return;
    if (deferred && audioPlayingRef.current) {
      deferredVoiceSegmentsRef.current.push(cleaned);
      deferredEmotionTokensRef.current.push(emotionToken);
      setVoice((current) => ({ ...current, transcript: mergeVoiceText(deferredVoiceSegmentsRef.current), phase: "deferred", error: "" }));
      return;
    }
    const last = voiceSegmentsRef.current.at(-1);
    if (last !== cleaned) voiceSegmentsRef.current.push(cleaned);
    if (emotionToken && !voiceEmotionTokensRef.current.includes(emotionToken)) {
      voiceEmotionTokensRef.current.push(emotionToken);
    }
    if (voiceMergeTimerRef.current != null) window.clearTimeout(voiceMergeTimerRef.current);
    const preview = mergeVoiceText([
      activeVoiceTurnTextRef.current,
      ...voiceSegmentsRef.current,
    ]);
    setInput(preview);
    setVoice((current) => ({ ...current, transcript: preview, phase: "collecting", level: 0, error: "" }));
    const delay = voiceMergeDelay(cleaned, settings?.audio.asr_utterance_merge_ms);
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

  const stopListening = useCallback((finalize = false) => {
    voiceSessionGenerationRef.current += 1;
    const socket = voiceSocketRef.current;
    const worklet = workletRef.current;
    const source = audioSourceRef.current;
    const monitor = silentMonitorRef.current;
    const stream = mediaStreamRef.current;
    const context = audioContextRef.current;
    voiceSocketRef.current = null;
    workletRef.current = null;
    audioSourceRef.current = null;
    silentMonitorRef.current = null;
    mediaStreamRef.current = null;
    audioContextRef.current = null;
    if (worklet) worklet.port.onmessage = null;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ action: finalize ? "stop" : "cancel" }));
        }
        socket.close(1000, "voice mode closed");
      } catch { /* the connection is already closing */ }
    }
    try { source?.disconnect(); } catch { /* already disconnected */ }
    try { worklet?.disconnect(); } catch { /* already disconnected */ }
    try { monitor?.disconnect(); } catch { /* already disconnected */ }
    stream?.getTracks().forEach((track) => track.stop());
    if (context && context.state !== "closed") void context.close().catch(() => undefined);
  }, []);

  const startListening = useCallback(async () => {
    stopListening(false);
    if (!voiceOpenRef.current) return;
    const generation = voiceSessionGenerationRef.current;
    closingVoiceRef.current = false;
    setVoice((current) => ({ ...current, phase: "connecting", error: "", level: 0 }));
    let stream: MediaStream | null = null;
    let context: AudioContext | null = null;
    let source: MediaStreamAudioSourceNode | null = null;
    let worklet: AudioWorkletNode | null = null;
    let silentMonitor: GainNode | null = null;
    let socket: WebSocket | null = null;
    const isCurrent = () => (
      voiceOpenRef.current
      && !closingVoiceRef.current
      && voiceSessionGenerationRef.current === generation
    );
    const releaseLocal = () => {
      if (worklet) worklet.port.onmessage = null;
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        try { socket.close(1000, "stale voice session"); } catch { /* already closed */ }
      }
      try { source?.disconnect(); } catch { /* already disconnected */ }
      try { worklet?.disconnect(); } catch { /* already disconnected */ }
      try { silentMonitor?.disconnect(); } catch { /* already disconnected */ }
      stream?.getTracks().forEach((track) => track.stop());
      if (context && context.state !== "closed") void context.close().catch(() => undefined);
      if (voiceSocketRef.current === socket) voiceSocketRef.current = null;
      if (mediaStreamRef.current === stream) mediaStreamRef.current = null;
      if (audioContextRef.current === context) audioContextRef.current = null;
      if (audioSourceRef.current === source) audioSourceRef.current = null;
      if (workletRef.current === worklet) workletRef.current = null;
      if (silentMonitorRef.current === silentMonitor) silentMonitorRef.current = null;
    };
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
      if (!isCurrent()) { releaseLocal(); return; }
      context = new AudioContext({ latencyHint: "interactive" });
      await context.audioWorklet.addModule("/assets/pcm-worklet.js");
      if (!isCurrent()) { releaseLocal(); return; }
      source = context.createMediaStreamSource(stream);
      worklet = new AudioWorkletNode(context, "mindspace-pcm");
      worklet.port.postMessage({
        type: "configure",
        noiseGateDb: num(settings?.audio.asr_noise_gate_db, -42),
        adaptive: bool(settings?.audio.asr_adaptive_noise_enabled ?? true),
        calibrationMs: num(settings?.audio.asr_noise_calibration_ms, 1500),
        noiseMarginDb: 8,
      });
      silentMonitor = context.createGain();
      silentMonitor.gain.value = 0;
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${location.host}/api/v1/audio/asr/stream`);
      socket.binaryType = "arraybuffer";
      if (!isCurrent()) { releaseLocal(); return; }
      const activeSocket = socket as WebSocket;
      const activeStream = stream as MediaStream;
      const activeContext = context as AudioContext;
      const activeSource = source as MediaStreamAudioSourceNode;
      const activeWorklet = worklet as AudioWorkletNode;
      const activeMonitor = silentMonitor as GainNode;
      voiceSocketRef.current = activeSocket;
      mediaStreamRef.current = activeStream;
      audioContextRef.current = activeContext;
      audioSourceRef.current = activeSource;
      workletRef.current = activeWorklet;
      silentMonitorRef.current = activeMonitor;
      activeWorklet.port.onmessage = (event: MessageEvent<{ pcm: ArrayBuffer; level: number; noiseFloorDb?: number; calibrated?: boolean }>) => {
        if (!isCurrent()) return;
        if (activeSocket.readyState !== WebSocket.OPEN || activeSocket.bufferedAmount > 256 * 1024) return;
        const floor = num(event.data.noiseFloorDb, noiseFloorRef.current);
        noiseFloorRef.current = floor;
        if (voiceInputLockedRef.current) return;
        activeSocket.send(event.data.pcm);
        const now = performance.now();
        if (event.data.calibrated && now - noiseReportRef.current.at >= 750 && Math.abs(floor - noiseReportRef.current.value) >= 1) {
          noiseReportRef.current = { value: floor, at: now };
          activeSocket.send(JSON.stringify({ action: "playback_state", playing: audioPlayingRef.current, noise_floor_db: floor }));
        }
        if (!audioPlayingRef.current) setVoice((current) => ({ ...current, level: event.data.level }));
      };
      activeSocket.onopen = () => {
        if (!isCurrent()) { releaseLocal(); return; }
        activeSocket.send(JSON.stringify({ action: "start", run_id: runIdRef.current }));
        activeSocket.send(JSON.stringify({ action: "playback_state", playing: audioPlayingRef.current }));
        activeSource.connect(activeWorklet);
        activeWorklet.connect(activeMonitor);
        activeMonitor.connect(activeContext.destination);
      };
      activeSocket.onmessage = (event) => {
        if (!isCurrent()) return;
        let payload: { event: string; data: Record<string, unknown> };
        try {
          payload = JSON.parse(String(event.data)) as { event: string; data: Record<string, unknown> };
        } catch {
          setVoice((current) => ({ ...current, phase: "error", error: "实时语音返回了无效数据", level: 0 }));
          return;
        }
        if (shouldIgnoreASREvent(voiceInputLockedRef.current, payload.event)) return;
        if (payload.event === "asr.ready") {
          setVoice((current) => ({ ...current, phase: "listening", error: "" }));
          scheduleIdleContinuation("voice");
        }
        if (payload.event === "asr.loading") setVoice((current) => ({ ...current, phase: "connecting" }));
        if (payload.event === "asr.speech_candidate") {
          cancelIdleContinuation();
          bargeCommittedRef.current = false;
          if (audioPlayingRef.current) {
            setPlaybackDucked(true);
            setVoice((current) => ({ ...current, phase: "candidate-interruption", error: "" }));
          } else {
            setVoice((current) => ({ ...current, phase: "user-speaking", error: "" }));
          }
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
          const emotionToken = str(payload.data.emotion_token);
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
              queueVoiceSegment(confirmedText, false, emotionToken);
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
          if (confirmed && quality !== "rejected") queueVoiceSegment(confirmed, true, str(payload.data.emotion_token));
          else setPlaybackDucked(false);
        }
        if (payload.event === "asr.interrupted") setVoice((current) => ({ ...current, phase: "interrupted", reply: "", level: 0 }));
        if (payload.event === "asr.error") setVoice((current) => ({ ...current, phase: "error", error: str(payload.data.error), level: 0 }));
      };
      activeSocket.onerror = () => {
        if (isCurrent()) setVoice((current) => ({ ...current, phase: "error", error: "实时语音连接失败", level: 0 }));
      };
      activeSocket.onclose = () => {
        if (isCurrent()) setVoice((current) => ({ ...current, phase: "error", error: current.error || "语音连接已断开", level: 0 }));
      };
    } catch (error) {
      const current = isCurrent();
      releaseLocal();
      if (current) {
        voiceSessionGenerationRef.current += 1;
        setVoice((state) => ({ ...state, phase: "error", error: `无法使用麦克风：${(error as Error).message}`, level: 0 }));
      }
    }
  }, [cancelIdleContinuation, cancelRun, captureVoiceInterruption, queueVoiceSegment, scheduleIdleContinuation, setPlaybackDucked, settings?.audio.asr_adaptive_noise_enabled, settings?.audio.asr_noise_calibration_ms, settings?.audio.asr_noise_gate_db, stopAudio, stopListening]);

  const enterVoice = useCallback((context: VoiceInteractionContext) => {
    cancelIdleContinuation();
    idleContinuationSentRef.current = false;
    companionRoundRef.current = 0;
    companionArmedRef.current = false;
    voiceInputLockedRef.current = false;
    setCompanionRound(0);
    voiceOpenRef.current = true;
    voiceInteractionRef.current = context;
    voiceSegmentsRef.current = [];
    deferredVoiceSegmentsRef.current = [];
    voiceEmotionTokensRef.current = [];
    deferredEmotionTokensRef.current = [];
    activeVoiceEmotionTokensRef.current = [];
    activeVoiceTurnTextRef.current = "";
    activeVoiceTurnRoundRef.current = 0;
    pendingASREvidenceRef.current = null;
    setVoice({ open: true, phase: "connecting", transcript: "", reply: "", level: 0, error: "" });
    void startListening();
  }, [cancelIdleContinuation, startListening]);

  const openVoiceEntry = useCallback(() => {
    const saved = savedVoiceInteraction(settings);
    setVoiceEntryMode(saved.mode);
    setVoiceEntryScene(saved.scene);
    setVoiceEntryBusy(false);
    setModalDirty(false);
    setModal("voice-entry");
  }, [settings]);

  const startVoiceFromEntry = useCallback(async () => {
    if (voiceEntryBusy) return;
    const context: VoiceInteractionContext = {
      mode: voiceEntryMode,
      scene: voiceEntryScene.trim().slice(0, 2000),
    };
    setVoiceEntryBusy(true);
    try {
      const result = await request<{ settings: ProductSettings }>("/api/v1/settings", {
        method: "PUT",
        body: JSON.stringify({
          interaction: {
            voice_entry_mode: context.mode,
            face_to_face_scene: context.scene,
          },
        }),
      });
      setSettings(result.settings);
      setModalDirty(false);
      setModal(null);
      enterVoice(context);
    } catch (error) {
      notify((error as Error).message);
    } finally {
      setVoiceEntryBusy(false);
    }
  }, [enterVoice, notify, voiceEntryBusy, voiceEntryMode, voiceEntryScene]);

  const exitVoice = useCallback(() => {
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
    voiceEmotionTokensRef.current = [];
    deferredEmotionTokensRef.current = [];
    activeVoiceEmotionTokensRef.current = [];
    activeVoiceTurnTextRef.current = "";
    activeVoiceTurnRoundRef.current = 0;
    stopListening(false);
    stopAudio();
    setVoice({ open: false, phase: "idle", transcript: "", reply: "", level: 0, error: "" });
    if (messages.some((item) => item.role === "assistant" && item.status === "complete")) {
      scheduleIdleContinuation("text");
    }
  }, [cancelIdleContinuation, messages, scheduleIdleContinuation, stopAudio, stopListening]);

  const retryVoice = useCallback(() => {
    voiceInputLockedRef.current = false;
    stopAudio();
    void startListening();
  }, [startListening, stopAudio]);

  const newSession = useCallback(() => {
    cancelIdleContinuation();
    idleContinuationSentRef.current = false;
    companionRoundRef.current = 0;
    companionArmedRef.current = false;
    setCompanionRound(0);
    voiceDeliveryRef.current = null;
    if (generating) void cancelRun();
    const id = uid();
    setSessionId(id);
    localStorage.setItem("mindspace.session", id);
    setMessages([]); setRound(1); setEvents([]); setRetrieval([]); setSidebarOpen(false);
    notify("已创建新对话");
  }, [cancelIdleContinuation, cancelRun, generating, notify]);

  const deleteSession = async (id: string) => {
    if (!window.confirm("确定删除这个会话吗？")) return;
    await request(`/api/v1/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (id === sessionId) newSession();
    await loadSessions();
    notify("会话已删除");
  };

  const deleteReply = async (messageId?: string) => {
    if (!messageId) { notify("回复尚未完成保存，暂时不能删除"); return; }
    if (!window.confirm("删除这条 AI 回复？用户原话会保留，JSON 将在下一轮重新校正。")) return;
    const result = await request<{ pending_json_reconciliation: boolean }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`, { method: "DELETE" });
    setMessages((items) => items.filter((item) => item.message_id !== messageId));
    await loadSessions();
    notify(result.pending_json_reconciliation ? "回复已删除；相关 JSON 将在下一轮重新校正" : "主动回复已删除");
  };

  const clearCurrent = async () => {
    if (!messages.length) { notify("当前会话没有可清空的内容"); return; }
    if (!window.confirm("清空当前会话上下文？")) return;
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

  const speakMessage = (text: string) => {
    stopAudio();
    segmentSpeechText(text).forEach((sentence) => enqueueSpeech(sentence, true));
  };
  const openModal = (name: Exclude<ModalName, null>) => { setModalDirty(false); setModal(name); };
  const closeModal = useCallback(() => {
    if (modalDirty && !window.confirm("存在未保存的修改，确定关闭吗？")) return;
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
        else if (modal) closeModal();
        else if (generating) void cancelRun();
      }
      if (ctrl && event.key.toLowerCase() === "n") { event.preventDefault(); newSession(); }
      if (ctrl && event.shiftKey && event.key.toLowerCase() === "m") { event.preventDefault(); voiceOpenRef.current ? exitVoice() : openVoiceEntry(); }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [cancelRun, closeModal, exitVoice, generating, modal, newSession, openVoiceEntry, profileCardRole]);

  useEffect(() => () => { closingVoiceRef.current = true; stopListening(false); stopAudio(); }, [stopAudio, stopListening]);

  const filteredSessions = useMemo(() => sessions.filter((item) => item.title.toLowerCase().includes(search.toLowerCase())), [search, sessions]);
  const title = sessions.find((item) => item.session_id === sessionId)?.title || "新对话";
  const userName = str(settings?.persona.user_name || "用户");
  const characterName = str(settings?.persona.character_name || "Mindspace");

  return <div className={`app-shell ${inspectorOpen ? "inspector-visible" : "inspector-hidden"}`}>
    <aside className={`sidebar ${sidebarOpen ? "mobile-open" : ""}`}>
      <div className="brand-row"><div className="brand-mark">M</div><div><strong>Mindspace</strong><small>LANGGRAPH STUDIO</small></div><button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} aria-label="关闭会话栏">×</button></div>
      <button className="new-chat" onClick={newSession}><span>＋</span> 新建对话 <kbd>Ctrl N</kbd></button>
      <label className="search-box"><span>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索会话" aria-label="搜索会话" /></label>
      <div className="session-heading"><span>最近会话</span><small>{filteredSessions.length}</small></div>
      <nav className="session-list">
        {filteredSessions.length ? filteredSessions.map((item) => <div className={`session-item ${item.session_id === sessionId ? "active" : ""}`} key={item.session_id}><button className="session-open" onClick={() => void openSession(item.session_id)}><span className="session-glyph">◌</span><span><strong>{item.title}</strong><small>{item.message_count} 条 · {formatTime(item.updated_at)}</small></span></button><button className="session-delete" onClick={() => void deleteSession(item.session_id)} title="删除会话" aria-label={`删除会话 ${item.title}`}>×</button></div>) : <div className="empty-mini">没有匹配的会话</div>}
      </nav>
      <div className="sidebar-tools"><button onClick={() => openModal("knowledge")}><span>▱</span> 全局知识库</button><button onClick={() => openModal("memory")}><span>◎</span> 记忆中心</button><button onClick={() => { setProfileEditorRole("user"); openModal("profile"); }}><span>◇</span> 人物与状态档案</button><button onClick={() => openModal("diagnostics")}><span>⌁</span> 系统诊断</button></div>
      <div className="account-card"><PortraitAvatar role="assistant" avatars={avatars} label={characterName} className="small" onClick={() => setProfileCardRole("assistant")} /><button className="account-settings" onClick={() => openModal("settings")}><span><strong>{characterName}</strong><small><i /> 服务已连接</small></span><b>设置</b></button></div>
    </aside>

    <main className="workspace">
      <header className="topbar"><button className="mobile-only mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="打开会话栏">☰</button><div className="title-block"><h1>{title}</h1><span>{generating ? "正在运行编排" : `第 ${round} 轮 · 已就绪`}</span></div><div className="top-actions"><span className={`model-chip ${llmReady ? "" : "warning"}`} title={llmReady ? "真实 LLM API 已配置" : "LLM API 尚未配置"}><i />{llmReady ? str(settings?.llm.model || "LLM") : "LLM 未配置"}</span><button onClick={exportSession} title="导出会话" aria-label="导出会话">⇩</button><button className="text-action" onClick={showFlow} title="查看 LangGraph 节点执行过程">执行详情</button><button onClick={() => openModal("settings")} title="产品设置" aria-label="产品设置">⚙</button></div></header>
      <section className="conversation">
        {!messages.length && <div className="welcome-panel"><span className="eyebrow">PRIVATE · LOCAL · STATEFUL</span><h2>让每一次对话<br />都成为连续的记忆</h2><p>双源检索、角色一致性、状态写回与实时语音，都由可观察的 LangGraph 流程调度。</p><div className="prompt-grid">{["解释当前 LangGraph 节点如何调度", "总结知识库中最重要的三点", "检查当前角色和状态档案", "设计一个低延迟语音对话流程"].map((value) => <button key={value} onClick={() => void sendMessage(value)}><span>↗</span>{value}</button>)}</div></div>}
        <MessageList messages={messages} avatars={avatars} userName={userName} characterName={characterName} onProfile={setProfileCardRole} onCopy={(text) => { void navigator.clipboard.writeText(text); notify("已复制回复"); }} onSpeak={speakMessage} onRegenerate={(value, targetRound) => void sendMessage(value, "regenerate", targetRound)} onInitiative={(targetRound) => void sendMessage("", "regenerate", targetRound, true)} onDelete={(messageId) => void deleteReply(messageId)} />
      </section>
      <section className="composer-wrap">{generating && <div className="run-strip"><span><i /> 正在流式执行 · {runId.slice(0, 8)}</span><button onClick={() => void cancelRun()}>停止生成</button></div>}<div className="composer"><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void sendMessage(); } }} placeholder="输入消息，或开启实时语音…" rows={1} /><div className="composer-row"><div><button className="voice-entry" onClick={openVoiceEntry}>● 实时语音</button><button className="initiative-entry" disabled={generating} onClick={() => void sendMessage("", "primary", round, true)} title="不输入文字，让角色根据当前关系主动说点什么">✦ 让 AI 说点什么</button><button onClick={() => openModal("knowledge")}>＋ 知识</button><button onClick={showContext}>本轮引用 <b>{retrieval.length}</b></button></div><button className="send" onClick={() => generating ? void cancelRun() : void sendMessage()} disabled={!generating && !input.trim()} aria-label={generating ? "停止生成" : "发送消息"}>{generating ? "■" : "↑"}</button></div></div><div className="composer-meta"><span>Enter 发送 · Shift+Enter 换行 · Esc 打断</span><button onClick={() => void clearCurrent()}>清空当前上下文</button></div></section>
    </main>

    <Inspector open={inspectorOpen} tab={inspectorTab} onTab={setInspectorTab} onClose={() => setInspectorOpen(false)} events={events} retrieval={retrieval} runId={inspectionRunId} />
    {modal === "settings" && settings && <SettingsDialog value={settings} avatars={avatars} onClose={closeModal} onDirty={setModalDirty} onSaved={(next, nextAvatars) => { setSettings(next); setAvatars(nextAvatars); setModalDirty(false); setModal(null); }} onSettingsChange={setSettings} onAvatarsChange={setAvatars} notify={notify} />}
    {modal === "knowledge" && <KnowledgeDialog onClose={closeModal} onDirty={setModalDirty} notify={notify} />}
    {modal === "memory" && <MemoryDialog onClose={closeModal} onDirty={setModalDirty} notify={notify} />}
    {modal === "profile" && <ProfileDialog initialName={profileEditorRole} onClose={closeModal} onDirty={setModalDirty} notify={notify} />}
    {modal === "diagnostics" && <DiagnosticsDialog onClose={closeModal} notify={notify} onCleared={() => { newSession(); void loadSessions(); }} />}
    {modal === "voice-entry" && <VoiceEntryDialog mode={voiceEntryMode} scene={voiceEntryScene} busy={voiceEntryBusy} onModeChange={(next) => { setVoiceEntryMode(next); setModalDirty(true); }} onSceneChange={(next) => { setVoiceEntryScene(next); setModalDirty(true); }} onClose={closeModal} onStart={() => void startVoiceFromEntry()} />}
    {profileCardRole && <ProfileCardDialog role={profileCardRole} avatars={avatars} displayName={profileCardRole === "user" ? userName : characterName} onClose={() => setProfileCardRole(null)} onEdit={(role) => { setProfileCardRole(null); setProfileEditorRole(role); setModal("profile"); }} />}
    {voice.open && <VoiceMode state={voice} avatar={avatars.assistant} characterName={characterName} context={voiceInteractionRef.current} companion={{ enabled: bool(settings?.interaction?.unlimited_reply_enabled), round: companionRound, limit: Math.max(1, Math.min(50, num(settings?.interaction?.unlimited_reply_max_rounds, 10))) }} onExit={exitVoice} onRetry={retryVoice} />}
    {toast && <div className="toast" role="status">{toast}</div>}
  </div>;
}

const MessageList = memo(function MessageList({ messages, avatars, userName, characterName, onProfile, onCopy, onSpeak, onRegenerate, onInitiative, onDelete }: {
  messages: Message[]; avatars: AvatarConfig; userName: string; characterName: string;
  onProfile: (role: Role) => void; onCopy: (text: string) => void; onSpeak: (text: string) => void;
  onRegenerate: (text: string, round: number) => void; onInitiative: (round: number) => void; onDelete: (messageId?: string) => void;
}) {
  return <div className="message-list">{messages.map((message, index) => {
    const label = message.role === "user" ? userName : characterName;
    const initiativeLabel = message.initiative_trigger === "continuous_companionship" ? "· 连续陪伴" : message.kind === "initiative_response" ? "· 主动回应" : "";
    return <article className={`message ${message.role} ${message.status || "complete"}`} key={message.message_id || `${message.round}-${message.role}-${index}`}><PortraitAvatar role={message.role} avatars={avatars} label={label} onClick={() => onProfile(message.role)} /><div className="message-content"><div className="message-head"><strong>{label}</strong><span>第 {message.round} 轮 {initiativeLabel}{message.status === "streaming" && "· 正在生成"}{message.status === "cancelled" && "· 已打断"}{message.status === "interrupted" && "· 回答在此处中断"}{message.status === "error" && "· 失败"}</span></div><div className="message-text">{richText(message.content || (message.status === "streaming" ? "" : "…"))}{message.status === "streaming" && <i className="stream-caret" />}</div>{message.role === "assistant" && message.status !== "streaming" && <div className="message-actions"><button onClick={() => onCopy(message.content)}>复制</button><button onClick={() => onSpeak(message.content)}>朗读</button><button onClick={() => { if (message.kind === "initiative_response") { onInitiative(message.round); return; } const user = messages.find((item) => item.role === "user" && item.round === message.round); if (user) onRegenerate(user.content, message.round); }}>重新生成</button><button onClick={() => onDelete(message.message_id)}>删除回复</button></div>}</div></article>;
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

function VoiceEntryDialog({ mode, scene, busy, onModeChange, onSceneChange, onClose, onStart }: {
  mode: VoiceInteractionMode;
  scene: string;
  busy: boolean;
  onModeChange: (mode: VoiceInteractionMode) => void;
  onSceneChange: (scene: string) => void;
  onClose: () => void;
  onStart: () => void;
}) {
  return <Modal title="选择互动方式" kicker="LIVE INTERACTION" onClose={onClose} compact className="voice-entry-card" footer={<><button className="secondary" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy} onClick={onStart}>{busy ? "正在保存…" : mode === "face_to_face" ? "开始面对面互动" : "开始通话"}</button></>}>
    <div className="voice-entry-setup">
      <p className="notice">选择会保存为下次默认值。通话保持原有语音逻辑；面对面会在每轮语音中加载你保存的场景，但不会把场景自动写成人物事实或长期记忆。</p>
      <div className="voice-entry-options" role="group" aria-label="互动方式">
        <button type="button" className={mode === "call" ? "active" : ""} aria-pressed={mode === "call"} onClick={() => onModeChange("call")}><span>通话</span><small>默认 · 保持现有实时语音逻辑</small></button>
        <button type="button" className={mode === "face_to_face" ? "active" : ""} aria-pressed={mode === "face_to_face"} onClick={() => onModeChange("face_to_face")}><span>面对面</span><small>通过语言呈现角色的外观、动作、距离与体感互动</small></button>
      </div>
      {mode === "face_to_face" && <label className="voice-scene-field"><span>当前场景</span><textarea aria-label="当前场景" value={scene} maxLength={2000} rows={6} placeholder="例如：深夜的客厅，只开着落地灯，窗外正在下雨；我们坐在沙发两端。" onChange={(event) => onSceneChange(event.target.value)} /><small>{scene.length} / 2000 · 可留空，角色会使用未指定的普通面对面场景</small></label>}
      <p className="voice-entry-boundary">{mode === "face_to_face" ? "用户默认看不到角色画面；角色会在合适时自然说出自身动作和可感知线索，但不会擅自替用户决定动作、反应或感受。" : "AI 只会知道当前正在实时语音通话，不会额外描述共同所处的物理场景。"}</p>
    </div>
  </Modal>;
}

function Modal({ title, kicker, onClose, children, footer, compact = false, className = "" }: { title: string; kicker: string; onClose: () => void; children: ReactNode; footer?: ReactNode; compact?: boolean; className?: string }) {
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) event.preventDefault(); }}><section className={`modal-card ${compact ? "compact" : ""} ${className}`.trim()} role="dialog" aria-modal="true" aria-label={title}><header><div><span className="eyebrow">{kicker}</span><h2>{title}</h2></div><button onClick={onClose} aria-label={`关闭${title}`}>×</button></header><div className="modal-body">{children}</div>{footer && <footer>{footer}</footer>}</section></div>;
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

function SettingsDialog({ value, avatars, onClose, onDirty, onSaved, onSettingsChange, onAvatarsChange, notify }: { value: ProductSettings; avatars: AvatarConfig; onClose: () => void; onDirty: (dirty: boolean) => void; onSaved: (value: ProductSettings, avatars: AvatarConfig) => void; onSettingsChange: (value: ProductSettings) => void; onAvatarsChange: (value: AvatarConfig) => void; notify: (message: string) => void }) {
  const normalizedValue: ProductSettings = {
    ...structuredClone(value),
    audio: {
      asr_listening_energy_threshold_db: -36,
      asr_listening_min_speech_ms: 160,
      asr_barge_in_energy_threshold_db: -27,
      asr_barge_in_min_speech_ms: 420,
      asr_candidate_release_ms: 280,
      asr_adaptive_noise_enabled: true,
      asr_noise_calibration_ms: 1500,
      asr_listening_noise_margin_db: 10,
      asr_barge_in_noise_margin_db: 16,
      asr_utterance_merge_ms: 350,
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
      local_status_enabled: true,
      mindspace_health_enabled: true,
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
  const [tab, setTab] = useState("model");
  const [audioBusy, setAudioBusy] = useState("");
  const [audioStatus, setAudioStatus] = useState(bool(value.audio.tts_reference_configured) ? `已配置参考音频：${str(value.audio.tts_reference_name)}` : "尚未上传参考音频");
  const [providerBusy, setProviderBusy] = useState(false);
  const [providerStatus, setProviderStatus] = useState("切换链路后立即保存，无需再点击底部保存按钮");
  const [gptVoices, setGptVoices] = useState<{ active_voice: string; items: Array<{ id: string; label: string; family: string; installed: boolean; selected: boolean }> }>({ active_voice: "v4-changli", items: [] });
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
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  useEffect(() => {
    request<{ active_voice: string; items: Array<{ id: string; label: string; family: string; installed: boolean; selected: boolean }> }>("/api/v1/audio/tts/voices")
      .then(setGptVoices)
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
    const provider = ["cosyvoice", "gpt-sovits"].includes(next) ? next : "siliconflow";
    const previous = str(draft.audio.tts_provider || "siliconflow");
    if (provider === previous || providerBusy) return;
    setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: provider } }));
    setProviderBusy(true);
    setProviderStatus(provider === "cosyvoice" ? "正在切换到本地 CosyVoice…" : provider === "gpt-sovits" ? "正在切换到独立 GPT-SoVITS…" : "正在切换到 SiliconFlow API…");
    try {
      const result = await request<{ settings: ProductSettings }>("/api/v1/settings", { method: "PUT", body: JSON.stringify({ audio: { tts_provider: provider } }) });
      const confirmed = str(result.settings.audio.tts_provider);
      setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: confirmed } }));
      const baseline = JSON.parse(initial.current) as { value: ProductSettings; avatars: AvatarConfig };
      baseline.value = { ...baseline.value, audio: { ...baseline.value.audio, tts_provider: confirmed } };
      initial.current = JSON.stringify(baseline);
      onSettingsChange(result.settings);
      const label = confirmed === "cosyvoice" ? "本地 CosyVoice" : confirmed === "gpt-sovits" ? "本地 GPT-SoVITS" : "SiliconFlow API";
      setProviderStatus(`已切换并保存：${label}`);
      notify(`TTS 链路已切换为${label}`);
    } catch (error) {
      setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: previous } }));
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
    if (!window.confirm("清除当前参考音频和参考文本？")) return;
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
  const ttsProvider = str(draft.audio.tts_provider || "cosyvoice");
  const uploadAvatar = async (role: Role, file: File) => {
    if (file.size > 5 * 1024 * 1024) { notify("头像不能超过 5 MiB"); return; }
    setAvatarBusy(role);
    try {
      const form = new FormData(); form.append("file", file);
      const result = await request<{ config: AvatarConfig }>(`/api/v1/avatar/upload/${role}`, { method: "POST", body: form });
      const normalized = normalizeAvatarConfig(result.config); setAvatarDraft(normalized); onAvatarsChange(normalized); notify(`${role === "assistant" ? "AI" : "用户"}头像上传成功`);
    } catch (error) { notify((error as Error).message); } finally { setAvatarBusy(""); }
  };
  const tabs = [["model", "模型与角色"], ["avatar", "人物头像"], ["rag", "RAG 与分块"], ["protocol", "协议"], ["audio", "实时语音"], ["vocabulary", "识别词表"], ["appearance", "界面"], ["rhythm", "陪伴频率"], ["capabilities", "自动能力"]];
  return <Modal title="产品设置" kicker="CONTROL CENTER" onClose={onClose} footer={<><button className="secondary" onClick={onClose}>取消</button><button className="primary" onClick={() => void save()}>保存设置</button></>}><div className="settings-layout"><nav>{tabs.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}</nav><div className="settings-panel">
    {tab === "model" && <>
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
        <div className="toggle-grid"><Field label="允许只读自动能力" value={draft.capabilities?.master_enabled} type="checkbox" onChange={(next) => update("capabilities", "master_enabled", next)} /><Field label="读取本机状态" value={draft.capabilities?.local_status_enabled} type="checkbox" onChange={(next) => update("capabilities", "local_status_enabled", next)} /><Field label="检查 Mindspace 服务" value={draft.capabilities?.mindspace_health_enabled} type="checkbox" onChange={(next) => update("capabilities", "mindspace_health_enabled", next)} /><Field label="自动查询本地知识" value={draft.capabilities?.local_knowledge_enabled} type="checkbox" onChange={(next) => update("capabilities", "local_knowledge_enabled", next)} /><Field label="允许联网搜索" value={draft.capabilities?.web_search_enabled} type="checkbox" onChange={(next) => update("capabilities", "web_search_enabled", next)} /><Field label="允许实时热点" value={draft.capabilities?.realtime_topics_enabled} type="checkbox" onChange={(next) => update("capabilities", "realtime_topics_enabled", next)} /><Field label="自然扩展相关话题" value={draft.capabilities?.topic_expansion_enabled} type="checkbox" onChange={(next) => update("capabilities", "topic_expansion_enabled", next)} /><Field label="沉默续接可参考热点" value={draft.capabilities?.proactive_hotspots_enabled} type="checkbox" onChange={(next) => update("capabilities", "proactive_hotspots_enabled", next)} /><Field label="回答中展示网页来源" value={draft.capabilities?.show_sources_enabled} type="checkbox" onChange={(next) => update("capabilities", "show_sources_enabled", next)} /></div>
        <h3>联网边界</h3><div className="form-grid"><Field label="联网超时秒数" value={draft.capabilities?.web_timeout_seconds} type="number" min={2} max={30} step={1} onChange={(next) => update("capabilities", "web_timeout_seconds", next)} /><Field label="搜索结果上限" value={draft.capabilities?.max_web_results} type="number" min={1} max={20} step={1} onChange={(next) => update("capabilities", "max_web_results", next)} /><Field label="打开原文上限" value={draft.capabilities?.max_web_pages} type="number" min={0} max={10} step={1} onChange={(next) => update("capabilities", "max_web_pages", next)} /><Field label="每页正文字符" value={draft.capabilities?.max_web_content_chars} type="number" min={2000} max={30000} step={1000} onChange={(next) => update("capabilities", "max_web_content_chars", next)} /></div>
        <p className="notice warning">该权限仅允许脱敏本机观测、现有知识检索和公开网页 GET 读取。它不允许执行命令、修改文件、上传资料、登录网站、发送消息、结束进程或读取密钥。网页内容不能修改人物 JSON，也不能作为用户偏好证据。</p>
      </>}
    {tab === "rag" && <><h3>检索开关</h3><div className="toggle-grid"><Field label="启用 RAG" value={draft.retrieval.rag_enabled} type="checkbox" onChange={(next) => update("retrieval", "rag_enabled", next)} /><Field label="知识库召回" value={draft.retrieval.knowledge_enabled} type="checkbox" onChange={(next) => update("retrieval", "knowledge_enabled", next)} /><Field label="会话记忆召回" value={draft.retrieval.chat_enabled} type="checkbox" onChange={(next) => update("retrieval", "chat_enabled", next)} /><Field label="JSON 字段记忆" value={draft.retrieval.structured_memory_enabled} type="checkbox" onChange={(next) => update("retrieval", "structured_memory_enabled", next)} /><Field label="BM25+ 词法召回" value={draft.retrieval.bm25_enabled} type="checkbox" onChange={(next) => update("retrieval", "bm25_enabled", next)} /><Field label="向量召回" value={draft.retrieval.vector_enabled} type="checkbox" onChange={(next) => update("retrieval", "vector_enabled", next)} /><Field label="本地精排（需模型）" value={draft.retrieval.reranker_enabled} type="checkbox" onChange={(next) => update("retrieval", "reranker_enabled", next)} /><Field label="公平曝光保护" value={draft.retrieval.fairness_enabled} type="checkbox" onChange={(next) => update("retrieval", "fairness_enabled", next)} /><Field label="时间衰减" value={draft.retrieval.temporal_enabled} type="checkbox" onChange={(next) => update("retrieval", "temporal_enabled", next)} /></div><h3>召回参数</h3><div className="form-grid"><Field label="知识召回数" value={draft.retrieval.knowledge_k} type="number" onChange={(next) => update("retrieval", "knowledge_k", next)} /><Field label="记忆召回数" value={draft.retrieval.chat_k} type="number" onChange={(next) => update("retrieval", "chat_k", next)} /><Field label="相似度阈值" value={draft.retrieval.similarity_threshold} type="number" step={0.05} onChange={(next) => update("retrieval", "similarity_threshold", next)} /><Field label="RRF 常数" value={draft.retrieval.rrf_k} type="number" onChange={(next) => update("retrieval", "rrf_k", next)} /><Field label="候选放大倍数" value={draft.retrieval.candidate_multiplier} type="number" onChange={(next) => update("retrieval", "candidate_multiplier", next)} /><Field label="精排候选数" value={draft.retrieval.reranker_top_n} type="number" onChange={(next) => update("retrieval", "reranker_top_n", next)} /><Field label="轮次衰减" value={draft.retrieval.decay_rounds} type="number" onChange={(next) => update("retrieval", "decay_rounds", next)} /><Field label="低曝光保留比例" value={draft.retrieval.low_exposure_ratio} type="number" step={0.05} onChange={(next) => update("retrieval", "low_exposure_ratio", next)} /><Field label="同字段族上限" value={draft.retrieval.memory_family_limit} type="number" onChange={(next) => update("retrieval", "memory_family_limit", next)} /><Field label="饥饿保护轮次" value={draft.retrieval.starvation_rounds} type="number" onChange={(next) => update("retrieval", "starvation_rounds", next)} /></div><p className="notice">BM25+ 与向量先独立排序，再由 RRF 融合；Boost 有总上限。本地精排模型缺失时会安全退回 RRF，不会在线下载。无 JSON 标签文本只进入限额候选池。</p><h3>知识分块</h3><div className="form-grid"><Field label="子块长度" value={draft.knowledge.child_size} type="number" onChange={(next) => update("knowledge", "child_size", next)} /><Field label="父块长度" value={draft.knowledge.parent_size} type="number" onChange={(next) => update("knowledge", "parent_size", next)} /><Field label="重叠字符" value={draft.knowledge.overlap} type="number" onChange={(next) => update("knowledge", "overlap", next)} /></div></>}
    {tab === "protocol" && <><h3>生成与 JSON 写回</h3><div className="form-grid"><Field label="协议模式" value={draft.protocol.mode} onChange={(next) => update("protocol", "mode", next)} /><Field label="角色审计模型（留空复用主模型）" value={draft.llm.role_audit_model} onChange={(next) => update("llm", "role_audit_model", next)} /></div><div className="toggle-grid"><Field label="自动结构修复" value={draft.protocol.auto_repair} type="checkbox" onChange={(next) => update("protocol", "auto_repair", next)} /><Field label="显示写回诊断" value={draft.protocol.diagnostics} type="checkbox" onChange={(next) => update("protocol", "diagnostics", next)} /><Field label="复杂角色异步审计" value={draft.llm.role_audit_enabled} type="checkbox" onChange={(next) => update("llm", "role_audit_enabled", next)} /></div><p className="notice">回复立即流式展示；JSON 每轮最多写入三个经过路径、证据和 revision 校验的叶子 Patch。复杂角色审计只在本轮完成后运行，不能替换已显示或已朗读的内容，严重偏移只影响下一轮。</p></>}
    {tab === "audio" && <>
      <h3>语音合成</h3>
      <div className="form-grid"><SelectField label="TTS 链路" value={draft.audio.tts_provider} disabled={providerBusy} options={[["siliconflow", "SiliconFlow 云端流式 TTS（上线推荐）"], ["cosyvoice", "本地 CosyVoice（可选）"], ["gpt-sovits", "本地 GPT-SoVITS（独立可选）"]]} onChange={(next) => void switchTtsProvider(next)} /><Field label="速度" value={draft.audio.tts_speed} type="number" min={0.5} max={2} step={0.1} onChange={(next) => update("audio", "tts_speed", next)} /></div>
      <p className={`notice ${providerStatus.startsWith("切换失败") ? "warning" : ""}`}>{providerStatus}</p>
      {ttsProvider === "cosyvoice" && <><div className="form-grid"><Field label="CosyVoice Worker" value={draft.audio.tts_worker_url} onChange={(next) => update("audio", "tts_worker_url", next)} /><Field label="识别出的参考文本（请校对）" value={draft.audio.tts_reference_text} type="textarea" onChange={(next) => update("audio", "tts_reference_text", next)} placeholder="上传后自动识别；必须与参考音频实际说出的内容一致" /></div><p className="notice warning">本地 CosyVoice 是可选链路，上线安装包不包含其模型。参考文本必须与音频逐字匹配；回复中的（括号动作）只显示，不会朗读。</p><div className="reference-panel"><div><strong>本地参考音频</strong><small>{audioStatus}</small></div><div><label className="secondary upload-button">{audioBusy === "upload" ? "上传中…" : bool(draft.audio.tts_reference_configured) ? "替换音频" : "选择并上传"}<input hidden disabled={Boolean(audioBusy)} type="file" accept=".wav,.mp3,.flac,.m4a,.ogg,audio/*" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadReference(file); event.currentTarget.value = ""; }} /></label><button className="secondary" disabled={Boolean(audioBusy) || !bool(draft.audio.tts_reference_configured)} onClick={() => void recognizeReference()}>{audioBusy === "recognize" ? "识别中…" : "识别音频文字"}</button><button className="secondary" disabled={Boolean(audioBusy) || !bool(draft.audio.tts_reference_configured)} onClick={() => void clearReference()}>清除</button></div></div></>}
      {ttsProvider === "gpt-sovits" && <><div className="form-grid"><SelectField label="GPT-SoVITS 音色" value={draft.audio.tts_gpt_sovits_voice || gptVoices.active_voice} disabled={providerBusy} options={gptVoices.items.length ? gptVoices.items.map((voice) => [voice.id, `${voice.label}${voice.installed ? " · 已安装" : " · 需在启动器安装"}`] as [string, string]) : [["v4-changli", "V4-长离"], ["v4-yae-miko", "V4-八重神子"], ["v2proplus-kafka", "V2ProPlus-卡芙卡"]]} onChange={(next) => void switchGptVoice(next)} /><Field label="GPT-SoVITS Worker" value={draft.audio.tts_gpt_sovits_worker_url || "http://127.0.0.1:5055"} onChange={(next) => update("audio", "tts_gpt_sovits_worker_url", next)} /></div><p className="notice warning">音色模型与原 CosyVoice 完全分离，由启动器按需安装。V4 原生输出 48 kHz；卡芙卡实际为 V2ProPlus。第三方角色音色仅用于本地非商业验证，正式上线前必须取得对应权利方授权。</p></>}
      {ttsProvider === "siliconflow" && <p className="notice">云端 API 参数已集中到“模型与角色”。此处只选择链路与播放速度；逐句流式播放、首句抢跑、插话打断和括号过滤逻辑与本地链路一致。</p>}
      <div className="row-actions"><button className="primary" disabled={Boolean(audioBusy)} onClick={() => void testTts()}>{audioBusy === "test" ? "生成中…" : "生成并试听 TTS"}</button></div>
      <Field label="回复时逐句自动朗读" value={draft.audio.auto_tts} type="checkbox" onChange={(next) => update("audio", "auto_tts", next)} />
      <h3>实时识别与环境噪声</h3>
      <div className="toggle-grid"><Field label="根据环境自动调整噪声门" value={draft.audio.asr_adaptive_noise_enabled} type="checkbox" onChange={(next) => update("audio", "asr_adaptive_noise_enabled", next)} /><Field label="启用人物与 JSON 动态词表" value={draft.audio.asr_hotwords_enabled} type="checkbox" onChange={(next) => update("audio", "asr_hotwords_enabled", next)} /><Field label="含糊停顿动态断句" value={draft.audio.asr_dynamic_endpointing} type="checkbox" onChange={(next) => update("audio", "asr_dynamic_endpointing", next)} /><Field label="Nano 整句复核" value={draft.audio.asr_final_refinement_enabled} type="checkbox" onChange={(next) => update("audio", "asr_final_refinement_enabled", next)} /></div>
      <div className="form-grid"><Field label="ASR 提供方" value={draft.audio.asr_provider} onChange={(next) => update("audio", "asr_provider", next)} /><Field label="ASR 模型" value={draft.audio.asr_model} onChange={(next) => update("audio", "asr_model", next)} /><Field label="最低输入噪声门 dBFS" value={draft.audio.asr_noise_gate_db} type="number" min={-70} max={-20} step={1} onChange={(next) => update("audio", "asr_noise_gate_db", next)} /><Field label="环境校准毫秒" value={draft.audio.asr_noise_calibration_ms} type="number" min={500} max={5000} step={100} onChange={(next) => update("audio", "asr_noise_calibration_ms", next)} /><Field label="静音断句毫秒" value={draft.audio.asr_silence_ms} type="number" min={250} max={3000} onChange={(next) => update("audio", "asr_silence_ms", next)} /><Field label="多段话合并窗口毫秒" value={draft.audio.asr_utterance_merge_ms} type="number" min={300} max={3000} step={50} onChange={(next) => update("audio", "asr_utterance_merge_ms", next)} /></div><p className="notice">Paraformer 保持实时字幕；停顿尾部会在 400–900ms 间动态断句。安装 Fun-ASR Nano 后只在整句结束时低优先级复核，缺失、超时或播放回声场景自动回退，不影响现有识别。</p>
      <h3>语音情绪感知 · 实验性</h3>
      <div className="toggle-grid"><Field label="情绪侧链接口（暂时停用）" value={false} type="checkbox" onChange={() => undefined} /></div>
      <p className="advanced-note">情绪分析在本轮回复完成后后台执行，不再等待或延迟当前回复；完成后的状态仅供下一轮语音调整语气。</p>
      <p className="notice">当前版本不加载情绪模型，也不执行声学或文本情绪分析；仅保留后端接口，便于后续按需接入。</p>
      <h3>AI 播放完：短回复优先</h3><div className="form-grid"><Field label="监听最低门槛 dBFS" value={draft.audio.asr_listening_energy_threshold_db} type="number" min={-60} max={-15} step={1} onChange={(next) => update("audio", "asr_listening_energy_threshold_db", next)} /><Field label="高于噪声底 dB" value={draft.audio.asr_listening_noise_margin_db} type="number" min={4} max={24} step={1} onChange={(next) => update("audio", "asr_listening_noise_margin_db", next)} /><Field label="监听最短发声毫秒" value={draft.audio.asr_listening_min_speech_ms} type="number" min={60} max={1000} step={20} onChange={(next) => update("audio", "asr_listening_min_speech_ms", next)} /></div>
      <h3>AI 播放中：三重确认后打断</h3><div className="form-grid"><Field label="插话最低门槛 dBFS" value={draft.audio.asr_barge_in_energy_threshold_db} type="number" min={-60} max={-15} step={1} onChange={(next) => update("audio", "asr_barge_in_energy_threshold_db", next)} /><Field label="高于噪声底 dB" value={draft.audio.asr_barge_in_noise_margin_db} type="number" min={6} max={30} step={1} onChange={(next) => update("audio", "asr_barge_in_noise_margin_db", next)} /><Field label="插话最短发声毫秒" value={draft.audio.asr_barge_in_min_speech_ms} type="number" min={120} max={1500} step={20} onChange={(next) => update("audio", "asr_barge_in_min_speech_ms", next)} /><Field label="疑似声音释放毫秒" value={draft.audio.asr_candidate_release_ms} type="number" min={80} max={1000} step={20} onChange={(next) => update("audio", "asr_candidate_release_ms", next)} /></div>
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
      <div className="vocabulary-list">{(vocabulary?.entries || []).filter((item) => !vocabularyQuery.trim() || `${item.term} ${item.aliases.join(" ")} ${item.source_field} ${item.category}`.toLowerCase().includes(vocabularyQuery.trim().toLowerCase())).slice(0, 160).map((item) => <article key={item.id} className={!item.enabled ? "disabled" : ""}><div><strong>{item.term}</strong><span className={`priority ${item.priority}`}>{item.priority === "critical" ? "最高" : item.priority === "high" ? "高" : item.priority === "medium" ? "中" : "轻"}</span><small>{item.category} · {item.source === "manual" ? "个人" : item.source === "profile" ? "JSON 自动" : "系统"}</small>{item.aliases.length > 0 && <p>易错：{item.aliases.join("、")}</p>}{item.source_field && <p className="source-field">{item.source_field}</p>}</div>{item.source === "manual" ? <div className="vocabulary-actions"><button className="secondary" disabled={vocabularyBusy} onClick={() => void saveManualVocabulary((vocabulary?.entries || []).filter((entry) => entry.source === "manual").map((entry) => entry.id === item.id ? { ...entry, enabled: !entry.enabled } : entry))}>{item.enabled ? "停用" : "启用"}</button><button className="danger-text" disabled={vocabularyBusy} onClick={() => { if (window.confirm(`删除词条“${item.term}”？`)) void saveManualVocabulary((vocabulary?.entries || []).filter((entry) => entry.source === "manual" && entry.id !== item.id)); }}>删除</button></div> : <span className="read-only-badge">自动</span>}</article>)}</div>
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
  return <Modal title="全局知识库" kicker="KNOWLEDGE BASE" onClose={onClose}><div className="knowledge-layout"><section className="knowledge-compose"><h3>新增资料</h3><Field label="来源名称" value={source} onChange={(next) => setSource(str(next))} /><Field label="知识内容" value={text} type="textarea" onChange={(next) => setText(str(next))} placeholder="粘贴文本，空行会成为自然分块边界" /><div className="row-actions"><label className="upload-button">上传 TXT / MD / JSON<input hidden type="file" accept=".txt,.md,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.currentTarget.value = ""; }} /></label><button className="primary" disabled={!text.trim()} onClick={() => void add()}>保存知识</button></div></section><section className="knowledge-manage"><div className="manage-head"><h3>知识块 <b>{items.length}</b></h3><label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索内容或来源" /></label></div>{loading ? <div className="empty-mini">正在读取知识库…</div> : <div className="knowledge-list">{items.length ? items.map((item) => <article key={item.chunk_id}><header><span>{item.source}</span><button onClick={async () => { if (!window.confirm("删除这个知识块？")) return; await request(`/api/v1/knowledge/${item.chunk_id}`, { method: "DELETE" }); notify("知识块已删除"); await load(); }}>删除</button></header><p>{item.text}</p><small>{item.chunk_id} · {formatTime(item.created_at)}</small></article>) : <div className="empty-mini">知识库中暂无匹配内容</div>}</div>}</section></div></Modal>;
}

function MemoryDialog({ onClose, onDirty, notify }: { onClose: () => void; onDirty: (dirty: boolean) => void; notify: (message: string) => void }) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [includeHistory, setIncludeHistory] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [editingKey, setEditingKey] = useState("");
  const [draft, setDraft] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await request<{ items: MemoryItem[] }>(`/api/v1/memory/items?include_history=${includeHistory ? "true" : "false"}`);
      setItems(result.items);
    } catch (error) { notify((error as Error).message); }
    finally { setLoading(false); }
  }, [includeHistory, notify]);
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
    if (!window.confirm(`删除“${item.display_name}：${item.value}”？权威档案会同步更新。`)) return;
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
    return <label className="profile-form-field profile-form-list"><span>{label}</span><textarea aria-label={label} value={value.map(String).join("\n")} placeholder="每行一项；留空表示暂无记录" onChange={(event) => onChange(path, event.target.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean))} /></label>;
  }
  if (typeof value === "boolean") {
    return <label className="profile-form-field profile-form-check"><input aria-label={label} type="checkbox" checked={value} onChange={(event) => onChange(path, event.target.checked)} /><span>{label}</span></label>;
  }
  if (fieldKey === "gender" && path.includes("identity")) {
    return <label className="profile-form-field"><span>{label}</span><select aria-label={label} value={String(value)} onChange={(event) => onChange(path, event.target.value)}><option value="男">男</option><option value="女">女</option></select><small>用户手动保存后作为模型最高优先级身份；AI 不能自行改写。</small></label>;
  }
  return <label className="profile-form-field"><span>{label}</span><input aria-label={label} type={typeof value === "number" ? "number" : "text"} value={value == null ? "" : String(value)} onChange={(event) => onChange(path, typeof value === "number" ? Number(event.target.value) : event.target.value)} /></label>;
}

function ProfileDialog({ initialName, onClose, onDirty, notify }: { initialName: Role | "state"; onClose: () => void; onDirty: (dirty: boolean) => void; notify: (message: string) => void }) {
  const [name, setName] = useState(initialName); const [document, setDocument] = useState(""); const [savedDocument, setSavedDocument] = useState(""); const [history, setHistory] = useState<ProfileHistoryItem[]>([]); const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [mode, setMode] = useState<"form" | "json">("form"); const [error, setError] = useState("");
  const parsed = useMemo(() => { try { const value = JSON.parse(document); return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null; } catch { return null; } }, [document]);
  const load = useCallback(async () => { setLoading(true); setError(""); try { const [value, versions] = await Promise.all([request<Record<string, unknown>>(`/api/v1/profiles/${name}`), request<{ items: ProfileHistoryItem[] }>(`/api/v1/profiles/${name}/history`).catch(() => ({ items: [] }))]); const serialized = JSON.stringify(value, null, 2); setDocument(serialized); setSavedDocument(serialized); setHistory(versions.items); } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); } finally { setLoading(false); } }, [name, notify]);
  useEffect(() => { void load(); }, [load]); useEffect(() => { onDirty(document !== savedDocument); return () => onDirty(false); }, [document, onDirty, savedDocument]);
  const updateValue = useCallback((path: string[], value: unknown) => { if (!parsed) return; const next = structuredClone(parsed); let cursor: Record<string, unknown> = next; path.slice(0, -1).forEach((key) => { cursor = cursor[key] as Record<string, unknown>; }); cursor[path[path.length - 1]] = value; setDocument(JSON.stringify(next, null, 2)); setError(""); }, [parsed]);
  const save = async () => { if (!parsed) { setError("JSON 格式无效，请修正后再保存。"); return; } setSaving(true); setError(""); try { const result = await request<{ document: Record<string, unknown> }>(`/api/v1/profiles/${name}`, { method: "PUT", body: JSON.stringify(parsed) }); const serialized = JSON.stringify(result.document, null, 2); setDocument(serialized); setSavedDocument(serialized); notify("档案已保存，后续对话将使用新版本"); } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); } finally { setSaving(false); } };
  const restorePrevious = async () => { const previous = history[0]; if (!previous || !parsed) return; if (!window.confirm(`恢复修订 ${previous.revision} 的内容？当前版本仍会保留在历史中。`)) return; setSaving(true); setError(""); try { const result = await request<{ document: Record<string, unknown> }>(`/api/v1/profiles/${name}/restore`, { method: "POST", body: JSON.stringify({ version_id: previous.version_id, expected_revision: parsed.revision }) }); const serialized = JSON.stringify(result.document, null, 2); setDocument(serialized); setSavedDocument(serialized); notify("已恢复上一版本，并生成新的修订"); await load(); } catch (reason) { const message = (reason as Error).message; setError(message); notify(message); } finally { setSaving(false); } };
  const switchProfile = (id: Role | "state") => { if (document !== savedDocument && !window.confirm("切换会放弃未保存修改，是否继续？")) return; setName(id); };
  return <Modal title="人物与状态档案" kicker="PROFILE DOCUMENTS" onClose={onClose} footer={<><button className="secondary" disabled={loading || saving || !history.length} onClick={() => void restorePrevious()}>恢复上一版本</button><button className="secondary" disabled={loading || saving} onClick={() => void load()}>放弃修改并重载</button><button className="primary" disabled={loading || saving || !parsed || document === savedDocument} onClick={() => void save()}>{saving ? "正在保存…" : "保存档案"}</button></>}><div className="profile-tabs">{([["user", "用户档案"], ["assistant", "AI 档案"], ["state", "运行状态"]] as Array<[Role | "state", string]>).map(([id, label]) => <button className={name === id ? "active" : ""} key={id} onClick={() => switchProfile(id)}>{label}</button>)}</div><div className="profile-editor-toolbar"><p className="advanced-note">用户修改直接生效并生成新 revision；AI 后续写回必须基于该 revision。当前保留 {history.length} 个可恢复版本。</p><div><button className={mode === "form" ? "active" : ""} onClick={() => setMode("form")}>表单编辑</button><button className={mode === "json" ? "active" : ""} onClick={() => setMode("json")}>高级 JSON</button></div></div>{error && <div className="profile-editor-error" role="alert">{error}</div>}{loading ? <div className="empty-mini">正在载入档案…</div> : mode === "json" ? <textarea aria-label="高级 JSON 编辑器" className="json-editor" value={document} onChange={(event) => { setDocument(event.target.value); setError(""); }} spellCheck={false} /> : parsed ? <div className="profile-form">{Object.entries(parsed).filter(([key]) => !PROFILE_TECHNICAL_FIELDS.has(key)).map(([key, value]) => <ProfileFieldEditor key={key} fieldKey={key} value={value} path={[key]} onChange={updateValue} />)}</div> : <div className="profile-editor-error" role="alert">JSON 格式无效，请切换到高级 JSON 修正。</div>}</Modal>;
}

function ProfileCardDialog({ role, avatars, displayName, onClose, onEdit }: { role: Role; avatars: AvatarConfig; displayName: string; onClose: () => void; onEdit: (role: Role) => void }) {
  const [card, setCard] = useState<ProfileCardData | null>(null); const [error, setError] = useState("");
  useEffect(() => { request<ProfileCardData>(`/api/v1/profiles/${role}/card`).then(setCard).catch((reason: Error) => setError(reason.message)); }, [role]);
  const blocks: [string, Record<string, unknown>][] = card ? [["身份信息", card.identity], ["人物性格", card.personality], ["近期关系", card.relationship]] : [];
  return <Modal title={`${displayName} · 人物卡`} kicker="CHARACTER PROFILE" onClose={onClose} compact footer={<button className="primary" onClick={() => onEdit(role)}>编辑这份档案</button>}><div className="profile-card-hero"><PortraitAvatar role={role} avatars={avatars} label={displayName} /><div><h3>{displayName}</h3><p>{role === "assistant" ? "AI 角色设定与当前关系状态" : "用户设定与偏好"}</p></div></div>{error ? <div className="profile-card-empty">{error}</div> : !card ? <div className="profile-card-empty">正在读取人物关键字段…</div> : <div className="profile-card-blocks">{blocks.map(([title, value]) => <section className="profile-card-block" key={title}><h3>{title}</h3>{Object.keys(value).length ? Object.entries(value).map(([key, item]) => <div className="profile-card-row" key={key}><span>{key}</span><strong>{friendlyValue(item)}</strong></div>) : <div className="profile-card-empty">暂无记录</div>}</section>)}<small className="profile-revision">修订 {card.revision} · {formatTime(card.updated_at)}</small></div>}</Modal>;
}

function DiagnosticsDialog({ onClose, notify, onCleared }: { onClose: () => void; notify: (message: string) => void; onCleared: () => void }) {
  const [report, setReport] = useState<DiagnosticReport | null>(null); const [loading, setLoading] = useState(true);
  const load = useCallback(() => { setLoading(true); request<DiagnosticReport>("/api/v1/diagnostics").then(setReport).catch((error: Error) => notify(error.message)).finally(() => setLoading(false)); }, [notify]);
  useEffect(() => { load(); }, [load]);
  const clear = async (scope: "knowledge" | "sessions" | "all") => { const phrase = { knowledge: "CLEAR KNOWLEDGE", sessions: "CLEAR SESSIONS", all: "CLEAR ALL" }[scope]; if (!window.confirm(`危险操作：${phrase}，是否继续？`)) return; await request("/api/v1/data/clear", { method: "POST", body: JSON.stringify({ scope, confirmation: phrase }) }); notify("数据清理完成"); onCleared(); load(); };
  return <Modal title="系统诊断与数据管理" kicker="SYSTEM HEALTH" onClose={onClose}>{loading ? <div className="empty-mini">正在检查服务状态…</div> : <><div className="diagnostic-grid"><article><span>主服务</span><strong>{report?.ok ? "正常" : "异常"}</strong><small>{str(report?.app.version)}</small></article><article><span>会话</span><strong>{num(report?.counts.sessions)}</strong><small>SQLite 权威存储 · JSON 投影</small></article><article><span>知识块</span><strong>{num(report?.counts.chunks)}</strong><small>{num(report?.counts.characters)} 字符</small></article><article><span>语音</span><strong>{bool(report?.audio.asr_ready) ? "ASR 就绪" : "ASR 降级"}</strong><small>{str(report?.audio.asr_provider)}</small></article></div><details className="report-json"><summary>查看完整诊断报告</summary><pre>{JSON.stringify(report, null, 2)}</pre></details><section className="danger-zone"><h3>危险数据操作</h3><p>这些操作只影响当前新项目的 runtime，不会修改原 Mindscape 数据。</p><div><button onClick={() => void clear("knowledge")}>清空知识库</button><button onClick={() => void clear("sessions")}>清空会话</button><button className="danger" onClick={() => void clear("all")}>清空全部运行数据</button></div></section></>}</Modal>;
}

function VoiceMode({ state, avatar, characterName, context, companion, onExit, onRetry }: { state: VoiceSessionState; avatar: AvatarEntry; characterName: string; context: VoiceInteractionContext; companion: { enabled: boolean; round: number; limit: number }; onExit: () => void; onRetry: () => void }) {
  const intensity = Math.max(0.08, state.level);
  const faceToFace = context.mode === "face_to_face";
  return <section className={`voice-mode phase-${state.phase}`} style={{ "--voice-level": intensity, "--voice-avatar": `url("${avatar.src}")` } as CSSProperties} aria-label="沉浸式实时语音"><div className="voice-background" /><div className="voice-shade" /><button className="voice-exit" onClick={onExit}>退出语音</button><div className="voice-stage"><span className="voice-kicker">{faceToFace ? "FACE TO FACE" : "LIVE CONVERSATION"}</span>{faceToFace && <div className="voice-scene-chip" title={context.scene || "普通面对面场景"}><span>面对面</span><small>{context.scene || "未指定具体场景"}</small></div>}{companion.enabled && <div className={`voice-companion ${companion.round >= companion.limit ? "complete" : ""}`} role="status"><span>连续陪伴</span><strong>{companion.round} / {companion.limit}</strong><small>{companion.round >= companion.limit ? "已到本次上限" : "朗读结束 10 秒后继续 · 可随时插话"}</small></div>}<div className="voice-portrait-shell"><i className="voice-ring ring-one" /><i className="voice-ring ring-two" /><div className="voice-portrait portrait-avatar" style={avatarStyle(avatar)}><img src={avatar.src} alt={`${characterName}头像`} /></div></div><h1>{characterName}</h1><div className="voice-status"><i />{VOICE_LABELS[state.phase]}</div><div className="voice-wave" aria-hidden="true">{Array.from({ length: 18 }, (_, index) => <i key={index} style={{ "--bar": (index % 5) + 1 } as CSSProperties} />)}</div><div className="voice-caption"><small>{state.reply ? `${characterName} 正在回应` : "你刚刚说"}</small><p>{state.reply || state.transcript || (state.phase === "error" ? state.error : "直接开始说话，我会自动识别、发送并回应。")}</p></div>{state.phase === "error" && <div className="voice-error"><span>{state.error}</span><button onClick={onRetry}>重新连接</button></div>}<span className="voice-tip">连续说话确认后才会打断 · 插话会重定向话题 · Ctrl+Shift+M 切换 · Esc 退出</span></div></section>;
}

export default App;

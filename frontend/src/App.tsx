import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { useApplicationData, useAppNavigation, useModalCoordinator } from "./app/index";
import { useAsrReadiness, useTtsRuntime, useVoiceSessionRuntime, VoiceMode } from "./features/voice";
import type {
  TtsRuntimeCallbacks,
  VoiceInteractionMode,
  VoiceSessionRuntimeCallbacks,
} from "./features/voice";
import { ProfileDialog } from "./features/profile";
import { MemoryDialog } from "./features/memory";
import { KnowledgeDialog } from "./features/knowledge";
import { asRecord, bool, formatTime, num, str } from "./shared/formatters";
import {
  CharacterLibrary,
  CharacterPicker,
  ModeLobby,
  ProfileCardDialog,
  useCharacterDirectory,
} from "./features/characters";
import type { AvatarConfig, CharacterRecord, CharacterSummary, Role } from "./features/characters";
import {
  ChatWorkspace,
  clearPersistedChatRun,
  createModelAttemptInspectorEvent,
  createModelSummaryInspectorEvent,
  ExecutionInspector,
  getProviderToolCapability,
  getPublicRunError,
  restoreSessionMessages,
  useChatRuntime,
  useConversationMaintenance,
  useSessionDirectory,
  useTurnComposer,
} from "./features/chat";
import type {
  ChatRuntimeCallbacks,
  InspectorTab,
  InitiativeTrigger,
  Message,
  StreamEnvelope,
  TurnComposerEffects,
  TurnSend,
} from "./features/chat";
import { DrawWorkshop } from "./features/destiny";
import {
  DiagnosticsDialog,
  Field,
  Modal,
  SettingsWorkspace,
  useModelSelection,
  useSettingsSynchronization,
} from "./features/settings";
import { ScenePickerPage, sceneAssetPath, useConversationScene } from "./features/scenes";

// A recovered SSE stream is historical UI state. It must never become a new
// audio job: restoring a page must not make the companion read an old answer.
export function shouldSynthesizeStreamEvent(isRecoveryReplay: boolean, processRecovery = false): boolean {
  return !isRecoveryReplay && !processRecovery;
}

const ADULT_MODE_STORAGE_KEY = "mindspace.r18_enhanced";
const NSFW_ADULT_CONFIRMED_STORAGE_KEY = "mindspace.nsfw_adult_confirmed";
const R18_STYLE_STORAGE_KEY = "mindspace.r18_style";
const VOICE_RECONNECT_DELAYS_MS = [250, 750, 1500, 3000] as const;

// Qwen receives one complete reply per turn. Its CustomVoice sampling context
// is therefore never reset at sentence boundaries.
export {
  alignPCM16Chunk,
  asrClientDisposition,
  companionContinuationPlan,
  shouldAutomaticallyQueueSpeech,
  shouldBufferQwenReplyForSinglePass,
  shouldIgnoreASREvent,
  shouldRetryMicrophoneStartup,
  shouldSkipSpeechSegmentFailure,
  voiceMergeDelay,
  voiceReconnectDelay,
} from "./features/voice";

export function shouldFollowConversationScroll(distanceFromBottom: number, threshold = 180): boolean {
  return Number.isFinite(distanceFromBottom)
    && distanceFromBottom <= Math.max(0, threshold);
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
  const { avatars, initialDataLoaded, initialize, setAvatars, setSettings, settings } = useApplicationData();
  const { characters, loadCharacters } = useCharacterDirectory();
  const [activeCharacterId, setActiveCharacterId] = useState("");
  const { conversationScene, loadConversationScene, setConversationScene } = useConversationScene();
  const {
    characterPickerIntent,
    characterPickerOpen,
    closeCharacterPicker,
    createSessionRecord,
    deleteSessionRecord,
    filteredSessions,
    findResumeSession,
    interruptedSession,
    loadSessions,
    newSession,
    openSessionRecord,
    requestResumeSession,
    search,
    selectPreferredSession,
    sessionId,
    sessions,
    setSearch,
    title,
  } = useSessionDirectory();
  const { appView, navigate } = useAppNavigation();
  const [settingsInitialTab, setSettingsInitialTab] = useState("model");
  const [adultMode, setAdultMode] = useState(
    () => localStorage.getItem(ADULT_MODE_STORAGE_KEY) === "1"
      && localStorage.getItem(NSFW_ADULT_CONFIRMED_STORAGE_KEY) === "1",
  );
  const [nsfwConfirmationOpen, setNsfwConfirmationOpen] = useState(false);
  const [nsfwConfirmationSeconds, setNsfwConfirmationSeconds] = useState(3);
  const [r18StyleId, setR18StyleId] = useState(
    () => localStorage.getItem(R18_STYLE_STORAGE_KEY) || "high_intensity",
  );
  const chatRuntimeCallbacksRef = useRef<ChatRuntimeCallbacks>({
    handleStreamEvent: () => undefined,
    notify: () => undefined,
    onBeforeRecovery: () => undefined,
    onConversationJump: () => undefined,
    onCancelEffects: () => undefined,
    onRequestFailure: () => undefined,
  });
  const {
    abortRef,
    addEvent,
    cancelRun,
    completeRegeneration,
    events,
    executeTurn,
    generating,
    generatingRef,
    inspectionRunId,
    messages,
    retrieval,
    round,
    roundRef,
    runId,
    runIdRef,
    setEvents,
    setGenerating,
    setInspectionRunId,
    setMessages,
    setRetrieval,
    setRound,
    setRunId,
    stageRegeneration,
  } = useChatRuntime({
    sessionId,
    initialDataLoaded,
    callbacksRef: chatRuntimeCallbacksRef,
  });
  const conversationRef = useRef<HTMLDivElement | null>(null);
  const conversationTailRef = useRef<HTMLDivElement | null>(null);
  const followConversationRef = useRef(true);
  const pendingConversationJumpRef = useRef(false);
  const {
    closeModal,
    modal,
    modalDirty,
    openModal,
    profileCardRole,
    profileEditorRole,
    setModal,
    setModalDirty,
    setProfileCardRole,
    setProfileEditorRole,
  } = useModalCoordinator();
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("flow");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [toast, setToast] = useState("");
  const activeInitiativeRef = useRef<{ trigger: InitiativeTrigger; sequence: number }>({ trigger: "none", sequence: 0 });
  const voiceReplyRef = useRef("");
  const currentAssistantIdRef = useRef("");
  const lastVoiceRunIdRef = useRef("");
  const pendingResponseDeltaRef = useRef("");
  const responseFrameRef = useRef<number | null>(null);  const turnComposerEffectsRef = useRef<TurnComposerEffects>({
    onMissingCharacter: () => undefined,
    onMissingLlm: () => undefined,
    cancelIdleContinuation: () => undefined,
    resetIdleContinuation: () => undefined,
    isVoiceOpen: () => false,
    isAudioPlaying: () => false,
    captureVoiceInterruption: () => undefined,
    stopAudio: () => undefined,
    resetResponseState: () => undefined,
    markVoiceThinking: () => undefined,
    getPendingASREvidence: () => null,
    clearPendingASREvidence: () => undefined,
    getVoiceDelivery: () => null,
    getVoiceContext: () => null,
    clearVoiceDelivery: () => undefined,
    setActiveInitiative: () => undefined,
  });
  const ttsRuntimeCallbacksRef = useRef<TtsRuntimeCallbacks>({
    isVoiceOpen: () => false,
    isGenerating: () => false,
    getRunId: () => "",
    getVisibleReply: () => "",
    getLastVoiceRunId: () => "",
    getAssistantMessageId: () => "",
    publishPlaybackState: () => undefined,
    updateVoice: () => undefined,
    setVoiceInputLocked: () => undefined,
    onPlaybackComplete: () => undefined,
    notify: () => undefined,
  });
  const voiceSessionRuntimeCallbacksRef = useRef<VoiceSessionRuntimeCallbacks>({
    notify: () => undefined,
    getInput: () => "",
    setInput: () => undefined,
    setMessages: () => undefined,
    cancelRun: async () => undefined,
    getRunId: () => "",
    getRound: () => 1,
    isGenerating: () => false,
    sendMessage: async () => undefined,
    openVoiceEntryModal: () => undefined,
    closeVoiceEntryModal: () => undefined,
    setModalDirty: () => undefined,
    onSettingsSaved: () => undefined,
  });  const sendMessageRef = useRef<TurnSend | null>(null);
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

  const ttsRuntime = useTtsRuntime({ settings, callbacksRef: ttsRuntimeCallbacksRef });
  const asrReady = useAsrReadiness(initialDataLoaded, settings);
  const { availableModels, chooseModel, loadAvailableModels, modelsLoading } = useModelSelection({
    notify,
    setSettings,
  });
  useSettingsSynchronization({
    enabled: initialDataLoaded,
    paused: modal === "settings" && modalDirty,
    setSettings,
  });

  const {
    acceptSpeechDelta,
    captureVoiceInterruption,
    clearVoiceDelivery,
    closePlaybackContext,
    enqueueSpeech,
    getVoiceCue,
    getVoiceDelivery,
    hasQueuedAudio,
    hasSubmittedQwenReply,
    isAudioPlaying,
    markQwenReplySubmitted,
    playbackAudioContext,
    resetResponseState: resetTtsResponseState,
    resetSpeechSegmentation,
    setPlaybackDucked,
    setVoiceCue,
    shouldBufferQwenReply,
    speak: speakWithTts,
    stopAudio,
  } = ttsRuntime;

  const {
    addInteraction,
    cancelRegenerationDraft,
    customInteraction,
    handleAttachmentFiles,
    hasPayload: composerHasPayload,
    input,
    inputRef,
    interactionBranch,
    interactionOpen,
    openInteractionForMessage,
    pendingAttachments,
    pendingInteractions,
    regenerateMessage,
    regenerationDraft,
    removeAttachment,
    removeInteraction,
    replyTarget,
    sendMessage,
    sendRegenerationDraft,
    setCustomInteraction,
    setInput,
    setInteractionBranch,
    setInteractionOpen,
    setReplyTarget,
  } = useTurnComposer({
    character: { activeCharacterId, activeCharacter },
    settings: { value: settings, llmReady, adultMode, r18StyleId },
    session: { sessionId },
    runtime: {
      cancelRun,
      completeRegeneration,
      executeTurn,
      generating,
      messages,
      round,
      setEvents,
      setRetrieval,
      stageRegeneration,
    },
    voice: { effectsRef: turnComposerEffectsRef },
    notify,
  });

  const {
    cancelIdleContinuation,
    clearActiveVoiceTurn,
    clearPendingASREvidence,
    closeCaptureContext,
    closeVoiceEntry,
    companionRound,
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
  } = useVoiceSessionRuntime({
    settings,
    input,
    generating,
    messages,
    callbacksRef: voiceSessionRuntimeCallbacksRef,
    tts: ttsRuntime,
  });

  voiceSessionRuntimeCallbacksRef.current = {
    notify,
    getInput: () => input,
    setInput,
    setMessages,
    cancelRun,
    getRunId: () => runIdRef.current,
    getRound: () => roundRef.current,
    isGenerating: () => generatingRef.current,
    sendMessage,
    openVoiceEntryModal: () => {
      setModalDirty(false);
      setModal("voice-entry");
    },
    closeVoiceEntryModal: () => {
      setModalDirty(false);
      setModal(null);
    },
    setModalDirty,
    onSettingsSaved: setSettings,
  };

  const openSession = useCallback(async (id: string) => {
    resetConversationVoiceState();
    clearVoiceDelivery();
    if (generating) await cancelRun();
    const { value } = await openSessionRecord(id);
    setActiveCharacterId(value.character_id || value.character?.character_id || "");
    await loadConversationScene(id);
    setMessages(restoreSessionMessages(id, value.messages || []));
    const highestRound = (value.messages || []).reduce(
      (maximum, message) => Math.max(maximum, message.round || 0),
      0,
    );
    setRound(Math.max(1, highestRound + 1));
    setEvents([]);
    setRetrieval([]);
    setSidebarOpen(false);
    navigate("chat", id);
  }, [cancelRun, clearVoiceDelivery, generating, loadConversationScene, navigate, openSessionRecord, resetConversationVoiceState, setEvents, setMessages, setRetrieval, setRound]);

  useEffect(() => {
    void initialize({
      loadSessions,
      loadCharacters,
      selectPreferredSession,
      openSession,
      onNoSession: (loadedCharacters) => {
        const firstActive = loadedCharacters.find((item) => item.status === "active");
        setActiveCharacterId(firstActive?.character_id || "");
        navigate(loadedCharacters.length ? "modes" : "modes");
      },
    }).catch((error) => notify((error as Error).message));
  }, [initialize, loadCharacters, loadSessions, navigate, notify, openSession, selectPreferredSession]);
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

  ttsRuntimeCallbacksRef.current = {
    isVoiceOpen,
    isGenerating: () => generatingRef.current,
    getRunId: () => runIdRef.current,
    getVisibleReply: () => voiceReplyRef.current,
    getLastVoiceRunId: () => lastVoiceRunIdRef.current,
    getAssistantMessageId: () => currentAssistantIdRef.current,
    publishPlaybackState: publishTtsPlaybackState,
    updateVoice,
    setVoiceInputLocked,
    onPlaybackComplete: handleTtsPlaybackComplete,
    notify,
  };

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

  const flushResponseDelta = useCallback(() => {
    // 一帧最多触发一次 React 状态更新；provider token 全部保留，只合并渲染。
    responseFrameRef.current = null;
    const delta = pendingResponseDeltaRef.current;
    pendingResponseDeltaRef.current = "";
    if (!delta) return;
    setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content: item.content + delta } : item));
    if (isVoiceOpen()) {
      updateVoice((current) => ({ ...current, reply: current.reply + delta, phase: isAudioPlaying() ? "assistant-speaking" : "thinking" }));
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

  const handleStreamEvent = useCallback((event: StreamEnvelope, isRecoveryReplay = false) => {
    const data = asRecord(event.data);
    if (event.event === "run.accepted") {
      runIdRef.current = event.run_id;
      setInspectionRunId(event.run_id);
      if (isVoiceOpen()) lastVoiceRunIdRef.current = event.run_id;
      setRunId(event.run_id);
      // ASR activation belongs to the voice intent created when the call is
      // opened.  Re-sending `start` here used to reset an active recognizer
      // halfway through a streamed response.
      if (isVoiceOpen()) updateVoice((current) => ({ ...current, phase: "thinking", reply: "", error: "" }));
    } else if (event.event === "node.started") {
      addEvent({ event: str(data.node), label: str(data.label || data.node), timestamp: event.timestamp, state: "active" });
    } else if (event.event === "node.completed") {
      setEvents((items) => items.map((item) => item.event === str(data.node) && item.state === "active" ? { ...item, state: data.error ? "error" : "done" } : item));
    } else if (event.event === "response.delta") {
      const delta = str(data.delta);
      if (isVoiceOpen()) voiceReplyRef.current += delta;
      if (
        shouldSynthesizeStreamEvent(isRecoveryReplay)
        && !shouldBufferQwenReply()
      ) {
        acceptSpeechDelta(delta);
      }
      scheduleResponseDelta(delta);
    } else if (event.event === "response.ready") {
      const content = str(data.content);
      const cue = str(data.voice_cue || getVoiceCue()).toLowerCase();
      if (
        content
        && shouldSynthesizeStreamEvent(isRecoveryReplay)
        && shouldBufferQwenReply()
        && !hasSubmittedQwenReply()
      ) {
        setVoiceCue(cue);
        resetSpeechSegmentation();
        markQwenReplySubmitted();
        enqueueSpeech(content);
      }
    } else if (event.event === "response.voice_cue") {
      const cue = str(data.cue).toLowerCase();
      setVoiceCue(cue);
      setMessages((items) => items.map((item) => item.status === "streaming"
        ? { ...item, voice_cue: getVoiceCue() }
        : item));
    } else if (event.event === "model.attempt") {
      addEvent(createModelAttemptInspectorEvent(data, event.timestamp, event.seq));
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
      if (isVoiceOpen()) voiceReplyRef.current = content;
      resetSpeechSegmentation();
      if (
        shouldSynthesizeStreamEvent(isRecoveryReplay, processRecovery)
        && !shouldBufferQwenReply()
      ) {
        acceptSpeechDelta(content, true);
      }
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content } : item));
      if (isVoiceOpen()) updateVoice((current) => ({ ...current, reply: content }));
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
        recordCompanionRound(completedInitiative.sequence);
      }
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      currentAssistantIdRef.current = str(response.assistant_message_id);
      const completedPresentation = str(response.presentation_mode);
      const modelSummary = createModelSummaryInspectorEvent(response.model, num(response.llm_call_count), event.timestamp);
      setEvents((items) => [...items.filter((item) => item.event !== "model.summary"), modelSummary]);
      if (shouldSynthesizeStreamEvent(isRecoveryReplay)) {
        if (shouldBufferQwenReply()) {
          if (!hasSubmittedQwenReply()) {
            markQwenReplySubmitted();
            enqueueSpeech(str(response.reply));
          }
        } else {
          acceptSpeechDelta("", true);
        }
      }
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, message_id: str(response.assistant_message_id) || item.message_id, content: str(response.reply || item.content), presentation_mode: completedPresentation === "scene" ? "scene" : "dialogue", status: "complete" as const } : item));
      if (isVoiceOpen()) updateVoice((current) => ({ ...current, reply: str(response.reply || current.reply), phase: isAudioPlaying() || hasQueuedAudio() ? "assistant-speaking" : "listening" }));
      clearActiveVoiceTurn();
      setRound((value) => value + 1);
      setGenerating(false);
      runIdRef.current = "";
      setRunId("");
      clearPersistedChatRun(event.run_id);
      if (isVoiceOpen() && !isAudioPlaying() && !hasQueuedAudio()) {
        setVoiceInputLocked(false, "turn_completed_without_audio");
      }
      void loadSessions();
      if (!isVoiceOpen()) scheduleIdleContinuation("text");
    } else if (event.event === "run.cancelled") {
      flushResponseDelta();
      stopAudio();
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, status: "cancelled" as const } : item));
      setGenerating(false);
      runIdRef.current = "";
      setRunId("");
      clearPersistedChatRun(event.run_id);
      setVoiceInputLocked(false, "run_cancelled_event");
      if (isVoiceOpen()) updateVoice((current) => ({ ...current, phase: "listening", level: 0, error: "" }));
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
      clearPersistedChatRun(event.run_id);
      setVoiceInputLocked(false, "run_interrupted");
      if (isVoiceOpen()) {
        updateVoice((current) => ({
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
      const modelSummary = createModelSummaryInspectorEvent(response.model || data.model, num(response.llm_call_count), event.timestamp, true);
      setEvents((items) => [...items.filter((item) => item.event !== "model.summary"), modelSummary]);
      const internalErrors = Array.isArray(response.errors) ? response.errors.join("；") : data.error;
      const errors = getPublicRunError(internalErrors, response.error_code || data.error_code);
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content: errors || "生成失败", status: "error" as const } : item));
      setGenerating(false);
      runIdRef.current = "";
      setRunId("");
      clearPersistedChatRun(event.run_id);
      setVoiceInputLocked(false, "run_failed");
      if (isVoiceOpen()) {
        notify(errors || "生成失败，语音识别仍在监听");
        updateVoice((current) => ({
          ...current,
          phase: isAudioPlaying() ? "assistant-speaking" : "listening",
          error: errors || "生成失败",
          level: 0,
        }));
      }
      clearActiveVoiceTurn();
    }
  }, [acceptSpeechDelta, addEvent, clearPendingResponseDelta, enqueueSpeech, flushResponseDelta, loadSessions, notify, scheduleIdleContinuation, scheduleResponseDelta, setVoiceInputLocked, settings, stopAudio]);

  chatRuntimeCallbacksRef.current = {
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
      if (isVoiceOpen()) updateVoice((current) => ({ ...current, phase: "listening", reply: "", level: 0, error: "" }));
    },
    onRequestFailure: (error) => {
      activeInitiativeRef.current = { trigger: "none", sequence: 0 };
      setVoiceInputLocked(false, "request_failed");
      if (isVoiceOpen()) {
        updateVoice((current) => ({ ...current, phase: isAudioPlaying() ? "assistant-speaking" : "listening", error: error.message, level: 0 }));
      }
    },
  };
  turnComposerEffectsRef.current = {
    onMissingCharacter: requestResumeSession,
    onMissingLlm: () => {
      setSettingsInitialTab("model");
      setModalDirty(false);
      setModal("settings");
    },
    cancelIdleContinuation,
    resetIdleContinuation,
    isVoiceOpen,
    isAudioPlaying: () => isAudioPlaying(),
    captureVoiceInterruption,
    stopAudio,
    resetResponseState: () => {
      // Do not lock ASR while the LLM is thinking. A committed utterance is
      // already protected by text deduplication, and any genuinely new utterance
      // must remain able to cancel/replace a slow or failed generation.
      resetTtsResponseState();
      clearPendingResponseDelta();
      voiceReplyRef.current = "";
      currentAssistantIdRef.current = "";
    },
    markVoiceThinking,
    getPendingASREvidence,
    clearPendingASREvidence,
    getVoiceDelivery,
    getVoiceContext,
    clearVoiceDelivery,
    setActiveInitiative: (trigger, sequence) => {
      activeInitiativeRef.current = { trigger, sequence };
    },
  };

  useEffect(() => { sendMessageRef.current = sendMessage; }, [sendMessage]);

  const startNewSessionForCharacter = useCallback(async (character: CharacterSummary | CharacterRecord) => {
    resetConversationVoiceState();
    clearVoiceDelivery();
    if (generating) await cancelRun();
    const id = await createSessionRecord(character);
    setActiveCharacterId(character.character_id);
    await loadConversationScene(id);
    setMessages([]); setRound(1); setEvents([]); setRetrieval([]); setSidebarOpen(false);
    closeCharacterPicker();
    navigate("chat", id);
    await Promise.all([loadSessions(), loadCharacters()]);
    notify("已创建新对话");
  }, [cancelIdleContinuation, cancelRun, closeCharacterPicker, createSessionRecord, generating, loadCharacters, loadConversationScene, loadSessions, navigate, notify]);

  const resumeCharacterSession = useCallback(async (character: CharacterSummary | CharacterRecord) => {
    const existing = findResumeSession(character);
    if (existing) {
      closeCharacterPicker();
      await openSession(existing);
      return;
    }
    await startNewSessionForCharacter(character);
  }, [closeCharacterPicker, findResumeSession, openSession, startNewSessionForCharacter]);

  const deleteSession = async (id: string) => {
    try {
      const outcome = await deleteSessionRecord(id, loadCharacters);
      if (!outcome) return;
      if (outcome.deletedCurrentSession) {
        if (outcome.nextSessionId) await openSession(outcome.nextSessionId);
        else {
          setMessages([]);
          setRound(1);
          setEvents([]);
          setRetrieval([]);
          setConversationScene(null);
          const characterStillExists = outcome.refreshedCharacters.some(
            (item) => item.character_id === outcome.deletedCharacterId && item.status === "active",
          );
          if (characterStillExists) {
            setActiveCharacterId(outcome.deletedCharacterId);
            navigate("characters");
          } else {
            setActiveCharacterId("");
            navigate("modes");
          }
        }
      }
      notify("会话已删除");
    } catch (error) {
      notify((error as Error).message);
    }
  };
  const { clearCurrent, deleteReply } = useConversationMaintenance({
    loadSessions,
    messages,
    notify,
    sessionId,
    setMessages,
    setRound,
  });

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
  const showFlow = () => { setInspectorTab("flow"); setInspectorOpen(true); };
  const showContext = () => { setInspectorTab("context"); setInspectorOpen(true); };

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const ctrl = event.ctrlKey || event.metaKey;
      if (event.key === "Escape") {
        if (isVoiceOpen()) exitVoice();
        else if (profileCardRole) setProfileCardRole(null);
        else if (modal === "voice-entry") closeVoiceEntry();
        else if (modal) closeModal();
        else if (generating) void cancelRun();
      }
      if (ctrl && event.key.toLowerCase() === "n") { event.preventDefault(); newSession(); }
      if (ctrl && event.shiftKey && event.key.toLowerCase() === "m") { event.preventDefault(); isVoiceOpen() ? exitVoice() : openVoiceEntry(); }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [cancelRun, closeModal, closeVoiceEntry, exitVoice, generating, modal, newSession, openVoiceEntry, profileCardRole]);

  useEffect(() => () => {
    stopAudio();
    closeCaptureContext();
    closePlaybackContext();
  }, [closeCaptureContext, closePlaybackContext, stopAudio, stopListening]);

  const openSettings = useCallback((tab = "model") => {
    setSettingsInitialTab(tab);
    setModalDirty(false);
    setModal("settings");
  }, []);
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
  const toolCapability = getProviderToolCapability();
  const pickerCharacters = characters;

  const enterDrawMode = () => navigate("draw");

  const enterCustomMode = () => {
    requestResumeSession();
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
        onClose={() => closeCharacterPicker()}
        onChoose={(character) => void (characterPickerIntent === "new"
          ? startNewSessionForCharacter(character)
          : resumeCharacterSession(character))}
        onDraw={() => { closeCharacterPicker(); navigate("draw"); }}
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

  const chatNavigation = {
    sidebarOpen,
    search,
    sessions: filteredSessions,
    sessionId,
    avatars: effectiveAvatars,
    characterName,
    onHome: () => navigate("modes"),
    onCloseSidebar: () => setSidebarOpen(false),
    onSearch: setSearch,
    onOpenSession: (id: string) => { void openSession(id); },
    onDeleteSession: (id: string) => { void deleteSession(id); },
    onOpenMemory: () => openModal("memory"),
    onOpenProfileCard: () => setProfileCardRole("assistant" as Role),
    onOpenProfileEditor: () => {
      setProfileEditorRole("assistant");
      setModal("profile");
    },
  };

  const chatConversation = {
    sceneActive: Boolean(conversationScene?.scene),
    sceneKey: conversationScene?.scene?.scene_id || "",
    sceneBackgroundImage: conversationScene?.scene
      ? 'url("' + sceneAssetPath(conversationScene.scene) + '")'
      : "",
    title,
    characterName,
    userName,
    generating,
    round,
    settingsReady: llmReady,
    settingsTitle: llmReady
      ? str(settings?.llm.model || "API 已连接") + " · 打开设置"
      : "API 尚未配置 · 打开设置",
    avatars: effectiveAvatars,
    conversationRef,
    conversationTailRef,
    onConversationScroll: handleConversationScroll,
    onPauseConversationFollow: pauseConversationFollow,
    onOpenSidebar: () => setSidebarOpen(true),
    onOpenProfileEditor: () => {
      setProfileEditorRole("assistant");
      setModal("profile");
    },
    onOpenSettings: () => openSettings("model"),
    welcome: {
      visible: !messages.length,
      relationshipLabel: activeCharacter?.relationship_label || "PRIVATE COMPANION",
      userAlias: activeCharacter?.user_alias || userName,
      onOpenProfileCard: () => setProfileCardRole("assistant"),
      onInitiative: () => { void sendMessage("", "primary", round, true); },
      onOpenScenes: () => navigate("scenes"),
      onOpenProfileEditor: () => {
        setProfileEditorRole("assistant");
        setModal("profile");
      },
    },
    messageList: {
      messages,
      avatars: effectiveAvatars,
      userName,
      characterName,
      onProfile: setProfileCardRole,
      onCopy: (text: string) => {
        void navigator.clipboard.writeText(text)
          .then(() => notify("已复制回复"))
          .catch((error) => notify(`复制失败：${(error as Error).message || "系统剪贴板不可用"}`));
      },
      onSpeak: speakMessage,
      onRegenerate: regenerateMessage,
      onInitiative: (targetRound: number) => { void sendMessage("", "regenerate", targetRound, true); },
      onDelete: (messageId?: string) => { void deleteReply(messageId); },
      onConfigure: () => openSettings("model"),
      onReply: (message: Message) => {
        if (!message.message_id) {
          notify("消息正在保存，请稍后再引用");
          return;
        }
        setReplyTarget(message);
      },
      onInteract: openInteractionForMessage,
    },
  };

  const chatComposer = {
    generating,
    characterName,
    input,
    onInput: setInput,
    onSend: () => { void sendMessage(); },
    onCancel: () => { void cancelRun(); },
    onOpenVoice: openVoiceEntry,
    asrReady,
    hasPayload: composerHasPayload,
    replyTarget,
    onClearReply: () => setReplyTarget(null),
    regenerationDraft: Boolean(regenerationDraft),
    onCancelRegeneration: cancelRegenerationDraft,
    onSendRegeneration: sendRegenerationDraft,
    pendingInteractions,
    onRemoveInteraction: removeInteraction,
    pendingAttachments,
    onRemoveAttachment: removeAttachment,
    onAttachmentFiles: handleAttachmentFiles,
    interactionOpen,
    interactionBranch,
    onInteractionOpen: setInteractionOpen,
    onInteractionBranch: setInteractionBranch,
    interactionTargets,
    onAddInteraction: addInteraction,
    customInteraction,
    onCustomInteraction: setCustomInteraction,
    round,
    onInitiative: () => { void sendMessage("", "primary", round, true); },
    sceneTitle: conversationScene?.scene?.title || "",
    onOpenScenes: () => navigate("scenes"),
    onShowFlow: showFlow,
    onShowContext: showContext,
    retrievalCount: retrieval.length,
    onExportSession: exportSession,
    adultMode,
    onToggleAdultMode: toggleAdultMode,
    r18StyleId,
    onR18StyleId: (next: string) => {
      setR18StyleId(next);
      localStorage.setItem(R18_STYLE_STORAGE_KEY, next);
    },
    model: str(settings?.llm.model),
    modelBaseUrl: llmBaseUrl,
    modelToolLabel: toolCapability.label,
    modelsLoading,
    availableModels,
    onLoadModels: () => { void loadAvailableModels(); },
    onChooseModel: (model: string) => { void chooseModel(model); },
    onClearCurrent: () => { void clearCurrent(); },
  };

  const chatOverlays = {
    content: <>
      <ExecutionInspector open={inspectorOpen} tab={inspectorTab} onTab={setInspectorTab} onClose={() => setInspectorOpen(false)} events={events} retrieval={retrieval} runId={inspectionRunId} />
      {modal === "settings" && settings && <SettingsWorkspace value={settings} avatars={avatars} initialTab={settingsInitialTab} onClose={closeModal} onDirty={setModalDirty} onOpenProfile={(role) => { setProfileEditorRole(role); setModalDirty(false); setModal("profile"); }} onOpenMemory={() => { setModalDirty(false); setModal("memory"); }} onOpenKnowledge={() => { setModalDirty(false); setModal("knowledge"); }} onOpenDiagnostics={() => { setModalDirty(false); setModal("diagnostics"); }} onSaved={(next, nextAvatars) => { setSettings(next); setAvatars(nextAvatars); setModalDirty(false); setModal(null); }} onSettingsChange={setSettings} onAvatarsChange={setAvatars} notify={notify} />}
      {modal === "knowledge" && <KnowledgeDialog onClose={closeModal} onDirty={setModalDirty} notify={notify} />}
      {modal === "memory" && <MemoryDialog characterId={activeCharacterId} onClose={closeModal} onDirty={setModalDirty} notify={notify} />}
      {modal === "profile" && <ProfileDialog characterId={activeCharacterId} initialName={profileEditorRole} onClose={closeModal} onDirty={setModalDirty} onOpenConnection={() => openSettings("model")} onOpenMemory={() => { setModalDirty(false); setModal("memory"); }} onSaved={() => void loadCharacters()} notify={notify} />}
      {modal === "diagnostics" && <DiagnosticsDialog onClose={closeModal} notify={notify} onCleared={() => { newSession(); void loadSessions(); }} />}
      {modal === "voice-entry" && <VoiceEntryDialog mode={voiceEntryMode} scene={voiceEntryScene} busy={voiceEntryBusy} error={voiceEntryError} onModeChange={(next) => { setVoiceEntryMode(next); setModalDirty(true); }} onSceneChange={(next) => { setVoiceEntryScene(next); setModalDirty(true); }} onClose={closeVoiceEntry} onStart={() => void startVoiceFromEntry()} />}
      {profileCardRole && <ProfileCardDialog characterId={activeCharacterId} role={profileCardRole} avatars={effectiveAvatars} displayName={profileCardRole === "user" ? userName : characterName} onClose={() => setProfileCardRole(null)} onEdit={(role) => { setProfileCardRole(null); setProfileEditorRole(role); setModal("profile"); }} />}
      {voice.open && <VoiceMode state={voice} avatar={effectiveAvatars.assistant} characterName={characterName} context={getVoiceContext()} companion={{ enabled: bool(settings?.interaction?.unlimited_reply_enabled), round: companionRound, limit: Math.max(1, Math.min(50, num(settings?.interaction?.unlimited_reply_max_rounds, 10))) }} onExit={exitVoice} onRetry={retryVoice} onFallback={useBrowserVoiceFallback} />}
      <CharacterPicker open={characterPickerOpen} characters={pickerCharacters} onClose={() => closeCharacterPicker()} onChoose={(character) => void (characterPickerIntent === "new" ? startNewSessionForCharacter(character) : resumeCharacterSession(character))} onDraw={() => { closeCharacterPicker(); navigate("draw"); }} />
      {nsfwConfirmationOpen && <NsfwAdultConfirmation seconds={nsfwConfirmationSeconds} onCancel={() => setNsfwConfirmationOpen(false)} onConfirm={confirmAdultMode} />}
      {toast && <div className="toast" role="status">{toast}</div>}
    </>,
  };

  return <ChatWorkspace
    navigation={chatNavigation}
    conversation={chatConversation}
    composer={chatComposer}
    overlays={chatOverlays}
    inspectorOpen={inspectorOpen}
  />;}

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

export default App;

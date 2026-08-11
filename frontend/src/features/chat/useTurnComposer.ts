import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
} from "react";

import {
  hasMissingAttachmentContent,
  mergeAttachmentFiles,
  requestAttachments,
} from "../../chat-contract";
import { bool, num, str } from "../../shared/formatters";
import type { TurnSend } from "../../shared/turn";
import type {
  CharacterSummary,
  ChatAttachment,
  ChatTurnRequest,
  InitiativeTrigger,
  InteractionTag,
  Message,
  ProductSettings,
  VoiceDeliveryState,
  VoiceInteractionContext,
} from "../../types";
import type { useChatRuntime } from "./useChatRuntime";

type ChatRuntime = ReturnType<typeof useChatRuntime>;

interface PendingASREvidence {
  uncertain_segments: Array<{ text: string; reason: string }>;
  decision_reasons: string[];
}

interface PreparedVoiceTurn {
  content: string;
  targetRound: number;
  initiative: boolean;
}

export interface TurnComposerEffects {
  onMissingCharacter: () => void;
  onMissingLlm: () => void;
  cancelIdleContinuation: () => void;
  resetIdleContinuation: (trigger: InitiativeTrigger) => void;
  isVoiceOpen: () => boolean;
  isAudioPlaying: () => boolean;
  captureVoiceInterruption: (reason: string) => void;
  stopAudio: () => void;
  resetResponseState: () => void;
  markVoiceThinking: (turn: PreparedVoiceTurn) => void;
  getPendingASREvidence: () => PendingASREvidence | null;
  clearPendingASREvidence: () => void;
  getVoiceDelivery: () => VoiceDeliveryState | null;
  getVoiceContext: () => VoiceInteractionContext | null;
  clearVoiceDelivery: () => void;
  setActiveInitiative: (trigger: InitiativeTrigger, sequence: number) => void;
}

interface UseTurnComposerOptions {
  character: {
    activeCharacterId: string;
    activeCharacter?: CharacterSummary;
  };
  settings: {
    value: ProductSettings | null;
    llmReady: boolean;
    adultMode: boolean;
    r18StyleId: string;
  };
  session: {
    sessionId: string;
  };
  runtime: Pick<
    ChatRuntime,
    | "cancelRun"
    | "completeRegeneration"
    | "executeTurn"
    | "generating"
    | "messages"
    | "round"
    | "setEvents"
    | "setRetrieval"
    | "stageRegeneration"
  >;
  voice: {
    effectsRef: MutableRefObject<TurnComposerEffects>;
  };
  notify: (message: string) => void;
}

export function useTurnComposer({
  character,
  settings,
  session,
  runtime,
  voice,
  notify,
}: UseTurnComposerOptions) {
  const [input, setInput] = useState("");
  const [pendingInteractions, setPendingInteractions] = useState<InteractionTag[]>([]);
  const [pendingAttachments, setPendingAttachments] = useState<ChatAttachment[]>([]);
  const [regenerationDraft, setRegenerationDraft] = useState<{
    round: number;
    request: ChatTurnRequest;
  } | null>(null);
  const [replyTarget, setReplyTarget] = useState<Message | null>(null);
  const [interactionOpen, setInteractionOpen] = useState(false);
  const [interactionBranch, setInteractionBranch] = useState<"root" | "touch" | "kiss">("root");
  const [customInteraction, setCustomInteraction] = useState("");
  const inputRef = useRef("");

  useEffect(() => {
    inputRef.current = input;
  }, [input]);

  const sendMessage = useCallback<TurnSend>(async (
    text = input,
    mode = "primary",
    targetRound = runtime.round,
    initiative = false,
    initiativeTrigger = initiative ? "manual" : "none",
    initiativeSequence = 0,
    initiativeSequenceLimit = 0,
    replayRequest,
  ) => {
    const effects = voice.effectsRef.current;
    const replay = mode === "regenerate" ? replayRequest : undefined;
    const content = replay?.message ?? (initiative ? "请求 AI 主动回复" : text.trim());
    const turnInteractions = replay?.interactions ?? (initiative ? [] : pendingInteractions);
    const turnAttachments = replay?.attachments ?? (initiative ? [] : pendingAttachments);
    const turnReplyTargetId = replay?.reply_to_message_id
      ?? (initiative ? "" : replyTarget?.message_id || "");
    const asrEvidence = replay?.input_evidence?.asr
      ?? (!initiative && effects.isVoiceOpen() ? effects.getPendingASREvidence() : null);
    if (!replay) effects.clearPendingASREvidence();
    if (!content && !turnInteractions.length && !turnAttachments.length) {
      notify("请输入消息、选择互动或添加附件");
      return;
    }
    if (hasMissingAttachmentContent(turnAttachments)) {
      notify("原回合附件正文未保存在本地，请重新附加全部标记文件后再生成");
      return;
    }
    if (!character.activeCharacterId) {
      notify("请先为当前会话选择角色");
      effects.onMissingCharacter();
      return;
    }
    if (!settings.llmReady) {
      notify("请先在设置中填写并保存 LLM API 配置");
      effects.onMissingLlm();
      return;
    }

    effects.cancelIdleContinuation();
    effects.resetIdleContinuation(initiativeTrigger);
    if (runtime.generating) await runtime.cancelRun();
    if (effects.isVoiceOpen() && effects.isAudioPlaying() && !initiative) {
      effects.captureVoiceInterruption("explicit_user_message");
    }
    effects.stopAudio();
    effects.resetResponseState();
    setInput("");
    if (!initiative) {
      setPendingInteractions([]);
      setPendingAttachments([]);
      setReplyTarget(null);
      setRegenerationDraft(null);
      setInteractionOpen(false);
      setInteractionBranch("root");
    }
    runtime.setEvents([]);
    runtime.setRetrieval([]);
    effects.markVoiceThinking({ content, targetRound, initiative });

    const requestId = crypto.randomUUID();
    effects.setActiveInitiative(initiativeTrigger, initiativeSequence);
    const clientSentAt = replay?.client_sent_at || new Date().toISOString();
    const persona = settings.value?.persona;
    const retrievalSettings = settings.value?.retrieval;
    const llm = settings.value?.llm;
    const voiceOpen = effects.isVoiceOpen();
    const payload: ChatTurnRequest = replay ? {
      ...structuredClone(replay),
      mode: "regenerate",
      round: targetRound,
      attachments: requestAttachments(turnAttachments),
    } : {
      message: content,
      session_id: session.sessionId,
      character_id: character.activeCharacterId,
      reply_to_message_id: turnReplyTargetId,
      interactions: turnInteractions.map((item) => ({ ...item })),
      attachments: requestAttachments(turnAttachments),
      activity_session_id: "",
      session_mode: character.activeCharacter?.source === "draw" ? "draw" : "custom",
      round: targetRound,
      mode,
      interaction_mode: voiceOpen ? "voice" : "text",
      presentation_mode: "auto",
      adult_mode: settings.adultMode,
      r18_style_id: settings.r18StyleId,
      initiative,
      initiative_trigger: initiativeTrigger,
      initiative_sequence: initiativeSequence,
      initiative_sequence_limit: initiativeSequenceLimit,
      client_sent_at: clientSentAt,
      client_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      client_utc_offset_minutes: -new Date().getTimezoneOffset(),
      voice_delivery: voiceOpen ? effects.getVoiceDelivery() : null,
      voice_context: voiceOpen ? effects.getVoiceContext() : null,
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
      api: {
        temperature: num(llm?.temperature, 0.7),
        max_tokens: num(llm?.max_tokens, 2000),
      },
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
    const user: Message = {
      role: "user",
      content,
      round: targetRound,
      status: "complete",
      timestamp: clientSentAt,
      reply_to_message_id: turnReplyTargetId || undefined,
      interactions: turnInteractions,
      attachments: turnAttachments.map((item) => ({ ...item })),
      request_snapshot: payload,
    };
    const assistant: Message = {
      role: "assistant",
      content: "",
      round: targetRound,
      status: "streaming",
      kind: initiative ? "initiative_response" : "message",
      initiative_trigger: initiativeTrigger,
    };
    const outgoing = initiative ? [assistant] : [user, assistant];
    if (voiceOpen) effects.clearVoiceDelivery();
    await runtime.executeTurn({ requestId, payload, outgoing, mode, targetRound });
  }, [
    character.activeCharacter,
    character.activeCharacterId,
    input,
    notify,
    pendingAttachments,
    pendingInteractions,
    replyTarget,
    runtime,
    session.sessionId,
    settings,
    voice.effectsRef,
  ]);

  const addInteraction = useCallback((
    category: InteractionTag["category"],
    action: string,
    target = "",
    sensitivity: InteractionTag["sensitivity"] = "normal",
  ) => {
    const id = `${category}:${action}:${target}`;
    setPendingInteractions((items) => items.some((item) => item.id === id)
      ? items
      : [...items, {
        id,
        category,
        level: category === "daily" ? 0 : category === "touch" ? 1 : category === "kiss" ? 2 : 9,
        action,
        target,
        sensitivity,
      }]);
  }, []);

  const removeInteraction = useCallback((id: string) => {
    setPendingInteractions((items) => items.filter((candidate) => candidate.id !== id));
  }, []);

  const handleAttachmentFiles = useCallback(async (files: FileList | null) => {
    if (!files?.length) return;
    const result = await mergeAttachmentFiles(
      pendingAttachments,
      Array.from(files),
      Boolean(regenerationDraft),
    );
    setPendingAttachments(result.attachments);
    if (result.feedback.length) notify(result.feedback.join("；"));
  }, [notify, pendingAttachments, regenerationDraft]);

  const removeAttachment = useCallback((attachment: ChatAttachment) => {
    setPendingAttachments((items) => items.filter(
      (candidate) => candidate.attachment_id !== attachment.attachment_id,
    ));
    notify("已移除附件 " + attachment.name);
  }, [notify]);

  const regenerateMessage = useCallback((message: Message, targetRound: number) => {
    const staged = runtime.stageRegeneration(message, targetRound);
    if (!staged) {
      notify("这个旧回合没有完整请求快照，无法保证一致重放；请作为新消息发送");
      return;
    }
    const { snapshot, preparation } = staged;
    if (preparation.request) {
      void sendMessage(
        snapshot.message,
        "regenerate",
        targetRound,
        false,
        snapshot.initiative_trigger,
        snapshot.initiative_sequence,
        snapshot.initiative_sequence_limit,
        preparation.request,
      );
      return;
    }
    setRegenerationDraft({ round: targetRound, request: snapshot });
    setInput(snapshot.message);
    setPendingInteractions(snapshot.interactions.map((item) => ({ ...item })));
    setPendingAttachments(preparation.stagedAttachments);
    setReplyTarget(snapshot.reply_to_message_id
      ? runtime.messages.find((item) => item.message_id === snapshot.reply_to_message_id) || null
      : null);
    notify(`请重新附加 ${preparation.missingAttachments.length} 个原附件；补齐前不会发送`);
  }, [notify, runtime, sendMessage]);

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
    const preparation = runtime.completeRegeneration(
      regenerationDraft.request,
      pendingAttachments,
    );
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
  }, [notify, pendingAttachments, regenerationDraft, runtime, sendMessage]);

  const openInteractionForMessage = useCallback((message: Message) => {
    if (message.message_id) setReplyTarget(message);
    setInteractionOpen(true);
    setInteractionBranch("root");
  }, []);

  return {
    addInteraction,
    cancelRegenerationDraft,
    customInteraction,
    handleAttachmentFiles,
    hasPayload: Boolean(input.trim() || pendingInteractions.length || pendingAttachments.length),
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
  };
}

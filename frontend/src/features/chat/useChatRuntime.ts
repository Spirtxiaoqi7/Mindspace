import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
} from "react";

import { useConversation } from "../../chat/useConversation";
import type {
  InspectorEvent,
  Message,
  StreamEnvelope,
} from "../../types";

export interface ChatRuntimeCallbacks {
  handleStreamEvent: (event: StreamEnvelope, isRecoveryReplay?: boolean) => void;
  notify: (message: string) => void;
  onBeforeRecovery: () => void;
  onConversationJump: () => void;
  onCancelEffects: () => void;
  onRequestFailure: (error: Error) => void;
}

interface UseChatRuntimeOptions {
  sessionId: string;
  initialDataLoaded: boolean;
  callbacksRef: MutableRefObject<ChatRuntimeCallbacks>;
}

export function useChatRuntime({
  sessionId,
  initialDataLoaded,
  callbacksRef,
}: UseChatRuntimeOptions) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [round, setRound] = useState(1);
  const [runId, setRunId] = useState("");
  const [inspectionRunId, setInspectionRunId] = useState("");
  const [generating, setGenerating] = useState(false);
  const [events, setEvents] = useState<InspectorEvent[]>([]);
  const [retrieval, setRetrieval] = useState<Record<string, unknown>[]>([]);

  const generatingRef = useRef(false);
  const runIdRef = useRef("");
  const roundRef = useRef(1);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    generatingRef.current = generating;
  }, [generating]);

  useEffect(() => {
    roundRef.current = round;
  }, [round]);

  const addEvent = useCallback((event: InspectorEvent) => {
    setEvents((items) => [...items.slice(-79), event]);
  }, []);

  const {
    executeTurn,
    cancelRun,
    stageRegeneration,
    completeRegeneration,
  } = useConversation({
    sessionId,
    initialDataLoaded,
    generatingRef,
    runIdRef,
    abortRef,
    setGenerating,
    setRunId,
    setMessages,
    handleStreamEvent: (event, isRecoveryReplay) => {
      callbacksRef.current.handleStreamEvent(event, isRecoveryReplay);
    },
    notify: (message) => callbacksRef.current.notify(message),
    onBeforeRecovery: () => callbacksRef.current.onBeforeRecovery(),
    onConversationJump: () => callbacksRef.current.onConversationJump(),
    onCancelEffects: () => callbacksRef.current.onCancelEffects(),
    onRequestFailure: (error) => callbacksRef.current.onRequestFailure(error),
  });

  return {
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
  };
}

import { useCallback, useEffect, useRef } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { cancelRunRequest, consumeResumableEventStream, openChatStream, openRunEventStream } from "../api";
import { clearActiveRun, createActiveRunRecord, prepareRegeneration, readActiveRun, readTurnRequestSnapshot, recoveredUserMessage, saveTurnRequestSnapshot, writeActiveRun } from "../chat-contract";
import type { ChatAttachment, ChatTurnRequest, Message, StreamEnvelope } from "../types";

export interface ExecuteConversationTurn {
  requestId: string;
  payload: ChatTurnRequest;
  outgoing: Message[];
  mode: "primary" | "regenerate";
  targetRound: number;
}

interface UseConversationOptions {
  sessionId: string;
  initialDataLoaded: boolean;
  generatingRef: MutableRefObject<boolean>;
  runIdRef: MutableRefObject<string>;
  abortRef: MutableRefObject<AbortController | null>;
  setGenerating: Dispatch<SetStateAction<boolean>>;
  setRunId: Dispatch<SetStateAction<string>>;
  setMessages: Dispatch<SetStateAction<Message[]>>;
  handleStreamEvent: (event: StreamEnvelope, recovery?: boolean) => void;
  notify: (message: string) => void;
  onBeforeRecovery: () => void;
  onCancelEffects: () => void;
  onRequestFailure: (error: Error) => void;
  onConversationJump: () => void;
}

export function useConversation(options: UseConversationOptions) {
  const latest = useRef(options);
  latest.current = options;

  const executeTurn = useCallback(async ({ requestId, payload, outgoing, mode, targetRound }: ExecuteConversationTurn) => {
    const current = latest.current;
    current.runIdRef.current = requestId;
    current.setRunId(requestId);
    current.setGenerating(true);
    writeActiveRun(createActiveRunRecord(requestId, payload));
    saveTurnRequestSnapshot(payload);
    current.onConversationJump();
    current.setMessages((items) => [...(mode === "regenerate" ? items.filter((item) => item.round !== targetRound) : items), ...outgoing]);
    const controller = new AbortController();
    current.abortRef.current = controller;
    try {
      const response = await openChatStream(payload, requestId, controller.signal);
      await consumeResumableEventStream(response, requestId, current.handleStreamEvent, controller.signal);
    } catch (reason) {
      const error = reason as Error;
      if (error.name !== "AbortError") {
        current.notify(error.message);
        current.setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content: "模型连接失败。请检查 API 地址、密钥或模型名称，然后重新尝试。", status: "error" as const } : item));
        current.onRequestFailure(error);
      }
      current.setGenerating(false);
    } finally {
      if (current.abortRef.current === controller) current.abortRef.current = null;
    }
  }, []);

  const cancelRun = useCallback(async () => {
    const current = latest.current;
    current.abortRef.current?.abort();
    const active = current.runIdRef.current;
    if (active) await cancelRunRequest(active).catch(() => undefined);
    current.setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, status: "cancelled" as const } : item));
    current.setGenerating(false);
    current.runIdRef.current = "";
    current.setRunId("");
    clearActiveRun(active);
    current.onCancelEffects();
  }, []);

  const stageRegeneration = useCallback((message: Message, targetRound: number) => {
    const current = latest.current;
    const snapshot = message.request_snapshot || readTurnRequestSnapshot(current.sessionId, targetRound);
    if (!snapshot) return null;
    return { snapshot, preparation: prepareRegeneration(snapshot) };
  }, []);

  const completeRegeneration = useCallback((request: ChatTurnRequest, attachments: ChatAttachment[]) => prepareRegeneration({
    ...request,
    attachments,
  }), []);

  useEffect(() => {
    const current = latest.current;
    if (!current.initialDataLoaded || current.generatingRef.current) return;
    const active = readActiveRun();
    if (!active || active.session_id !== current.sessionId) return;
    current.onBeforeRecovery();
    const controller = new AbortController();
    current.abortRef.current = controller;
    current.runIdRef.current = active.run_id;
    current.setRunId(active.run_id);
    current.setGenerating(true);
    current.setMessages((items) => {
      const hasUser = items.some((item) => item.round === active.round && item.role === "user");
      const hasAssistant = items.some((item) => item.round === active.round && item.role === "assistant");
      const recovered: Message[] = [];
      if (!hasUser && !active.request.initiative) recovered.push(recoveredUserMessage(active));
      if (!hasAssistant) recovered.push({ role: "assistant", content: "", round: active.round, status: "streaming" });
      return recovered.length ? [...items, ...recovered] : items;
    });
    void openRunEventStream(active.run_id, 0, controller.signal).then((response) => consumeResumableEventStream(
      response,
      active.run_id,
      (event) => latest.current.handleStreamEvent(event, true),
      controller.signal,
    )).catch((reason: Error) => {
      if (reason.name === "AbortError") return;
      clearActiveRun(active.run_id);
      current.runIdRef.current = "";
      current.setRunId("");
      current.setGenerating(false);
      current.setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content: item.content || "未找到可恢复的运行", status: "error" as const } : item));
      current.notify(reason.message);
    }).finally(() => {
      if (current.abortRef.current === controller) current.abortRef.current = null;
    });
    return () => controller.abort();
  }, [options.initialDataLoaded, options.sessionId]);

  return { executeTurn, cancelRun, stageRegeneration, completeRegeneration };
}

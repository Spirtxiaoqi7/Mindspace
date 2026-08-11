import { useCallback, type Dispatch, type SetStateAction } from "react";

import { request } from "../../shared/api";
import { clearTurnRequestSnapshots } from "../../chat-contract";
import type { Message } from "../../types";
import { styledConfirm } from "../../ui/styledConfirm";

interface UseConversationMaintenanceOptions {
  loadSessions: () => Promise<unknown>;
  messages: Message[];
  notify: (message: string) => void;
  sessionId: string;
  setMessages: Dispatch<SetStateAction<Message[]>>;
  setRound: Dispatch<SetStateAction<number>>;
}

export function useConversationMaintenance({
  loadSessions,
  messages,
  notify,
  sessionId,
  setMessages,
  setRound,
}: UseConversationMaintenanceOptions) {
  const deleteReply = useCallback(async (messageId?: string) => {
    if (!messageId) {
      notify("回复尚未完成保存，暂时不能删除");
      return;
    }
    if (!(await styledConfirm({
      title: "删除这条 AI 回复？",
      message: "用户原话会保留，相关 JSON 会在下一轮重新校正。",
      confirmLabel: "删除回复",
      danger: true,
    }))) return;
    const result = await request<{ pending_json_reconciliation: boolean }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`,
      { method: "DELETE" },
    );
    setMessages((items) => items.filter((item) => item.message_id !== messageId));
    await loadSessions();
    notify(result.pending_json_reconciliation
      ? "回复已删除；相关 JSON 将在下一轮重新校正"
      : "主动回复已删除");
  }, [loadSessions, notify, sessionId, setMessages]);

  const clearCurrent = useCallback(async () => {
    if (!messages.length) {
      notify("当前会话没有可清空的内容");
      return;
    }
    if (!(await styledConfirm({
      title: "清空当前上下文？",
      message: "当前会话内容会被清空，但人物档案与长期记忆不会被删除。",
      confirmLabel: "清空上下文",
      danger: true,
    }))) return;
    await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/clear`, { method: "POST" });
    clearTurnRequestSnapshots(sessionId);
    setMessages([]);
    setRound(1);
    await loadSessions();
    notify("当前上下文已清空");
  }, [loadSessions, messages.length, notify, sessionId, setMessages, setRound]);

  return { clearCurrent, deleteReply };
}

import { useCallback, useMemo, useState } from "react";

import { request } from "../../shared/api";
import { num, str } from "../../shared/formatters";
import type {
  CharacterRecord,
  CharacterSummary,
  SessionDocument,
  SessionSummary,
} from "../../types";
import { styledConfirm } from "../../ui/styledConfirm";

const SESSION_STORAGE_KEY = "mindspace.session";

export function useSessionDirectory() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState(
    () => localStorage.getItem(SESSION_STORAGE_KEY) || crypto.randomUUID(),
  );
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [characterPickerOpen, setCharacterPickerOpen] = useState(false);
  const [characterPickerIntent, setCharacterPickerIntent] = useState<"resume" | "new">("resume");

  const loadSessions = useCallback(async () => {
    try {
      const result = await request<{ sessions: SessionSummary[] }>("/api/v1/sessions");
      setSessions(result.sessions);
      setError("");
      return result.sessions;
    } catch (reason) {
      setError((reason as Error).message);
      throw reason;
    }
  }, []);

  const openSessionRecord = useCallback(async (id: string) => {
    try {
      const value = await request<SessionDocument>(`/api/v1/sessions/${encodeURIComponent(id)}`);
      const sameSession = id === sessionId;
      setSessionId(id);
      localStorage.setItem(SESSION_STORAGE_KEY, id);
      setError("");
      return { value, sameSession };
    } catch (reason) {
      setError((reason as Error).message);
      throw reason;
    }
  }, [sessionId]);

  const createSessionRecord = useCallback(async (character: CharacterSummary | CharacterRecord) => {
    const id = crypto.randomUUID();
    try {
      await request<SessionDocument>("/api/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ session_id: id, character_id: character.character_id }),
      });
      setSessionId(id);
      localStorage.setItem(SESSION_STORAGE_KEY, id);
      setError("");
      return id;
    } catch (reason) {
      setError((reason as Error).message);
      throw reason;
    }
  }, []);

  const selectPreferredSession = useCallback((
    loadedSessions: SessionSummary[],
    characters: CharacterSummary[],
    routeSessionId: string,
    rememberedSessionId: string | null,
  ) => {
    const activeCharacterIds = new Set(
      characters
        .filter((item) => item.status === "active")
        .map((item) => item.character_id),
    );
    const usableSessions = loadedSessions.filter((item) =>
      !item.character_id || activeCharacterIds.has(item.character_id),
    );
    return usableSessions.find((item) => item.session_id === routeSessionId)
      || usableSessions.find((item) => item.session_id === rememberedSessionId)
      || usableSessions[0];
  }, []);

  const findResumeSession = useCallback((character: CharacterSummary | CharacterRecord) => {
    const matchingSessions = sessions
      .filter((item) => item.character_id === character.character_id
        || (!item.character_id && str(item.character_name).trim() === character.display_name.trim()))
      .sort((left, right) => Date.parse(right.updated_at || "0") - Date.parse(left.updated_at || "0"));
    return matchingSessions[0]?.session_id || "";
  }, [sessions]);

  const deleteSessionRecord = useCallback(async (
    id: string,
    refreshCharacters: () => Promise<CharacterSummary[]>,
  ) => {
    const target = sessions.find((item) => item.session_id === id);
    if (!target) throw new Error("会话不存在或已经删除");
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
    }))) return null;
    try {
      await request(`/api/v1/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (localStorage.getItem(SESSION_STORAGE_KEY) === id) {
        localStorage.removeItem(SESSION_STORAGE_KEY);
      }
      const [remaining, refreshedCharacters] = await Promise.all([
        loadSessions(),
        refreshCharacters(),
      ]);
      const deletedCharacterId = str(target.character_id);
      const nextSessionId = id === sessionId
        ? remaining.find((item) => item.character_id === deletedCharacterId)?.session_id || ""
        : "";
      setError("");
      return {
        target,
        remaining,
        refreshedCharacters,
        deletedCharacterId,
        nextSessionId,
        deletedCurrentSession: id === sessionId,
      };
    } catch (reason) {
      setError((reason as Error).message);
      throw reason;
    }
  }, [loadSessions, sessionId, sessions]);

  const newSession = useCallback(() => {
    setCharacterPickerIntent("new");
    setCharacterPickerOpen(true);
  }, []);

  const requestResumeSession = useCallback(() => {
    setCharacterPickerIntent("resume");
    setCharacterPickerOpen(true);
  }, []);

  const closeCharacterPicker = useCallback(() => {
    setCharacterPickerOpen(false);
  }, []);

  const recentSessions = useMemo(() => [...sessions]
    .sort((left, right) => Date.parse(right.updated_at || "0") - Date.parse(left.updated_at || "0"))
    .slice(0, 40), [sessions]);

  const filteredSessions = useMemo(() => {
    const normalizedQuery = search.trim().toLowerCase();
    return recentSessions.filter((item) => !normalizedQuery
      || item.title.toLowerCase().includes(normalizedQuery)
      || str(item.character_name).toLowerCase().includes(normalizedQuery));
  }, [recentSessions, search]);

  const title = sessions.find((item) => item.session_id === sessionId)?.title || "新对话";
  const interruptedSession = sessions.find((item) =>
    Boolean((item as SessionSummary & { interrupted?: boolean }).interrupted));

  return {
    characterPickerIntent,
    characterPickerOpen,
    closeCharacterPicker,
    createSessionRecord,
    deleteSessionRecord,
    error,
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
  };
}

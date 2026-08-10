import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { createActiveRunRecord, writeActiveRun } from "../chat-contract";
import type { ChatTurnRequest, Message, StreamEnvelope } from "../types";
import { useConversation } from "./useConversation";

const request: ChatTurnRequest = {
  message: "恢复这轮",
  session_id: "session-a",
  character_id: "character-a",
  reply_to_message_id: "",
  interactions: [],
  attachments: [],
  activity_session_id: "",
  session_mode: "draw",
  round: 2,
  mode: "primary",
  interaction_mode: "text",
  presentation_mode: "auto",
  adult_mode: false,
  r18_style_id: "high_intensity",
  initiative: false,
  initiative_trigger: "none",
  initiative_sequence: 0,
  initiative_sequence_limit: 0,
  client_sent_at: "2026-08-10T12:00:00Z",
  client_timezone: "Asia/Shanghai",
  client_utc_offset_minutes: 480,
  voice_delivery: null,
  voice_context: null,
  input_evidence: null,
  user_name: "用户",
  user_persona: "",
  reply_length_preference: "",
  character_name: "角色",
  system_prompt: "",
  api: { temperature: 0.7, max_tokens: 2000 },
  retrieval: { rag_enabled: true, knowledge_enabled: true, chat_enabled: true, structured_memory_enabled: true, temporal_enabled: true, knowledge_k: 2, chat_k: 3, history_k: 3, similarity_threshold: 0.5, decay_rounds: 20, decay_hours: 168, fairness_enabled: true, low_exposure_ratio: 0.2, memory_family_limit: 2, starvation_rounds: 6, starvation_boost: 0.12, bm25_enabled: true, vector_enabled: true, rrf_k: 60, candidate_multiplier: 4, max_total_boost: 0.25, reranker_enabled: false, reranker_top_n: 12, boosts: {} },
};

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

it("recovers a durable active run through the same resumable SSE path", async () => {
  writeActiveRun(createActiveRunRecord("run-a", request));
  const envelope: StreamEnvelope = { version: "1", event: "run.completed", run_id: "run-a", session_id: "session-a", round: 2, seq: 1, timestamp: "2026-08-10T12:00:01Z", data: { response: { reply: "已恢复" } } };
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(`id: 1\nevent: run.completed\ndata: ${JSON.stringify(envelope)}\n\n`, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
  let messages: Message[] = [];
  const setMessages = vi.fn((update: Message[] | ((items: Message[]) => Message[])) => { messages = typeof update === "function" ? update(messages) : update; });
  const handle = vi.fn();
  const generatingRef = { current: false };
  const runIdRef = { current: "" };
  const abortRef = { current: null as AbortController | null };

  renderHook(() => useConversation({
    sessionId: "session-a", initialDataLoaded: true, generatingRef, runIdRef, abortRef,
    setGenerating: vi.fn(), setRunId: vi.fn(), setMessages, handleStreamEvent: handle,
    notify: vi.fn(), onBeforeRecovery: vi.fn(), onCancelEffects: vi.fn(), onRequestFailure: vi.fn(), onConversationJump: vi.fn(),
  }));

  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(handle).toHaveBeenCalledWith(expect.objectContaining({ event: "run.completed", run_id: "run-a" }), true));
  expect(messages).toEqual(expect.arrayContaining([expect.objectContaining({ role: "user", content: "恢复这轮" }), expect.objectContaining({ role: "assistant", status: "streaming" })]));
  expect(fetch).toHaveBeenCalledTimes(1);
});

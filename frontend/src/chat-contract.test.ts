import { describe, expect, it, vi } from "vitest";

import {
  closeOpenMenusOutside,
  composerAction,
  createActiveRunRecord,
  hasMissingAttachmentContent,
  mergeAttachmentFiles,
  modelAttemptInspectorEvent,
  modelSummaryInspectorEvent,
  prepareRegeneration,
  providerToolCapability,
  readActiveRun,
  recoveredUserMessage,
  saveTurnRequestSnapshot,
  shouldRenderToolExecution,
  shouldShowComposerAction,
  writeActiveRun,
} from "./chat-contract";
import type { ChatTurnRequest, ModelDiagnostics, ProviderHttpAttempt } from "./types";

const requestFixture = (): ChatTurnRequest => ({
  message: "看看这个附件",
  session_id: "session-a",
  character_id: "character-a",
  reply_to_message_id: "message-before",
  interactions: [{ id: "touch:抚摸:头发", category: "touch", level: 1, action: "抚摸", target: "头发", sensitivity: "normal" }],
  attachments: [{ attachment_id: "attachment-a", name: "notes.txt", media_type: "text/plain", size: 5, content: "hello" }],
  activity_session_id: "",
  session_mode: "draw",
  round: 7,
  mode: "primary",
  interaction_mode: "text",
  presentation_mode: "auto",
  adult_mode: true,
  r18_style_id: "dialogue_led",
  initiative: false,
  initiative_trigger: "none",
  initiative_sequence: 0,
  initiative_sequence_limit: 0,
  client_sent_at: "2026-08-10T12:00:00.000Z",
  client_timezone: "Asia/Shanghai",
  client_utc_offset_minutes: 480,
  voice_delivery: null,
  voice_context: null,
  input_evidence: null,
  user_name: "用户",
  user_persona: "直率",
  reply_length_preference: "详细",
  character_name: "林见月",
  system_prompt: "保持角色",
  api: { temperature: 0.8, max_tokens: 2400 },
  retrieval: {
    rag_enabled: true, knowledge_enabled: true, chat_enabled: true, structured_memory_enabled: true,
    temporal_enabled: true, knowledge_k: 2, chat_k: 3, history_k: 4, similarity_threshold: 0.4,
    decay_rounds: 20, decay_hours: 168, fairness_enabled: true, low_exposure_ratio: 0.2,
    memory_family_limit: 2, starvation_rounds: 6, starvation_boost: 0.12, bm25_enabled: true,
    vector_enabled: true, rrf_k: 60, candidate_multiplier: 4, max_total_boost: 0.25,
    reranker_enabled: false, reranker_top_n: 12, boosts: { relationship: 1.1 },
  },
});

describe("chat request persistence and regeneration", () => {
  it("persists every prompt-affecting field while marking attachment bodies for reattachment", () => {
    const original = requestFixture();
    writeActiveRun(createActiveRunRecord("run-a", original));
    saveTurnRequestSnapshot(original);
    const active = readActiveRun();

    expect(active?.request).toMatchObject({
      adult_mode: true,
      r18_style_id: "dialogue_led",
      reply_to_message_id: "message-before",
      interaction_mode: "text",
      api: { temperature: 0.8, max_tokens: 2400 },
      retrieval: { history_k: 4 },
    });
    expect(active?.request.attachments[0]).toEqual({
      attachment_id: "attachment-a", name: "notes.txt", media_type: "text/plain", size: 5, content_missing: true,
    });
    expect(recoveredUserMessage(active!).interactions).toEqual(original.interactions);
  });

  it("replays a complete request without replacing its modes or prompt controls", () => {
    const original = requestFixture();
    const prepared = prepareRegeneration(original);
    expect(prepared.missingAttachments).toEqual([]);
    expect(prepared.request).toEqual({ ...original, mode: "regenerate" });
  });

  it("blocks regeneration when an attachment body is unavailable", () => {
    const original = requestFixture();
    original.attachments = [{ ...original.attachments[0], content: undefined, content_missing: true }];
    const prepared = prepareRegeneration(original);
    expect(prepared.request).toBeNull();
    expect(hasMissingAttachmentContent(prepared.stagedAttachments)).toBe(true);
  });
});

describe("attachment guardrails", () => {
  it("rejects duplicates, unsupported files and read failures", async () => {
    const existing = [{ attachment_id: "a", name: "same.txt", media_type: "text/plain", size: 4, content: "same" }];
    const duplicate = new File(["same"], "same.txt", { type: "text/plain" });
    const unsupported = new File(["x"], "image.png", { type: "image/png" });
    const broken = new File(["broken"], "broken.txt", { type: "text/plain" });
    Object.defineProperty(broken, "text", { value: vi.fn().mockRejectedValue(new Error("read failed")) });
    const result = await mergeAttachmentFiles(existing, [duplicate, unsupported, broken]);
    expect(result.attachments).toHaveLength(1);
    expect(result.feedback.join(" ")).toContain("重复");
    expect(result.feedback.join(" ")).toContain("格式不支持");
    expect(result.feedback.join(" ")).toContain("读取失败");
  });

  it("replaces only the matching missing attachment during guarded reattachment", async () => {
    const missing = [{ attachment_id: "a", name: "same.txt", media_type: "text/plain", size: 4, content_missing: true }];
    const wrong = new File(["no"], "wrong.txt", { type: "text/plain" });
    const exact = new File(["same"], "same.txt", { type: "text/plain" });
    const result = await mergeAttachmentFiles(missing, [wrong, exact], true);
    expect(result.attachments[0]).toMatchObject({ attachment_id: "a", content: "same", content_missing: false });
    expect(result.feedback.join(" ")).toContain("不一致");
  });
});

describe("execution details and product gates", () => {
  const attempt: ProviderHttpAttempt = {
    attempt: 2, request_kind: "main_generation", status: "http_error", elapsed_ms: 321,
    http_status: 429, compatibility_variant: "responses", retry_reason: "rate_limit", error: "busy",
  };
  const diagnostics: ModelDiagnostics = { call_summary: [], total_calls: 2, provider_attempts: [attempt], total_http_attempts: 3 };

  it("shows exact logical and provider attempt counts", () => {
    expect(modelAttemptInspectorEvent(attempt, "2026-08-10T12:00:00Z", 4).data).toEqual(attempt);
    expect(modelSummaryInspectorEvent(diagnostics, "2026-08-10T12:00:01Z").label).toBe("模型调用：逻辑 2 · HTTP 3");
  });

  it("never renders a tool card for null or empty tool state", () => {
    expect(shouldRenderToolExecution(null)).toBe(false);
    expect(shouldRenderToolExecution({} as never)).toBe(false);
  });

  it("uses live ASR readiness and renders only backend-probed provider capability", () => {
    expect(shouldShowComposerAction(false, false, false)).toBe(false);
    expect(composerAction(false, true, false)).toBe("send");
    expect(composerAction(false, false, true)).toBe("voice");
    expect(providerToolCapability("supported")).toMatchObject({ native: true, state: "supported" });
    expect(providerToolCapability("unsupported")).toMatchObject({ native: false, state: "unsupported" });
    expect(providerToolCapability("probing")).toMatchObject({ native: null, state: "probing" });
  });

  it("does not infer capability from OpenAI, custom-compatible, or DeepSeek URLs", () => {
    const providerUrls = [
      "https://api.openai.com/v1",
      "https://llm.example.test/openai/v1",
      "https://api.deepseek.com/v1",
    ];
    for (const baseUrl of providerUrls) {
      expect(providerToolCapability(baseUrl)).toMatchObject({ native: null, state: "unknown" });
      expect(providerToolCapability("transient_failure")).toMatchObject({ native: null, state: "unknown" });
    }
  });

  it("closes menus only when the pointer target is outside", () => {
    document.body.innerHTML = '<details class="message-more" open><summary>更多</summary><button>互动</button></details><div id="outside"></div>';
    const details = document.querySelector("details")!;
    const button = document.querySelector("button")!;
    expect(closeOpenMenusOutside(button)).toBe(0);
    expect(details).toHaveAttribute("open");
    expect(closeOpenMenusOutside(document.querySelector("#outside")!)).toBe(1);
    expect(details).not.toHaveAttribute("open");
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import App, {
  asrClientDisposition,
  companionContinuationPlan,
  shouldIgnoreASREvent,
  voiceMergeDelay,
} from "./App";

it("submits punctuated voice turns through the fast merge path", () => {
  expect(voiceMergeDelay("这句话说完了。", 900)).toBe(160);
  expect(voiceMergeDelay("我还没说完", 350)).toBe(350);
});

it("only schedules continuous companionship ten seconds after TTS and below its cap", () => {
  const interaction = { unlimited_reply_enabled: true, unlimited_reply_max_rounds: 12 };
  expect(companionContinuationPlan(interaction, false, 0)).toBeNull();
  expect(companionContinuationPlan(interaction, true, 2)).toEqual({ delaySeconds: 10, nextSequence: 3, limit: 12 });
  expect(companionContinuationPlan(interaction, true, 12)).toBeNull();
  expect(companionContinuationPlan({ ...interaction, unlimited_reply_max_rounds: 99 }, true, 49)).toEqual({ delaySeconds: 10, nextSequence: 50, limit: 50 });
});

it("drops speech events while a committed voice turn is waiting for TTS", () => {
  [
    "asr.speech_candidate",
    "asr.speech_candidate_cleared",
    "asr.speech_start",
    "asr.partial",
    "asr.final",
    "asr.deferred",
  ].forEach((event) => expect(shouldIgnoreASREvent(true, event)).toBe(true));
  expect(shouldIgnoreASREvent(true, "asr.ready")).toBe(false);
  expect(shouldIgnoreASREvent(true, "asr.error")).toBe(false);
  expect(shouldIgnoreASREvent(false, "asr.speech_start")).toBe(false);
});

it("never submits or interrupts for an all-low-confidence ASR draft", () => {
  expect(asrClientDisposition({
    text: "像是没听清的名字",
    quality: "uncertain",
    confirmed_text: "",
    uncertain_segments: [{ text: "没听清的名字", reason: "playback_unstable_text" }],
    auto_send: true,
    barge_in_eligible: true,
  })).toMatchObject({
    confirmedText: "",
    submitToLLM: false,
    commitBargeIn: false,
  });
});

it("submits only the reliable ASR backbone and keeps uncommon words as evidence", () => {
  expect(asrClientDisposition({
    text: "我想找阿斯塔利昂帮我配音",
    quality: "uncertain",
    confirmed_text: "我想找帮我配音",
    uncertain_segments: [{ text: "阿斯塔利昂", reason: "stream_final_disagreement" }],
    auto_send: true,
    barge_in_eligible: true,
  })).toMatchObject({
    confirmedText: "我想找帮我配音",
    uncertainSegments: [{ text: "阿斯塔利昂", reason: "stream_final_disagreement" }],
    submitToLLM: true,
    commitBargeIn: true,
  });
});

const settings = {
  schema_version: "1",
  llm: { mode: "openai", model: "deepseek-chat", base_url: "https://api.deepseek.com", temperature: 0.7, max_tokens: 1024, credentials_configured: true },
  persona: { user_name: "用户", character_name: "Mindspace", user_persona: "", system_prompt: "" },
  retrieval: {},
  knowledge: {},
  protocol: {},
  audio: { tts_provider: "siliconflow", tts_speed: 1, asr_ws_url: "ws://127.0.0.1:8766", tts_reference_configured: false },
  interaction: { idle_continuation_enabled: false, text_idle_seconds: 180, voice_idle_seconds: 30, unlimited_reply_enabled: false, unlimited_reply_interval_seconds: 10, unlimited_reply_max_rounds: 10 },
  appearance: { theme: "mindscape", density: "chat", font_scale: 1.3 },
};

const avatarConfig = {
  user: { src: "/assets/avatar-user-default.webp", aspect: "2 / 3", scale: 1, x: 0, y: 0 },
  assistant: { src: "/assets/avatar-ai-default.webp", aspect: "2 / 3", scale: 1, x: 0, y: 0 },
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function initiativeStream() {
  const envelope = (event: string, data: unknown, seq: number) =>
    `event: ${event}\ndata: ${JSON.stringify({ version: "1", event, seq, run_id: "initiative-run", session_id: "initiative-session", round: 1, timestamp: new Date().toISOString(), data })}\n\n`;
  return new Response([
    envelope("run.accepted", { request_id: "initiative-run" }, 1),
    envelope("response.delta", { delta: "那我就陪你安静一会儿。" }, 2),
    envelope("run.completed", { response: { assistant_message_id: "a-initiative", reply: "那我就陪你安静一会儿。" } }, 3),
  ].join(""), { headers: { "Content-Type": "text/event-stream" } });
}

function installFetchMock(settingsOverride: Record<string, unknown> = {}) {
  let currentSettings = { ...structuredClone(settings), ...structuredClone(settingsOverride) };
  let vocabulary = { revision: "v1", manual_revision: 0, profile_revisions: {}, counts: { manual: 0, profile: 1, system: 2 }, entries: [], decoder_hotwords: ["Mindspace"], explicit: {} };
  const profiles: Record<string, Record<string, unknown>> = {
    user: { schema_version: "1.0.0", profile_type: "user", revision: 0, identity: { preferred_name: "用户", occupation: "", language: "zh-CN" }, stable_preferences: { likes: [], dislikes: [], interests: [], habits: [] } },
    assistant: { schema_version: "1.0.0", profile_type: "ai", revision: 0, identity: { name: "Mindspace", self_description: "可靠的本地伙伴", relationship_to_user: "助手" }, personality: { core_traits: ["可靠", "克制"], speech_style: ["自然"] } },
    state: { schema_version: "1.0.0", profile_type: "runtime_state", revision: 0, relationship_state: { current_stage: "", current_tone: "", recent_conflicts: [], recent_positive_events: [], unresolved_issues: [] }, user_state: { current_goal: "", current_task: "", current_topic: "", temporary_preferences: [], current_emotional_cues: [] }, ai_state: { pending_responses: [], current_emotional_cues: [], current_intentions: [] } },
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if (url === "/api/v1/settings" && init.method === "PUT") {
      const payload = JSON.parse(String(init.body));
      const llm = { ...currentSettings.llm, ...payload.llm };
      const audio = { ...currentSettings.audio, ...payload.audio };
      llm.credentials_configured = Boolean(llm.api_key) || Boolean(llm.credentials_configured);
      audio.tts_siliconflow_credentials_configured = Boolean(audio.tts_siliconflow_api_key) || Boolean(audio.tts_siliconflow_credentials_configured);
      delete llm.api_key;
      delete audio.tts_siliconflow_api_key;
      currentSettings = { ...currentSettings, ...payload, llm, audio };
      return json({ settings: currentSettings });
    }
    if (url === "/api/v1/settings") return json(currentSettings);
    const profileCardMatch = url.match(/^\/api\/v1\/profiles\/(user|assistant)\/card$/);
    if (profileCardMatch) {
      const document = profiles[profileCardMatch[1]];
      return json({
        name: profileCardMatch[1],
        identity: document.identity || {},
        personality: document.personality || {},
        relationship: document.relationship_state || {},
        revision: document.revision || 0,
        updated_at: "",
      });
    }
    const profileHistoryMatch = url.match(/^\/api\/v1\/profiles\/(user|assistant|state)\/history$/);
    if (profileHistoryMatch) return json({ items: [] });
    const profileMatch = url.match(/^\/api\/v1\/profiles\/(user|assistant|state)$/);
    if (profileMatch && init.method === "PUT") {
      profiles[profileMatch[1]] = JSON.parse(String(init.body));
      return json({ document: profiles[profileMatch[1]] });
    }
    if (profileMatch) return json(profiles[profileMatch[1]]);
    if (url === "/api/v1/sessions") return json({ sessions: [] });
    if (url === "/api/v1/avatar/config" && init.method === "PUT") return json({ config: avatarConfig });
    if (url === "/api/v1/avatar/config") return json(avatarConfig);
    if (url === "/api/v1/audio/asr/vocabulary" && init.method === "PUT") {
      const payload = JSON.parse(String(init.body));
      vocabulary = { ...vocabulary, revision: "v2", manual_revision: 1, counts: { ...vocabulary.counts, manual: payload.entries.length }, entries: payload.entries.map((item: Record<string, unknown>) => ({ ...item, source: "manual", read_only: false, weight: 100 })) };
      return json(vocabulary);
    }
    if (url === "/api/v1/audio/asr/vocabulary") return json(vocabulary);
    if (url === "/api/v1/audio/asr/vocabulary/test" && init.method === "POST") return json({ corrected_text: "我想换成长离的声音", matches: [{ from: "长利", to: "长离" }] });
    if (url === "/api/v1/chat/stream" && init.method === "POST") return initiativeStream();
    if (url === "/api/v1/settings/test" && init.method === "POST") return json({ ok: true, mode: "openai", detail: "真实最小生成请求成功" });
    if (url === "/api/v1/audio/status") return json({ tts_ready: true });
    if (url === "/api/v1/audio/tts" && init.method === "POST") return new Response(new Uint8Array([82, 73, 70, 70]), { headers: { "Content-Type": "audio/wav" } });
    if (url.startsWith("/api/v1/memory/items") && init.method === "PUT") return json({ success: true });
    if (url.startsWith("/api/v1/memory/items")) return json({ items: [{ memory_key: "user:user.preference:berry", field_code: "user.preference.likes", display_name: "喜欢", category: "偏好", value: "草莓", scope: "user", lifecycle: "persistent", status: "active", created_at: "2026-07-19T00:00:00Z", updated_at: "2026-07-19T00:00:00Z", source_text: "用户：我喜欢草莓" }] });
    return json({ detail: `unexpected request ${url}` }, 404);
  }));
}

async function renderReady() {
  render(<App />);
  await waitFor(() => expect(document.querySelector(".account-settings")).toBeInTheDocument());
}

describe("Mindspace product interactions", () => {
  beforeEach(() => {
    localStorage.clear();
    installFetchMock();
  });

  it("binds every rendered button to explicit behavior", () => {
    const source = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf-8");
    const buttonTags = source.match(/<button\b[^>]*>/g) || [];
    expect(buttonTags.length).toBeGreaterThan(40);
    expect(buttonTags.filter((tag) => !tag.includes("onClick="))).toEqual([]);
  });

  it("keeps settings open when the backdrop is clicked", async () => {
    await renderReady();
    fireEvent.click(document.querySelector(".account-settings")!);
    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(document.querySelector(".modal-backdrop")!);
    expect(dialog).toBeInTheDocument();
  });

  it("asks before Escape closes a dirty dialog", async () => {
    await renderReady();
    fireEvent.click(document.querySelector(".account-settings")!);
    const dialog = await screen.findByRole("dialog");
    const firstInput = dialog.querySelector("input")!;
    fireEvent.change(firstInput, { target: { value: "changed" } });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(confirm).toHaveBeenCalledOnce();
    expect(dialog).toBeInTheDocument();
  });

  it("submits the complete LLM API key without exposing it in the response", async () => {
    const user = userEvent.setup();
    await renderReady();
    await user.click(document.querySelector(".account-settings")!);
    const keyInput = await screen.findByLabelText("新 API 密钥（留空保持）");
    await user.type(keyInput, "sk-complete-secret");
    expect(keyInput).toHaveValue("sk-complete-secret");
    await user.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url) === "/api/v1/settings" && init?.method === "PUT");
      expect(call).toBeDefined();
      const payload = JSON.parse(String(call?.[1]?.body));
      expect(payload.llm.api_key).toBe("sk-complete-secret");
    });
    expect(JSON.stringify(document.body.textContent)).not.toContain("sk-complete-secret");
  });

  it("locks the product to real API mode instead of exposing fixed demo replies", async () => {
    await renderReady();
    fireEvent.click(document.querySelector(".account-settings")!);
    const mode = screen.getByLabelText("运行模式");
    expect(mode).toHaveValue("openai");
    expect(mode).toBeDisabled();
    expect(screen.queryByText(/固定回复/)).not.toBeInTheDocument();
  });

  it("uses the 130% default and persists an adjustable interface font size", async () => {
    const user = userEvent.setup();
    await renderReady();
    expect(document.documentElement.style.fontSize).toBe("20.8px");

    await user.click(document.querySelector(".account-settings")!);
    const tabs = document.querySelectorAll<HTMLButtonElement>(".settings-layout nav button");
    await user.click(tabs[6]);
    const fontSize = screen.getByLabelText("字体大小");
    expect(fontSize).toHaveValue("1.3");
    await user.selectOptions(fontSize, "1.45");
    await user.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url) === "/api/v1/settings" && init?.method === "PUT");
      const payload = JSON.parse(String(call?.[1]?.body));
      expect(payload.appearance.font_scale).toBe(1.45);
    });
  });

  it("saves and checks LLM plus cloud TTS from one API panel", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("Audio", class { onended: null | (() => void) = null; async play() { this.onended?.(); } });
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:test-audio"), revokeObjectURL: vi.fn() });
    await renderReady();
    await user.click(document.querySelector(".account-settings")!);
    const tabs = document.querySelectorAll<HTMLButtonElement>(".settings-layout nav button");
    await user.click(tabs[4]);
    await user.selectOptions(screen.getByLabelText("TTS 链路"), "siliconflow");
    await user.click(tabs[0]);
    await user.type(screen.getByLabelText("新 TTS API 密钥（留空保持）"), "sk-tts-test");
    await user.click(screen.getByRole("button", { name: "自检 LLM + TTS API" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/v1/settings/test", expect.objectContaining({ method: "POST" })));
    expect(fetch).toHaveBeenCalledWith("/api/v1/audio/status", expect.objectContaining({ headers: expect.any(Object) }));
    expect(fetch).toHaveBeenCalledWith("/api/v1/audio/tts", expect.objectContaining({ method: "POST" }));
  });

  it("persists a local TTS route immediately and does not jump back to the API", async () => {
    const user = userEvent.setup();
    await renderReady();
    await user.click(document.querySelector(".account-settings")!);
    const tabs = document.querySelectorAll<HTMLButtonElement>(".settings-layout nav button");
    await user.click(tabs[4]);
    const route = screen.getByLabelText("TTS 链路");

    await user.selectOptions(route, "cosyvoice");
    expect(route).toHaveValue("cosyvoice");
    await waitFor(() => expect(screen.getByText("已切换并保存：本地 CosyVoice")).toBeInTheDocument());
    const call = vi.mocked(fetch).mock.calls.find(([url, init]) => {
      if (String(url) !== "/api/v1/settings" || init?.method !== "PUT") return false;
      return JSON.parse(String(init.body)).audio?.tts_provider === "cosyvoice";
    });
    expect(call).toBeDefined();

    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.click(document.querySelector(".account-settings")!);
    await user.click(document.querySelectorAll<HTMLButtonElement>(".settings-layout nav button")[4]);
    expect(screen.getByLabelText("TTS 链路")).toHaveValue("cosyvoice");
  });

  it("opens the reference inspector directly", async () => {
    await renderReady();
    fireEvent.click(document.querySelector(".composer-row div button:last-child")!);
    const tabs = document.querySelectorAll(".inspector-tabs button");
    expect(tabs[1]).toHaveClass("active");
    expect(document.querySelector(".context-list")).toBeInTheDocument();
  });

  it("streams an AI-initiative reply without rendering a synthetic user bubble", async () => {
    const user = userEvent.setup();
    await renderReady();

    await user.click(screen.getByRole("button", { name: /让 AI 说点什么/ }));

    expect(await screen.findByText("那我就陪你安静一会儿。")).toBeInTheDocument();
    expect(screen.queryByText("请求 AI 主动回复")).not.toBeInTheDocument();
    const call = vi.mocked(fetch).mock.calls.find(
      ([url, init]) => String(url) === "/api/v1/chat/stream" && init?.method === "POST",
    );
    const payload = JSON.parse(String(call?.[1]?.body));
    expect(payload.initiative).toBe(true);
    expect(payload.initiative_trigger).toBe("manual");
    expect(payload.interaction_mode).toBe("text");
    expect(payload.client_sent_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(typeof payload.client_timezone).toBe("string");
    expect(document.querySelectorAll(".message.user")).toHaveLength(0);
    expect(document.querySelectorAll(".message.assistant")).toHaveLength(1);
  });

  it("fires one pressure-free idle continuation in text mode", async () => {
    installFetchMock({ interaction: { idle_continuation_enabled: true, text_idle_seconds: 0.01, voice_idle_seconds: 30 } });
    const user = userEvent.setup();
    await renderReady();
    await user.type(screen.getByPlaceholderText("输入消息，或开启实时语音…"), "你好");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("那我就陪你安静一会儿。");

    await waitFor(() => {
      const calls = vi.mocked(fetch).mock.calls.filter(
        ([url, init]) => String(url) === "/api/v1/chat/stream" && init?.method === "POST",
      );
      expect(calls).toHaveLength(2);
      const payload = JSON.parse(String(calls[1][1]?.body));
      expect(payload.initiative).toBe(true);
      expect(payload.initiative_trigger).toBe("idle_continuation");
      expect(payload.interaction_mode).toBe("text");
    }, { timeout: 1800 });
    expect(document.querySelectorAll(".message.user")).toHaveLength(1);
  });

  it("exposes continuous companionship controls and voice-only progress", async () => {
    installFetchMock({ interaction: { ...settings.interaction, unlimited_reply_enabled: true, unlimited_reply_max_rounds: 12 } });
    const user = userEvent.setup();
    await renderReady();

    await user.click(document.querySelector(".account-settings")!);
    await user.click(screen.getByRole("button", { name: "陪伴频率" }));
    expect(screen.getByLabelText("无限制回复")).toBeChecked();
    expect(screen.getByLabelText("连续陪伴轮次上限")).toHaveValue(12);
    expect(screen.getByText(/衔接间隔固定为 10 秒/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.click(screen.getByRole("button", { name: /实时语音/ }));
    expect(await screen.findByText("0 / 12")).toBeInTheDocument();
    expect(screen.getByText(/可随时插话/)).toBeInTheDocument();
  });

  it("shows explainable memories and saves user edits", async () => {
    const user = userEvent.setup();
    await renderReady();
    await user.click(screen.getByRole("button", { name: /记忆中心/ }));
    expect(await screen.findByText("草莓")).toBeInTheDocument();
    expect(screen.getByText("为什么记住")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "修改" }));
    const input = document.querySelector<HTMLInputElement>(".memory-edit input")!;
    await user.clear(input);
    await user.type(input, "蓝莓");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/memory/items/"),
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ value: "蓝莓" }) }),
    ));
  });

  it("edits and saves the AI profile through the structured form", async () => {
    const user = userEvent.setup();
    await renderReady();
    await user.click(screen.getByRole("button", { name: /人物与状态档案/ }));
    await user.click(await screen.findByRole("button", { name: "AI 档案" }));
    const traits = await screen.findByLabelText("核心性格");
    fireEvent.change(traits, { target: { value: "可靠\n很容易满足" } });
    await user.click(screen.getByRole("button", { name: "保存档案" }));

    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url) === "/api/v1/profiles/assistant" && init?.method === "PUT");
      expect(call).toBeDefined();
      const payload = JSON.parse(String(call?.[1]?.body));
      expect(payload.personality.core_traits).toEqual(["可靠", "很容易满足"]);
    });
  });

  it("opens an editable profile directly from the user-owned character card", async () => {
    const user = userEvent.setup();
    await renderReady();
    await user.click(screen.getByRole("button", { name: "查看Mindspace人物卡" }));
    expect(await screen.findByText("AI 角色设定与当前关系状态")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑这份档案" }));
    expect(await screen.findByRole("button", { name: "AI 档案" })).toHaveClass("active");
    const description = await screen.findByLabelText("角色自述");
    await user.clear(description);
    await user.type(description, "用户可直接修改的角色档案");
    await user.click(screen.getByRole("button", { name: "保存档案" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/v1/profiles/assistant",
      expect.objectContaining({ method: "PUT" }),
    ));
  });

  it("closes the execution inspector and restores it from the top action", async () => {
    const user = userEvent.setup();
    await renderReady();
    expect(document.querySelector(".app-shell")).toHaveClass("inspector-visible");
    await user.click(screen.getByRole("button", { name: "关闭执行详情" }));
    expect(document.querySelector(".app-shell")).toHaveClass("inspector-hidden");
    expect(document.querySelector(".inspector")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "执行详情" }));
    expect(document.querySelector(".app-shell")).toHaveClass("inspector-visible");
    expect(document.querySelector(".inspector")).toBeVisible();
  });

  it("exposes TTS reference and dual avatar controls", async () => {
    await renderReady();
    fireEvent.click(document.querySelector(".account-settings")!);
    const nav = await waitFor(() => document.querySelectorAll(".settings-layout nav button"));
    expect(nav).toHaveLength(9);
    fireEvent.click(nav[4]);
    fireEvent.change(screen.getByLabelText("TTS 链路"), { target: { value: "cosyvoice" } });
    await screen.findByText("已切换并保存：本地 CosyVoice");
    const audioInput = document.querySelector<HTMLInputElement>('input[type="file"][accept*=".wav"]');
    expect(audioInput).toHaveAttribute("accept", expect.stringContaining(".flac"));
    expect(document.querySelector(".reference-panel")).toBeInTheDocument();
    fireEvent.click(nav[1]);
    expect(document.querySelectorAll(".avatar-editor-card")).toHaveLength(2);
    expect(document.querySelectorAll('.avatar-editor-card input[type="range"]')).toHaveLength(6);
  });

  it("edits the ASR vocabulary online without an LLM request", async () => {
    const user = userEvent.setup();
    await renderReady();
    await user.click(document.querySelector(".account-settings")!);
    const nav = document.querySelectorAll<HTMLButtonElement>(".settings-layout nav button");
    await user.click(nav[5]);
    await user.type(screen.getByLabelText("标准写法"), "长离");
    await user.type(screen.getByLabelText("常见误识别（逗号分隔）"), "长利,常离");
    await user.click(screen.getByRole("button", { name: "新增并立即生效" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/v1/audio/asr/vocabulary",
      expect.objectContaining({ method: "PUT" }),
    ));
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url) === "/api/v1/chat/stream")).toBe(false);
  });

  it("saves persistent read-only capability permissions without per-call prompts", async () => {
    const user = userEvent.setup();
    await renderReady();
    await user.click(document.querySelector(".account-settings")!);
    const nav = document.querySelectorAll<HTMLButtonElement>(".settings-layout nav button");
    await user.click(nav[8]);
    await user.click(screen.getByLabelText("允许联网搜索"));
    await user.click(screen.getByLabelText("允许实时热点"));
    await user.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([url, init]) => {
        if (String(url) !== "/api/v1/settings" || init?.method !== "PUT") return false;
        const payload = JSON.parse(String(init.body));
        return payload.capabilities?.web_search_enabled === true
          && payload.capabilities?.realtime_topics_enabled === true;
      });
      expect(call).toBeTruthy();
    });
    expect(screen.queryByText(/是否允许联网/)).not.toBeInTheDocument();
  });

  it("keeps the immersive voice page open when microphone startup fails", async () => {
    const user = userEvent.setup();
    await renderReady();
    await user.click(document.querySelector(".voice-entry")!);
    await waitFor(() => expect(document.querySelector(".voice-mode.phase-error")).toBeInTheDocument());
    expect(document.querySelector(".voice-exit")).toBeInTheDocument();
    fireEvent.click(document.querySelector(".voice-exit")!);
    expect(document.querySelector(".voice-mode")).not.toBeInTheDocument();
  });

  it("releases a microphone that resolves after voice mode has already closed", async () => {
    let resolveStream!: (stream: MediaStream) => void;
    const pendingStream = new Promise<MediaStream>((resolve) => { resolveStream = resolve; });
    const stopTrack = vi.fn();
    const originalMediaDevices = navigator.mediaDevices;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn(() => pendingStream) },
    });

    try {
      await renderReady();
      fireEvent.click(document.querySelector(".voice-entry")!);
      await waitFor(() => expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledOnce());
      fireEvent.click(document.querySelector(".voice-exit")!);
      expect(document.querySelector(".voice-mode")).not.toBeInTheDocument();

      resolveStream({ getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream);
      await waitFor(() => expect(stopTrack).toHaveBeenCalledOnce());
      expect(document.querySelector(".voice-mode")).not.toBeInTheDocument();
    } finally {
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: originalMediaDevices,
      });
    }
  });

  it("reattaches to the saved run after refresh and preserves a Core checkpoint", async () => {
    localStorage.setItem("mindspace.session", "recover-session");
    localStorage.setItem("mindspace.active_run", JSON.stringify({
      run_id: "recover-run",
      session_id: "recover-session",
      round: 1,
      user_content: "继续解释",
      started_at: "2026-07-23T00:00:00.000Z",
    }));
    const streamEnvelope = (event: string, seq: number, data: unknown) =>
      `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify({
        version: "1",
        event,
        seq,
        run_id: "recover-run",
        session_id: "recover-session",
        round: 1,
        timestamp: "2026-07-23T00:00:01.000Z",
        data,
      })}\n\n`;
    const fetchMock = vi.fn(async (
      input: RequestInfo | URL,
      _init: RequestInit = {},
    ) => {
      const url = String(input);
      if (url === "/api/v1/settings") return json(settings);
      if (url === "/api/v1/avatar/config") return json(avatarConfig);
      if (url === "/api/v1/sessions") {
        return json({
          sessions: [{
            session_id: "recover-session",
            title: "恢复测试",
            updated_at: "",
            message_count: 0,
          }],
        });
      }
      if (url === "/api/v1/sessions/recover-session") {
        return json({ session_id: "recover-session", title: "恢复测试", messages: [] });
      }
      if (url === "/api/v1/runs/recover-run/stream?after=0") {
        return new Response(
          streamEnvelope(
            "response.replace",
            1000043,
            { content: "已经生成的部分", reason: "process_recovery" },
          ) + streamEnvelope(
            "run.interrupted",
            1000044,
            { partial_text: "已经生成的部分", reason: "core_restarted" },
          ),
          { headers: { "Content-Type": "text/event-stream" } },
        );
      }
      return json({ detail: `unexpected request ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    await renderReady();

    expect(await screen.findByText("已经生成的部分")).toBeInTheDocument();
    expect(screen.getByText(/回答在此处中断/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/runs/recover-run/stream?after=0",
      expect.objectContaining({ headers: { "Last-Event-ID": "0" } }),
    );
    expect(fetchMock.mock.calls.some(([url, init]) =>
      String(url) === "/api/v1/chat/stream" && init?.method === "POST"
    )).toBe(false);
    expect(localStorage.getItem("mindspace.active_run")).toBeNull();
  });

  it("deletes only the AI reply and schedules JSON reconciliation", async () => {
    localStorage.setItem("mindspace.session", "s1");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input);
      if (url === "/api/v1/settings") return json(settings);
      if (url === "/api/v1/avatar/config") return json(avatarConfig);
      if (url === "/api/v1/sessions") {
        return json({ sessions: [{ session_id: "s1", title: "测试", updated_at: "", message_count: 2 }] });
      }
      if (url === "/api/v1/sessions/s1") {
        return json({
          session_id: "s1",
          title: "测试",
          messages: [
            { message_id: "u1", role: "user", content: "用户原话", round: 1 },
            { message_id: "a1", role: "assistant", content: "待删除回复", round: 1 },
          ],
        });
      }
      if (url === "/api/v1/sessions/s1/messages/a1" && init.method === "DELETE") {
        return json({ success: true, deletion_event_id: "d1", pending_json_reconciliation: true });
      }
      return json({ detail: `unexpected request ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    await renderReady();
    await screen.findByText("待删除回复");
    await userEvent.click(screen.getByRole("button", { name: "删除回复" }));

    await waitFor(() => expect(screen.queryByText("待删除回复")).not.toBeInTheDocument());
    expect(screen.getByText("用户原话")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/sessions/s1/messages/a1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

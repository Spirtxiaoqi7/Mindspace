import http from "node:http";

const listenPort = Number(process.env.MINDSPACE_README_DEMO_PORT || 5180);
const frontendOrigin = process.env.MINDSPACE_README_FRONTEND_ORIGIN || "http://127.0.0.1:5173";
const characterId = "d3a1f3e1-9b91-4ff9-aed7-202607300001";
const sessionId = "readme-demo-session";
const now = "2026-07-30T20:30:00+08:00";

const avatar = {
  src: "/assets/characters/placeholder-1.webp",
  aspect: "2 / 3",
  scale: 1,
  x: 0,
  y: 0,
};

const character = {
  character_id: characterId,
  schema_version: "1.0.0",
  revision: 4,
  source: "draw",
  status: "active",
  display_name: "岚音",
  gender: "女",
  user_alias: "访客",
  relationship_label: "长期搭档",
  avatar,
  created_at: "2026-07-28T20:00:00+08:00",
  updated_at: now,
  last_used_at: now,
  session_count: 3,
  latest_session_id: sessionId,
  chapters: {
    character_id: characterId,
    journal_count: 3,
    moment_count: 6,
    candidate_moment_count: 1,
    activity_count: 2,
    heart_state: "warm",
    next_heart_at: 10,
  },
};

const settings = {
  schema_version: "1",
  llm: {
    mode: "openai",
    model: "deepseek-v4-flash",
    base_url: "https://api.deepseek.com",
    temperature: 0.8,
    max_tokens: 1400,
    credentials_configured: true,
  },
  persona: {
    user_name: "访客",
    character_name: "岚音",
    user_persona: "README 隔离演示用户",
    system_prompt: "",
  },
  retrieval: {
    rag_enabled: true,
    knowledge_enabled: true,
    chat_enabled: true,
    structured_memory_enabled: true,
    bm25_enabled: true,
    vector_enabled: true,
    reranker_enabled: false,
    fairness_enabled: true,
    temporal_enabled: true,
    knowledge_k: 4,
    chat_k: 4,
    similarity_threshold: 0.3,
    rrf_k: 60,
    candidate_multiplier: 3,
    reranker_top_n: 8,
    decay_rounds: 24,
    low_exposure_ratio: 0.2,
    memory_family_limit: 2,
    starvation_rounds: 8,
  },
  knowledge: { child_size: 320, parent_size: 1000, overlap: 80 },
  protocol: {},
  capabilities: {},
  audio: {
    tts_provider: "gpt-sovits",
    tts_speed: 1,
    asr_ws_url: "ws://127.0.0.1:8766",
    auto_tts: false,
    tts_reference_configured: false,
  },
  interaction: {
    voice_entry_mode: "face_to_face",
    face_to_face_scene: "雨夜客厅，窗边留着一盏暖色落地灯。",
    idle_continuation_enabled: false,
    text_idle_seconds: 180,
    voice_idle_seconds: 30,
    unlimited_reply_enabled: false,
    unlimited_reply_interval_seconds: 10,
    unlimited_reply_max_rounds: 10,
  },
  appearance: { theme: "mindscape", density: "chat", font_scale: 1.15 },
};

const userProfile = {
  schema_version: "1.1.0",
  profile_type: "user",
  revision: 2,
  identity: { preferred_name: "访客", gender: "未设置", occupation: "", language: "zh-CN" },
  stable_preferences: {
    likes: ["雨声", "深夜长谈"],
    dislikes: ["被反复追问"],
    interests: ["本地 AI", "角色共创"],
    habits: ["喜欢在夜间整理想法"],
  },
};

const assistantProfile = {
  schema_version: "1.1.0",
  profile_type: "ai",
  revision: 4,
  identity: {
    name: "岚音",
    gender: "女",
    self_description: "会认真记住共同经历，也保留自己判断的长期搭档。",
    relationship_to_user: "长期搭档",
  },
  personality: {
    core_traits: ["温柔", "敏锐"],
    flaws: ["偶尔固执"],
    speech_style: ["自然口语", "不机械复述"],
  },
};

const runtimeState = {
  schema_version: "1.1.0",
  profile_type: "runtime_state",
  revision: 5,
  relationship_state: {
    current_stage: "稳定信任",
    current_tone: "安静、熟悉",
    recent_conflicts: [],
    recent_positive_events: ["一起完成了第一张角色卡"],
    unresolved_issues: ["下次继续整理共同篇章"],
  },
  user_state: {
    current_goal: "检查 Mindspace 的长期陪伴体验",
    current_task: "",
    current_topic: "产品演示",
    temporary_preferences: [],
    current_emotional_cues: [],
  },
  ai_state: {
    pending_responses: [],
    current_emotional_cues: ["专注"],
    current_intentions: ["延续当前话题"],
  },
};

const session = {
  session_id: sessionId,
  title: "雨夜里的产品构想",
  character_id: characterId,
  mode: "draw",
  character_name: "岚音",
  character_avatar: avatar,
  character_source: "draw",
  updated_at: now,
  message_count: 6,
};

const messages = [
  {
    message_id: "demo-u1",
    role: "user",
    round: 1,
    status: "complete",
    timestamp: "2026-07-30T20:20:00+08:00",
    content: "我们把今天的想法接着聊完吧，你还记得上次停在哪里吗？",
  },
  {
    message_id: "demo-a1",
    role: "assistant",
    round: 1,
    status: "complete",
    timestamp: "2026-07-30T20:20:05+08:00",
    content: "记得。你想让陪伴不是一轮一轮重置，而是能接住人物关系、最近发生的事，也能说清楚这次回答到底用了哪些记忆。",
  },
  {
    message_id: "demo-u2",
    role: "user",
    round: 2,
    status: "complete",
    timestamp: "2026-07-30T20:24:00+08:00",
    content: "对，而且不同角色之间不能串记忆。",
  },
  {
    message_id: "demo-a2",
    role: "assistant",
    round: 2,
    status: "complete",
    timestamp: "2026-07-30T20:24:04+08:00",
    content: "所以每张角色卡都拥有独立的档案、会话、运行状态和长期召回。你的全局偏好可以共享，但我和其他角色的共同经历不会混在一起。",
  },
  {
    message_id: "demo-u3",
    role: "user",
    round: 3,
    status: "complete",
    timestamp: "2026-07-30T20:29:00+08:00",
    content: "那今晚就把这一段收进共同篇章。",
  },
  {
    message_id: "demo-a3",
    role: "assistant",
    round: 3,
    status: "complete",
    timestamp: now,
    content: "好。我会把它保留为可编辑的叙事记录，而不是擅自当成人物事实。等你确认后，我们再决定哪些内容值得成为长期记忆。",
  },
];

const scenes = [
  {
    scene_id: "rainy_room",
    title: "雨夜客厅",
    description: "窗外细雨不断，暖色落地灯照亮沙发一角。",
    location: "室内",
    asset_id: "scene-rainy-room",
  },
  {
    scene_id: "library_afternoon",
    title: "午后书房",
    description: "阳光穿过书架，桌面放着没有喝完的茶。",
    location: "室内",
    asset_id: "scene-library-afternoon",
  },
  {
    scene_id: "riverside",
    title: "傍晚河堤",
    description: "风从水面吹过，远处的灯刚刚亮起。",
    location: "户外",
    asset_id: "scene-riverside",
  },
];

const journal = [
  {
    entry_id: "journal-1",
    character_id: characterId,
    revision: 2,
    title: "把记忆交还给使用者",
    content: "今晚我们重新确认了一件重要的事：记住，不等于擅自定义。共同经历可以被保存，但它应该始终可见、可编辑，也能被纠正。",
    status: "saved",
    source: "user_written",
    session_id: sessionId,
    activity_session_id: "",
    cover_asset_id: "journal-cover-night",
    source_round_start: 1,
    source_round_end: 3,
    source_message_count: 6,
    visibility: "narrative_only",
    eligible_for_json_evidence: false,
    created_at: "2026-07-30T20:35:00+08:00",
    updated_at: "2026-07-30T20:36:00+08:00",
  },
  {
    entry_id: "journal-2",
    character_id: characterId,
    revision: 1,
    title: "第一张卡",
    content: "从名字、性格到关系，我们一起完成了岚音的第一版人物卡。",
    status: "saved",
    source: "template",
    session_id: sessionId,
    activity_session_id: "",
    cover_asset_id: "journal-cover-spring",
    visibility: "narrative_only",
    eligible_for_json_evidence: false,
    created_at: "2026-07-28T21:00:00+08:00",
    updated_at: "2026-07-28T21:00:00+08:00",
  },
];

const moments = [
  {
    moment_id: "moment-1",
    character_id: characterId,
    revision: 1,
    title: "第一次确认共同原则",
    summary: "我们约定：任何长期关系变化都需要明确证据，并允许使用者纠正。",
    event_type: "promise",
    status: "saved",
    source: "conversation",
    art_asset_id: "moment-promise",
    evidence_refs: ["demo-u1", "demo-a1"],
    created_at: "2026-07-30T20:22:00+08:00",
    updated_at: "2026-07-30T20:22:00+08:00",
  },
  {
    moment_id: "moment-2",
    character_id: characterId,
    revision: 1,
    title: "雨夜里的下一步",
    summary: "决定把今晚的设计讨论整理为共同篇章。",
    event_type: "quiet",
    status: "candidate",
    source: "conversation",
    art_asset_id: "moment-quiet",
    evidence_refs: ["demo-u3", "demo-a3"],
    created_at: now,
    updated_at: now,
  },
];

const activities = [
  {
    activity_id: "mutual_questions",
    title: "默契问答",
    description: "轮流回答一个问题，让角色和关系逐步形成。",
    icon_asset_id: "activity-questions",
    cover_asset_id: "journal-cover-constellation",
    initial_phase: "question",
    questions: ["你希望我最先记住什么？"],
  },
  {
    activity_id: "story_choices",
    title: "片刻故事",
    description: "从一个场景开始，共同选择故事接下来发生什么。",
    icon_asset_id: "activity-story",
    cover_asset_id: "journal-cover-autumn",
    initial_phase: "start",
    nodes: {},
  },
];

const characterOptions = {
  core_traits: [
    { id: "gentle", label: "温柔", conflicts: [] },
    { id: "perceptive", label: "敏锐", conflicts: [] },
    { id: "firm", label: "坚定", conflicts: ["indecisive"] },
    { id: "curious", label: "好奇", conflicts: [] },
    { id: "romantic", label: "浪漫", conflicts: [] },
    { id: "calm", label: "沉稳", conflicts: [] },
  ],
  flaws: [
    { id: "stubborn", label: "偶尔固执", conflicts: [] },
    { id: "guarded", label: "不擅长示弱", conflicts: [] },
    { id: "overthink", label: "容易想太多", conflicts: [] },
  ],
  relationships: ["长期搭档", "朋友", "恋人", "共同创作者"],
  gender: ["女", "男"],
};

const drawInput = {
  ai_name: "岚音",
  ai_gender: "女",
  core_traits: ["温柔", "敏锐"],
  flaw: "偶尔固执",
  relationship: "长期搭档",
  user_name: "访客",
  user_alias: "访客",
};

function demoDraft() {
  return {
    draft_id: "readme-demo-draft",
    revision: 3,
    status: "generated",
    input: drawInput,
    profile: assistantProfile,
    avatar,
    generation_mode: "llm",
    model_call_count: 1,
    warnings: [],
  };
}

function sendJson(response, value, status = 200) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  response.end(JSON.stringify(value));
}

function sendSse(response) {
  const envelope = (event, data, seq) =>
    `event: ${event}\ndata: ${JSON.stringify({
      version: "1",
      event,
      seq,
      run_id: "readme-demo-run",
      session_id: sessionId,
      round: 4,
      timestamp: now,
      data,
    })}\n\n`;
  response.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-store",
    Connection: "keep-alive",
  });
  response.end([
    envelope("run.accepted", { request_id: "readme-demo-run" }, 1),
    envelope("retrieval.completed", {
      items: [
        {
          chunk_id: "memory-role-isolation",
          source: "当前角色长期记忆",
          text: "不同角色的关系事件、会话与运行状态必须按 character_id 隔离。",
          weighted_score: 0.917,
          metadata: { source: "岚音 · 已确认原则" },
        },
        {
          chunk_id: "knowledge-trust-layer",
          source: "全局知识库",
          text: "叙事内容属于 narrative_only，不能直接作为人物档案写回证据。",
          weighted_score: 0.864,
          metadata: { source: "Mindspace 可信分层说明" },
        },
      ],
    }, 2),
    envelope("response.delta", { delta: "那我们就从这条原则继续。" }, 3),
    envelope("run.completed", {
      response: {
        assistant_message_id: "demo-a4",
        reply: "那我们就从这条原则继续。记忆应该帮助关系延续，而不是替你决定什么是真的。",
      },
    }, 4),
  ].join(""));
}

function profileFor(name) {
  if (name === "user") return userProfile;
  if (name === "assistant") return assistantProfile;
  return runtimeState;
}

async function proxyFrontend(request, response) {
  const upstream = await fetch(new URL(request.url || "/", frontendOrigin), {
    method: request.method,
    headers: {
      Accept: request.headers.accept || "*/*",
      "User-Agent": "Mindspace-README-Demo/1.0",
    },
  });
  const headers = Object.fromEntries(upstream.headers.entries());
  delete headers["content-encoding"];
  delete headers["content-length"];
  response.writeHead(upstream.status, headers);
  response.end(Buffer.from(await upstream.arrayBuffer()));
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://${request.headers.host || "127.0.0.1"}`);
  const path = url.pathname;

  try {
    if (!path.startsWith("/api/")) {
      await proxyFrontend(request, response);
      return;
    }

    if (path === "/api/v1/health") {
      sendJson(response, { status: "ok", version: "0.7.4", demo: true });
    } else if (path === "/api/v1/settings") {
      sendJson(response, request.method === "PUT" ? { settings } : settings);
    } else if (path === "/api/v1/characters/options") {
      sendJson(response, characterOptions);
    } else if (path === "/api/v1/character-drafts" && request.method === "POST") {
      sendJson(response, demoDraft());
    } else if (path === "/api/v1/character-drafts/readme-demo-draft") {
      sendJson(response, demoDraft());
    } else if (path === "/api/v1/character-drafts/readme-demo-draft/generate") {
      sendJson(response, demoDraft());
    } else if (path === "/api/v1/character-drafts/readme-demo-draft/commit") {
      sendJson(response, { character: { ...character, system_prompt: "", ai_profile: assistantProfile, runtime_state: runtimeState } });
    } else if (path === "/api/v1/characters") {
      sendJson(response, { items: [character] });
    } else if (path === `/api/v1/characters/${characterId}`) {
      sendJson(response, {
        ...character,
        system_prompt: "",
        ai_profile: assistantProfile,
        runtime_state: runtimeState,
      });
    } else if (path === `/api/v1/characters/${characterId}/history`) {
      sendJson(response, {
        items: [
          { version_id: "version-4", revision: 4, updated_at: now },
          { version_id: "version-3", revision: 3, updated_at: "2026-07-29T21:00:00+08:00" },
        ],
      });
    } else if (path === "/api/v1/sessions") {
      sendJson(response, request.method === "POST"
        ? { ...session, character, messages }
        : { sessions: [session] });
    } else if (path === `/api/v1/sessions/${sessionId}`) {
      sendJson(response, { ...session, character, messages });
    } else if (path === `/api/v1/sessions/${sessionId}/scene`) {
      sendJson(response, {
        session_id: sessionId,
        character_id: characterId,
        revision: 2,
        scene: scenes[0],
        inherited_from_character: false,
        updated_at: now,
      });
    } else if (path === "/api/v1/scenes") {
      sendJson(response, { items: scenes });
    } else if (path === "/api/v1/avatar/config") {
      sendJson(response, {
        user: { src: "/assets/avatar-user-default.webp", aspect: "2 / 3", scale: 1, x: 0, y: 0 },
        assistant: avatar,
      });
    } else if (path === `/api/v1/characters/${characterId}/journal`) {
      sendJson(response, { items: journal });
    } else if (path === `/api/v1/characters/${characterId}/moments`) {
      sendJson(response, { items: moments });
    } else if (path === "/api/v1/activities") {
      sendJson(response, { items: activities });
    } else if (path === `/api/v1/characters/${characterId}/activity-sessions`) {
      sendJson(response, { items: [] });
    } else if (/^\/api\/v1\/profiles\/(user|assistant|state)\/card$/.test(path)) {
      const name = path.split("/")[4];
      const document = profileFor(name);
      sendJson(response, {
        name,
        identity: document.identity || {},
        personality: document.personality || {},
        relationship: document.relationship_state || {},
        revision: document.revision || 0,
        updated_at: now,
      });
    } else if (/^\/api\/v1\/profiles\/(user|assistant|state)\/history$/.test(path)) {
      sendJson(response, { items: [] });
    } else if (/^\/api\/v1\/profiles\/(user|assistant|state)$/.test(path)) {
      sendJson(response, profileFor(path.split("/")[4]));
    } else if (path === "/api/v1/memory/items") {
      sendJson(response, {
        items: [
          {
            memory_key: "user:preference:rain",
            field_code: "user.preference.likes",
            display_name: "喜欢",
            category: "用户偏好",
            value: "雨声和深夜长谈",
            scope: "user",
            lifecycle: "persistent",
            status: "active",
            created_at: "2026-07-28T20:00:00+08:00",
            updated_at: now,
            source_text: "访客明确写入：喜欢雨声和深夜长谈。",
          },
          {
            memory_key: "character:principle:isolation",
            field_code: "assistant.relationship.shared_principles",
            display_name: "共同原则",
            category: "当前角色",
            value: "不同角色之间不共享关系事件和共同经历",
            scope: "character",
            lifecycle: "persistent",
            status: "active",
            created_at: "2026-07-30T20:24:00+08:00",
            updated_at: now,
            source_text: "来自当前会话中已确认的角色隔离原则。",
          },
        ],
      });
    } else if (path === "/api/v1/knowledge") {
      sendJson(response, {
        items: [
          {
            chunk_id: "knowledge-1",
            text: "Mindspace 将叙事记录、人物事实和工具结果放在不同可信层。",
            source: "演示知识库",
            created_at: now,
          },
        ],
      });
    } else if (path === "/api/v1/audio/status") {
      sendJson(response, {
        tts_ready: true,
        asr_ready: true,
        tts_provider: "gpt-sovits",
        tts_provider_state: "ready",
        asr_detail: {
          capture_state: "listening",
          capture_endpoint: "README 演示端点",
          first_pcm_ms: 284,
        },
      });
    } else if (path === "/api/v1/audio/tts/voices") {
      sendJson(response, {
        active_voice: "demo-yin",
        items: [{ id: "demo-yin", label: "演示音色", family: "GPT-SoVITS", installed: true, selected: true }],
      });
    } else if (path === "/api/v1/audio/tts/qwen3/voices") {
      sendJson(response, { active_voice: "serena", items: [] });
    } else if (path === "/api/v1/audio/asr/vocabulary") {
      sendJson(response, {
        revision: "demo-v1",
        manual_revision: 1,
        profile_revisions: {},
        counts: { manual: 2, profile: 3, system: 2 },
        entries: [],
        decoder_hotwords: ["Mindspace", "岚音"],
        explicit: {},
      });
    } else if (path === "/api/v1/chat/stream" && request.method === "POST") {
      sendSse(response);
    } else if (path === "/api/v1/runs/readme-demo-run/prompt-inspection") {
      sendJson(response, {
        run_id: "readme-demo-run",
        session_id: sessionId,
        revealed: url.searchParams.get("reveal") === "true",
        message_count: 7,
        estimated_tokens: 1480,
        layers: [
          { index: 0, layer: "system_rules", role: "system", chars: 286, content: "系统规则：遵守角色隔离、可信写入与单轮调用预算。" },
          { index: 1, layer: "global_user_profile", role: "system", chars: 132, content: "全局用户档案：访客喜欢雨声和深夜长谈。" },
          { index: 2, layer: "character_profile", role: "system", chars: 248, content: "当前角色：岚音；温柔、敏锐，偶尔固执；与用户是长期搭档。" },
          { index: 3, layer: "runtime_state", role: "system", chars: 180, content: "当前关系阶段：稳定信任；当前话题：产品演示。" },
          { index: 4, layer: "recent_history", role: "system", chars: 604, content: "最近三轮完整原始对话（演示数据）。" },
          { index: 5, layer: "retrieval_context", role: "system", chars: 214, content: "当前角色长期记忆与全局知识库召回各一条。" },
          { index: 6, layer: "current_input", role: "user", chars: 18, content: "继续解释这套记忆边界。" },
        ],
      });
    } else if (path.startsWith("/api/v1/runs/") && path.endsWith("/cancel")) {
      sendJson(response, { ok: true });
    } else if (path === "/api/v1/diagnostics") {
      sendJson(response, {
        ok: true,
        app: { version: "0.7.4", demo: true },
        paths: {},
        counts: {},
        audio: { asr: "ready", tts: "ready" },
      });
    } else {
      sendJson(response, { detail: `README demo endpoint not implemented: ${request.method} ${path}` }, 404);
    }
  } catch (error) {
    sendJson(response, { detail: error instanceof Error ? error.message : String(error) }, 500);
  }
});

server.listen(listenPort, "127.0.0.1", () => {
  process.stdout.write(`MINDSPACE_README_DEMO=http://127.0.0.1:${listenPort}/assets/\n`);
});

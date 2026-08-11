import { useEffect, useRef, useState } from "react";
import { getAudioStatus, rawRequest, request } from "../shared/api";
import { Field } from "../shared/Field";
import type { ASRVocabularyEntry, ASRVocabularySnapshot, AvatarConfig, AvatarEntry, ProductSettings, Role } from "../types";
import { styledConfirm } from "../ui/styledConfirm";
import { avatarStyle, DEFAULT_AVATARS, normalizeAvatarConfig } from "../ui/avatar";
import { Modal } from "./Modal";

const uid = () => crypto.randomUUID();
const bool = (value: unknown) => Boolean(value);
const num = (value: unknown, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const str = (value: unknown) => String(value ?? "");
type LlmProviderOption = { id: string; label: string; base_url: string; models: string[]; requires_key?: boolean; custom?: boolean };
function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

function encodeMonoWav(samples: Float32Array, sampleRate: number) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  write(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); write(8, "WAVE");
  write(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => view.setInt16(44 + index * 2, Math.round(Math.max(-1, Math.min(1, sample)) * 32767), true));
  return buffer;
}

async function normalizeReferenceAudio(file: File) {
  const context = new AudioContext();
  let decoded: AudioBuffer;
  try { decoded = await context.decodeAudioData(await file.arrayBuffer()); } finally { await context.close(); }
  if (decoded.duration < 0.2) throw new Error("参考音频过短，至少需要 0.2 秒");
  if (decoded.duration > 120) throw new Error("参考音频过长，请裁剪到 120 秒以内");
  const sampleRate = 16000;
  const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * sampleRate), sampleRate);
  const source = offline.createBufferSource(); source.buffer = decoded; source.connect(offline.destination); source.start();
  const rendered = await offline.startRendering();
  const name = `${file.name.replace(/\.[^.]+$/, "") || "reference"}.wav`;
  return new File([encodeMonoWav(rendered.getChannelData(0), sampleRate)], name, { type: "audio/wav" });
}

function SelectField({ label, value, options, onChange, disabled = false }: { label: string; value: unknown; options: [string, string][]; onChange: (value: string) => void; disabled?: boolean }) {
  return <label className="field"><span>{label}</span><select value={str(value)} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{options.map(([id, name]) => <option value={id} key={id}>{name}</option>)}</select></label>;
}

function AvatarEditor({ role, entry, onChange, onUpload, busy }: { role: Role; entry: AvatarEntry; onChange: (entry: AvatarEntry) => void; onUpload: (file: File) => void; busy: boolean }) {
  const label = role === "assistant" ? "AI 头像" : "用户头像";
  return <article className="avatar-editor-card"><div className="avatar-editor-head"><div className="avatar-preview portrait-avatar" style={avatarStyle(entry)}><img src={entry.src} alt={`${label}预览`} /></div><div><strong>{label}</strong><small>{role === "assistant" ? "聊天与语音页面中的角色形象" : "聊天消息中的用户形象"}</small></div></div><div className="avatar-editor-actions"><label className="secondary upload-button">{busy ? "上传中…" : "上传图片"}<input hidden disabled={busy} type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload(file); event.currentTarget.value = ""; }} /></label><button className="secondary" onClick={() => onChange(DEFAULT_AVATARS[role])}>恢复默认</button></div><div className="avatar-controls"><SelectField label="头像比例" value={entry.aspect} options={[["2 / 3", "2:3 竖屏"], ["3 / 4", "3:4 竖屏"], ["4 / 5", "4:5 竖屏"], ["9 / 16", "9:16 长屏"], ["1 / 1", "1:1 方形"]]} onChange={(value) => onChange({ ...entry, aspect: value as AvatarEntry["aspect"] })} /><label>缩放 <b>{entry.scale.toFixed(2)}x</b><input type="range" min="0.6" max="3" step="0.01" value={entry.scale} onChange={(event) => onChange({ ...entry, scale: Number(event.target.value) })} /></label><label>横移 <b>{entry.x}%</b><input type="range" min="-80" max="80" value={entry.x} onChange={(event) => onChange({ ...entry, x: Number(event.target.value) })} /></label><label>纵移 <b>{entry.y}%</b><input type="range" min="-80" max="80" value={entry.y} onChange={(event) => onChange({ ...entry, y: Number(event.target.value) })} /></label></div></article>;
}

export function SettingsWorkspace({ value, avatars, initialTab = "model", onClose, onDirty, onOpenProfile, onOpenMemory, onOpenKnowledge, onOpenDiagnostics, onSaved, onSettingsChange, onAvatarsChange, notify }: { value: ProductSettings; avatars: AvatarConfig; initialTab?: string; onClose: () => void; onDirty: (dirty: boolean) => void; onOpenProfile: (role: Role) => void; onOpenMemory: () => void; onOpenKnowledge: () => void; onOpenDiagnostics: () => void; onSaved: (value: ProductSettings, avatars: AvatarConfig) => void; onSettingsChange: (value: ProductSettings) => void; onAvatarsChange: (value: AvatarConfig) => void; notify: (message: string) => void }) {
  const normalizedValue: ProductSettings = {
    ...structuredClone(value),
    llm: { provider: "custom", ...structuredClone(value.llm) },
    audio: {
      asr_listening_energy_threshold_db: -50,
      asr_listening_min_speech_ms: 120,
      asr_barge_in_energy_threshold_db: -38,
      asr_barge_in_min_speech_ms: 300,
      asr_candidate_release_ms: 280,
      asr_adaptive_noise_enabled: false,
      asr_noise_calibration_ms: 1500,
      asr_listening_noise_margin_db: 10,
      asr_barge_in_noise_margin_db: 16,
      asr_utterance_merge_ms: 1100,
      asr_deferred_during_playback: true,
      asr_hotwords_enabled: true,
      asr_dynamic_endpointing: true,
      asr_final_refinement_enabled: true,
      ...structuredClone(value.audio),
    },
    interaction: {
      idle_continuation_enabled: false,
      text_idle_seconds: 180,
      voice_idle_seconds: 30,
      unlimited_reply_enabled: false,
      unlimited_reply_interval_seconds: 10,
      unlimited_reply_max_rounds: 10,
      ...structuredClone(value.interaction || {}),
    },
    capabilities: {
      master_enabled: true,
      local_knowledge_enabled: true,
      web_search_enabled: true,
      realtime_topics_enabled: false,
      topic_expansion_enabled: true,
      proactive_hotspots_enabled: false,
      show_sources_enabled: true,
      web_timeout_seconds: 12,
      max_web_results: 10,
      max_web_pages: 6,
      max_web_content_chars: 12000,
      ...structuredClone(value.capabilities || {}),
    },
  };
  const [draft, setDraft] = useState<ProductSettings>(normalizedValue);
  const [avatarDraft, setAvatarDraft] = useState<AvatarConfig>(structuredClone(avatars));
  const [tab, setTab] = useState(initialTab);
  const [audioBusy, setAudioBusy] = useState("");
  const [audioStatus, setAudioStatus] = useState(bool(value.audio.tts_reference_configured) ? `已配置参考音频：${str(value.audio.tts_reference_name)}` : "尚未上传参考音频");
  const [providerBusy, setProviderBusy] = useState(false);
  const [providerStatus, setProviderStatus] = useState("切换链路后立即保存，无需再点击底部保存按钮");
  const [gptVoices, setGptVoices] = useState<{ active_voice: string; items: Array<{ id: string; label: string; family: string; installed: boolean; selected: boolean }> }>({ active_voice: "v4-changli", items: [] });
  const [qwenVoices, setQwenVoices] = useState<{ active_voice: string; items: Array<{ id: string; label: string; installed: boolean; selected: boolean }> }>({ active_voice: "serena", items: [] });
  const [avatarBusy, setAvatarBusy] = useState<Role | "">("");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [llmProviders, setLlmProviders] = useState<LlmProviderOption[]>([]);
  const [availableModels, setAvailableModels] = useState<string[]>([str(normalizedValue.llm.model)].filter(Boolean));
  const [llmModelBusy, setLlmModelBusy] = useState(false);
  const [llmModelStatus, setLlmModelStatus] = useState("选择供应商后获取实时模型列表，也可以直接填写模型 ID");
  const [ttsApiKey, setTtsApiKey] = useState("");
  const [vocabulary, setVocabulary] = useState<ASRVocabularySnapshot | null>(null);
  const [vocabularyBusy, setVocabularyBusy] = useState(false);
  const [vocabularyQuery, setVocabularyQuery] = useState("");
  const [vocabularyTerm, setVocabularyTerm] = useState("");
  const [vocabularyAliases, setVocabularyAliases] = useState("");
  const [vocabularyPriority, setVocabularyPriority] = useState<ASRVocabularyEntry["priority"]>("high");
  const [vocabularyTest, setVocabularyTest] = useState("");
  const [vocabularyTestResult, setVocabularyTestResult] = useState("");
  const initial = useRef(JSON.stringify({ value: normalizedValue, avatars }));
  const update = (group: keyof ProductSettings, key: string, next: unknown) => setDraft((current) => ({ ...current, [group]: { ...(current[group] as unknown as Record<string, unknown>), [key]: next } }));
  const dirty = Boolean(llmApiKey || ttsApiKey) || JSON.stringify({ value: draft, avatars: avatarDraft }) !== initial.current;
  const externalAudioSelection = JSON.stringify({
    provider: value.audio.tts_provider,
    gpt: value.audio.tts_gpt_sovits_voice,
    qwen: value.audio.tts_qwen3_vllm_voice,
    auto: value.audio.auto_tts,
  });
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  useEffect(() => {
    if (providerBusy) return;
    const externalAudio = {
      tts_provider: value.audio.tts_provider,
      tts_gpt_sovits_voice: value.audio.tts_gpt_sovits_voice,
      tts_qwen3_vllm_voice: value.audio.tts_qwen3_vllm_voice,
      auto_tts: value.audio.auto_tts,
    };
    setDraft((current) => ({ ...current, audio: { ...current.audio, ...externalAudio } }));
    const baseline = JSON.parse(initial.current) as { value: ProductSettings; avatars: AvatarConfig };
    baseline.value = { ...baseline.value, audio: { ...baseline.value.audio, ...externalAudio } };
    initial.current = JSON.stringify(baseline);
  }, [externalAudioSelection, providerBusy, value.audio]);
  useEffect(() => {
    request<{ providers: LlmProviderOption[] }>("/api/v1/models/providers")
      .then(({ providers }) => {
        setLlmProviders(providers);
        setDraft((current) => {
          const selected = providers.find((item) => item.id === current.llm.provider)
            || providers.find((item) => item.base_url.replace(/\/$/, "") === str(current.llm.base_url).replace(/\/$/, ""))
            || providers.find((item) => item.id === "custom");
          return selected ? { ...current, llm: { ...current.llm, provider: selected.id } } : current;
        });
      })
      .catch(() => undefined);
    request<{ active_voice: string; items: Array<{ id: string; label: string; family: string; installed: boolean; selected: boolean }> }>("/api/v1/audio/tts/voices")
      .then(setGptVoices)
      .catch(() => undefined);
    request<{ active_voice: string; items: Array<{ id: string; label: string; installed: boolean; selected: boolean }> }>("/api/v1/audio/tts/qwen3/voices")
      .then(setQwenVoices)
      .catch(() => undefined);
    request<ASRVocabularySnapshot>("/api/v1/audio/asr/vocabulary")
      .then(setVocabulary)
      .catch(() => undefined);
  }, []);

  const saveManualVocabulary = async (entries: ASRVocabularyEntry[]) => {
    setVocabularyBusy(true);
    try {
      const result = await request<ASRVocabularySnapshot>("/api/v1/audio/asr/vocabulary", {
        method: "PUT",
        body: JSON.stringify({ entries: entries.map((item) => ({
          id: item.id, term: item.term, aliases: item.aliases, priority: item.priority,
          scope: item.scope, category: item.category, source_field: item.source_field,
          enabled: item.enabled, hit_count: item.hit_count, updated_at: item.updated_at,
        })) }),
      });
      setVocabulary(result);
      notify("识别词表已更新，下一段语音立即生效");
    } catch (error) {
      notify((error as Error).message);
    } finally {
      setVocabularyBusy(false);
    }
  };
  const addVocabularyEntry = async () => {
    const term = vocabularyTerm.trim();
    if (!term) { notify("请填写标准写法"); return; }
    const manual = (vocabulary?.entries || []).filter((item) => item.source === "manual");
    if (manual.some((item) => item.term.toLowerCase() === term.toLowerCase())) { notify("这个标准词已经存在"); return; }
    const entry: ASRVocabularyEntry = {
      id: uid(), term, aliases: vocabularyAliases.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean),
      priority: vocabularyPriority, weight: vocabularyPriority === "critical" ? 100 : vocabularyPriority === "high" ? 90 : vocabularyPriority === "medium" ? 65 : 30,
      scope: "global", category: "个人词表", source: "manual", source_field: "", enabled: true,
      hit_count: 0, updated_at: new Date().toISOString(), read_only: false,
    };
    await saveManualVocabulary([...manual, entry]);
    setVocabularyTerm(""); setVocabularyAliases("");
  };
  const testVocabulary = async () => {
    if (!vocabularyTest.trim()) return;
    setVocabularyBusy(true);
    try {
      const result = await request<{ corrected_text: string; matches: Array<{ from: string; to: string }> }>("/api/v1/audio/asr/vocabulary/test", { method: "POST", body: JSON.stringify({ text: vocabularyTest }) });
      setVocabularyTestResult(result.matches.length ? `${result.corrected_text}（${result.matches.map((item) => `${item.from}→${item.to}`).join("、")}）` : `${result.corrected_text}（未命中明确映射）`);
    } catch (error) { notify((error as Error).message); } finally { setVocabularyBusy(false); }
  };

  const persistSettings = async () => {
    const payload = structuredClone(draft);
    payload.llm.mode = "openai";
    if (llmApiKey.trim()) payload.llm.api_key = llmApiKey.trim();
    if (ttsApiKey.trim()) payload.audio.tts_siliconflow_api_key = ttsApiKey.trim();
    const result = await request<{ settings: ProductSettings }>("/api/v1/settings", { method: "PUT", body: JSON.stringify(payload) });
    setDraft(result.settings); setLlmApiKey(""); setTtsApiKey(""); onSettingsChange(result.settings); return result.settings;
  };
  const switchLlmProvider = (providerId: string) => {
    const provider = llmProviders.find((item) => item.id === providerId);
    if (!provider) return;
    const presetModels = provider.models || [];
    setDraft((current) => ({
      ...current,
      llm: {
        ...current.llm,
        provider: provider.id,
        base_url: provider.custom ? current.llm.base_url : provider.base_url,
        model: presetModels[0] || current.llm.model,
      },
    }));
    setAvailableModels(Array.from(new Set([str(draft.llm.model), ...presetModels].filter(Boolean))));
    setLlmModelStatus(provider.custom ? "填写兼容端点后获取模型，或直接输入模型 ID" : `${provider.label} 端点已自动配置`);
  };
  const discoverLlmModels = async () => {
    setLlmModelBusy(true);
    setLlmModelStatus("正在读取供应商模型列表…");
    try {
      const result = await request<{ models: string[]; source: string; warning?: string }>("/api/v1/models/discover", {
        method: "POST",
        body: JSON.stringify({
          provider: draft.llm.provider || "custom",
          base_url: draft.llm.base_url,
          api_key: llmApiKey.trim(),
        }),
      });
      const models = Array.from(new Set([str(draft.llm.model), ...(result.models || [])].filter(Boolean)));
      setAvailableModels(models);
      setLlmModelStatus(result.warning || `已读取 ${result.models.length} 个模型`);
      if (!draft.llm.model && result.models[0]) update("llm", "model", result.models[0]);
    } catch (error) {
      setLlmModelStatus((error as Error).message);
    } finally {
      setLlmModelBusy(false);
    }
  };
  const switchTtsProvider = async (next: string) => {
    const provider = ["browser", "cosyvoice", "gpt-sovits", "qwen3-vllm", "siliconflow"].includes(next) ? next : "browser";
    const previous = str(draft.audio.tts_provider || "qwen3-vllm");
    const previousAutoTts = bool(draft.audio.auto_tts);
    if (provider === previous || providerBusy) return;
    setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: provider, auto_tts: provider !== "browser" } }));
    setProviderBusy(true);
    setProviderStatus(provider === "browser" ? "正在关闭 TTS…" : provider === "cosyvoice" ? "正在切换到本地 CosyVoice…" : provider === "gpt-sovits" ? "正在切换到独立 GPT-SoVITS…" : provider === "qwen3-vllm" ? "正在切换到 Qwen3 实时语音…" : "正在切换到 SiliconFlow API…");
    try {
      const result = await request<{ settings: ProductSettings }>("/api/v1/settings", { method: "PUT", body: JSON.stringify({ audio: { tts_provider: provider, auto_tts: provider !== "browser" } }) });
      const confirmed = str(result.settings.audio.tts_provider);
      setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: confirmed } }));
      const baseline = JSON.parse(initial.current) as { value: ProductSettings; avatars: AvatarConfig };
      baseline.value = { ...baseline.value, audio: { ...baseline.value.audio, tts_provider: confirmed, auto_tts: confirmed !== "browser" } };
      initial.current = JSON.stringify(baseline);
      onSettingsChange(result.settings);
      const label = confirmed === "browser" ? "关闭声音（仅文字）" : confirmed === "cosyvoice" ? "本地 CosyVoice" : confirmed === "gpt-sovits" ? "本地 GPT-SoVITS" : confirmed === "qwen3-vllm" ? "Qwen3 实时语音" : "SiliconFlow API";
      setProviderStatus(`已切换并保存：${label}`);
      notify(`TTS 链路已切换为${label}`);
    } catch (error) {
      setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: previous, auto_tts: previousAutoTts } }));
      setProviderStatus(`切换失败，已保持原链路：${(error as Error).message}`);
      notify((error as Error).message);
    } finally {
      setProviderBusy(false);
    }
  };
  const switchGptVoice = async (voiceId: string) => {
    if (providerBusy) return;
    const previous = str(draft.audio.tts_gpt_sovits_voice || "v4-changli");
    setProviderBusy(true);
    setDraft((current) => ({ ...current, audio: { ...current.audio, tts_provider: "gpt-sovits", tts_gpt_sovits_voice: voiceId } }));
    setProviderStatus("正在切换 GPT-SoVITS 音色…");
    try {
      const result = await request<{ ok: boolean; pending_worker?: boolean; message?: string; settings: ProductSettings["audio"] }>("/api/v1/audio/tts/voice/select", { method: "POST", body: JSON.stringify({ voice_id: voiceId }) });
      const next = { ...draft, audio: result.settings };
      setDraft(next); onSettingsChange(next);
      setGptVoices((current) => ({ ...current, active_voice: voiceId, items: current.items.map((item) => ({ ...item, selected: item.id === voiceId })) }));
      const pendingMessage = result.message || "音色已保存，但 Worker 暂未完成切换";
      setProviderStatus(result.pending_worker ? pendingMessage : "音色已切换并热加载");
      notify(result.pending_worker ? pendingMessage : "GPT-SoVITS 音色切换完成");
    } catch (error) {
      setDraft((current) => ({ ...current, audio: { ...current.audio, tts_gpt_sovits_voice: previous } }));
      setProviderStatus(`音色切换失败：${(error as Error).message}`); notify((error as Error).message);
    } finally { setProviderBusy(false); }
  };
  const switchQwenVoice = async (voiceId: string) => {
    if (providerBusy) return;
    setProviderBusy(true);
    try {
      const result = await request<{ settings: ProductSettings }>("/api/v1/settings", {
        method: "PUT",
        body: JSON.stringify({ audio: { tts_provider: "qwen3-vllm", tts_qwen3_vllm_voice: voiceId, tts_qwen3_vllm_task_type: "CustomVoice" } }),
      });
      setDraft((current) => ({ ...current, audio: result.settings.audio }));
      onSettingsChange(result.settings);
      setQwenVoices((current) => ({ ...current, active_voice: voiceId, items: current.items.map((item) => ({ ...item, selected: item.id === voiceId })) }));
      setProviderStatus("Qwen3 音色已保存；下一段语音立即生效");
    } catch (error) {
      setProviderStatus(`Qwen3 音色切换失败：${(error as Error).message}`);
      notify((error as Error).message);
    } finally { setProviderBusy(false); }
  };
  const save = async () => {
    try {
      const next = await persistSettings();
      const avatarResult = await request<{ config: AvatarConfig }>("/api/v1/avatar/config", { method: "PUT", body: JSON.stringify(avatarDraft) });
      notify("设置和头像已保存并立即生效"); onSaved(next, normalizeAvatarConfig(avatarResult.config));
    } catch (error) { notify((error as Error).message); }
  };
  const uploadReference = async (file: File) => {
    if (file.size > 20 * 1024 * 1024) { notify("参考音频不能超过 20 MiB"); return; }
    setAudioBusy("upload"); setAudioStatus(`正在优化并上传 ${file.name}…`);
    try {
      let prepared = file;
      try { prepared = await normalizeReferenceAudio(file); } catch { /* Server-side decoding remains available. */ }
      const form = new FormData(); form.append("file", prepared); form.append("transcript", str(draft.audio.tts_reference_text));
      const result = await request<{ reference: Record<string, unknown>; settings: ProductSettings["audio"] }>("/api/v1/audio/tts/reference", { method: "POST", body: form });
      const uploaded = { ...draft, audio: result.settings }; setDraft(uploaded); onSettingsChange(uploaded);
      setAudioBusy("recognize"); setAudioStatus("音频已保存，正在识别实际说话内容…");
      try {
        const recognized = await request<{ transcript: string; duration?: number; settings: ProductSettings["audio"] }>("/api/v1/audio/tts/reference/transcribe", { method: "POST" });
        const next = { ...uploaded, audio: recognized.settings }; setDraft(next); onSettingsChange(next);
        const duration = recognized.duration ? ` · ${recognized.duration.toFixed(1)} 秒` : "";
        setAudioStatus(`识别完成${duration}，请核对下方文字`); notify("参考音频已上传并识别，请核对参考文本");
      } catch (error) {
        setAudioStatus(`音频已保存，但自动识别失败：${(error as Error).message}`); notify("音频已上传，请手动填写或重新识别参考文本");
      }
    } catch (error) { setAudioStatus((error as Error).message); notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const recognizeReference = async () => {
    setAudioBusy("recognize"); setAudioStatus("正在识别参考音频中的实际文字…");
    try {
      const result = await request<{ transcript: string; duration?: number; settings: ProductSettings["audio"] }>("/api/v1/audio/tts/reference/transcribe", { method: "POST" });
      const next = { ...draft, audio: result.settings }; setDraft(next); onSettingsChange(next);
      const duration = result.duration ? ` · ${result.duration.toFixed(1)} 秒` : "";
      setAudioStatus(`识别完成${duration}，请核对后保存`); notify("识别结果已填入参考文本");
    } catch (error) { setAudioStatus((error as Error).message); notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const clearReference = async () => {
    if (!(await styledConfirm({ title: "清除参考音频？", message: "当前参考音频和参考文本都会被清除。", confirmLabel: "清除参考", danger: true }))) return;
    setAudioBusy("clear");
    try {
      const result = await request<{ settings: ProductSettings["audio"] }>("/api/v1/audio/tts/reference", { method: "DELETE" });
      const next = { ...draft, audio: result.settings }; setDraft(next); onSettingsChange(next); setAudioStatus("尚未上传参考音频"); notify("参考音频已清除");
    } catch (error) { notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const playTtsTest = async (next: ProductSettings) => {
    const status = await request<Record<string, unknown>>("/api/v1/audio/status");
    if (!bool(status.tts_ready)) throw new Error(str(status.tts_error || "TTS 服务尚未就绪"));
    if (str(next.audio.tts_provider) === "cosyvoice" && !bool(next.audio.tts_reference_configured)) throw new Error("请先上传参考音频");
    if (str(next.audio.tts_provider) === "siliconflow" && !bool(next.audio.tts_siliconflow_credentials_configured)) throw new Error("请先填写 SiliconFlow API 密钥");
    const response = await rawRequest("/api/v1/audio/tts", { method: "POST", body: JSON.stringify({ text: "这是 Mindspace 语音测试。", speed: num(next.audio.tts_speed, 1), request_id: uid() }) });
    if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(str(detail.detail || "测试语音生成失败")); }
    const blob = await response.blob();
    if (!blob.size) throw new Error("TTS 接口未返回音频数据");
    const url = URL.createObjectURL(blob); const audio = new Audio(url); audio.onended = () => URL.revokeObjectURL(url); await audio.play();
  };
  const testApiConnections = async () => {
    setAudioBusy("api-check"); setAudioStatus("正在同步检查 LLM 与 TTS API…");
    try {
      const next = await persistSettings();
      const llm = await request<Record<string, unknown>>("/api/v1/settings/test", { method: "POST" });
      if (!bool(llm.ok)) throw new Error(`LLM 自检失败：${str(llm.error || "连接失败")}`);
      await playTtsTest(next);
      const llmDetail = "LLM API 正常";
      const ttsDetail = str(next.audio.tts_provider) === "siliconflow" ? "云端 TTS API 正常" : "本地 TTS 正常";
      setAudioStatus(`${ttsDetail}，测试音频已播放`); notify(`自检完成：${llmDetail}；${ttsDetail}`);
    } catch (error) { setAudioStatus((error as Error).message); notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const testTts = async () => {
    setAudioBusy("test"); setAudioStatus("正在检查语音服务并生成测试语音…");
    try {
      const next = await persistSettings();
      await playTtsTest(next); setAudioStatus("测试语音生成并播放成功");
    } catch (error) { setAudioStatus((error as Error).message); notify((error as Error).message); } finally { setAudioBusy(""); }
  };
  const ttsProvider = str(draft.audio.tts_provider || "qwen3-vllm");
  const uploadAvatar = async (role: Role, file: File) => {
    if (file.size > 5 * 1024 * 1024) { notify("头像不能超过 5 MiB"); return; }
    setAvatarBusy(role);
    try {
      const form = new FormData(); form.append("file", file);
      const result = await request<{ config: AvatarConfig }>(`/api/v1/avatar/upload/${role}`, { method: "POST", body: form });
      const normalized = normalizeAvatarConfig(result.config); setAvatarDraft(normalized); onAvatarsChange(normalized); notify(`${role === "assistant" ? "AI" : "用户"}头像上传成功`);
    } catch (error) { notify((error as Error).message); } finally { setAvatarBusy(""); }
  };
  const settingGroups = [
    { id: "connection", label: "连接", tabs: [["model", "模型与 API"], ["capabilities", "自动能力"]] },
    { id: "memory", label: "记忆", tabs: [["rag", "记忆与检索"]] },
    { id: "voice", label: "声音", tabs: [["audio", "实时语音"], ["vocabulary", "识别词表"], ["rhythm", "陪伴频率"]] },
    { id: "interface", label: "界面", tabs: [["avatar", "人物头像"], ["appearance", "显示偏好"]] },
    { id: "advanced", label: "高级", tabs: [["protocol", "协议与诊断"]] },
  ] as const;
  const activeGroup = settingGroups.find((group) => group.tabs.some(([id]) => id === tab)) || settingGroups[0];
  return <Modal title="设置中心" kicker="SETTINGS HUB" onClose={onClose} footer={<><button className="secondary" onClick={onClose}>取消</button><button className="primary" onClick={() => void save()}>保存设置</button></>}>
    <div className="settings-layout settings-hub-layout">
      <nav>{settingGroups.map((group) => <button key={group.id} className={activeGroup.id === group.id ? "active" : ""} onClick={() => setTab(group.tabs[0][0])}><span aria-hidden="true">{group.id === "connection" ? "⌁" : group.id === "memory" ? "◇" : group.id === "voice" ? "◉" : group.id === "interface" ? "▣" : "⌘"}</span>{group.label}</button>)}</nav>
      <div className="settings-panel">
        <div className="settings-subnav">
          {activeGroup.tabs.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
          {activeGroup.id === "memory" && <><button onClick={onOpenMemory}>记忆内容 ↗</button><button onClick={onOpenKnowledge}>知识库 ↗</button></>}
          {activeGroup.id === "advanced" && <button onClick={onOpenDiagnostics}>系统诊断 ↗</button>}
        </div>
    {tab === "model" && <>
      <h3>用户资料</h3>
      <p className="notice">角色设定已收缩为 V2 角色卡；请在角色库编辑基础资料，在记忆中心维护偏好与任务。</p>
      <div className="persona-config-actions"><button type="button" onClick={() => onOpenProfile("user")}>编辑用户资料</button></div>
      <h3>语言模型 API</h3>
      <p className={`notice ${bool(draft.llm.credentials_configured) ? "" : "warning"}`}>{bool(draft.llm.credentials_configured) ? "真实 LLM API 已启用；保存后立即用于下一轮对话。" : "尚未配置 LLM API 密钥。未配置时会阻止发送，不会生成演示回复。"}</p>
      <section className="llm-connection-panel">
        <header><div><span>OPENAI COMPATIBLE</span><strong>{llmProviders.find((item) => item.id === draft.llm.provider)?.label || "选择供应商"}</strong></div><small>预设端点自动维护，自定义接口仍可完整填写</small></header>
        <div className="form-grid">
          <SelectField label="API 供应商" value={draft.llm.provider || "custom"} options={llmProviders.map((item) => [item.id, item.label])} onChange={switchLlmProvider} />
          <Field label="新 API 密钥（留空保持）" value={llmApiKey} type="password" placeholder={bool(draft.llm.credentials_configured) ? "已配置；输入新密钥可替换" : "输入 API 密钥"} onChange={(next) => setLlmApiKey(str(next))} />
          {draft.llm.provider === "custom" && <Field label="OpenAI 兼容 API 地址" value={draft.llm.base_url} onChange={(next) => update("llm", "base_url", next)} placeholder="例如 http://127.0.0.1:1234/v1" />}
          <label className="field" style={{ gridColumn: "1 / -1" }}><span>模型</span><input list="mindspace-llm-models" value={draft.llm.model} onChange={(event) => update("llm", "model", event.target.value)} placeholder="选择或填写模型 ID" /><datalist id="mindspace-llm-models">{availableModels.map((model) => <option value={model} key={model} />)}</datalist></label>
          <div style={{ gridColumn: "1 / -1" }}><Field label="最大输出 Token" value={draft.llm.max_tokens} type="number" min={512} max={32768} step={512} onChange={(next) => update("llm", "max_tokens", next)} /><small className="field-hint">控制模型单次输出上限；工具调用及工具后的回复最低保障 4096 Token，不改变上下文窗口。</small></div>
          <Field label="温度" value={draft.llm.temperature} type="number" min={0} max={2} step={0.05} onChange={(next) => update("llm", "temperature", next)} />
        </div>
        <footer><button className="secondary" type="button" disabled={llmModelBusy || !llmProviders.length} onClick={() => void discoverLlmModels()}>{llmModelBusy ? "正在获取模型…" : "获取模型列表"}</button><span>{llmModelStatus}</span></footer>
      </section>
      <h3>语音合成 API</h3>
      <p className="notice">上线版本默认使用云端流式 TTS，不随安装包分发本地 CosyVoice 模型；本地链路仍可在“实时语音”中切换。</p>
      <div className="form-grid"><Field label="SiliconFlow API 地址" value={draft.audio.tts_siliconflow_base_url} onChange={(next) => update("audio", "tts_siliconflow_base_url", next)} /><Field label="新 TTS API 密钥（留空保持）" value={ttsApiKey} type="password" placeholder={bool(draft.audio.tts_siliconflow_credentials_configured) ? "已配置；输入新密钥可替换" : "输入 SiliconFlow API 密钥"} onChange={(next) => setTtsApiKey(str(next))} /><SelectField label="云端模型" value={draft.audio.tts_siliconflow_model} options={[["fnlp/MOSS-TTSD-v0.5", "MOSS-TTSD v0.5"], ["FunAudioLLM/CosyVoice2-0.5B", "CosyVoice2 0.5B"]]} onChange={(next) => setDraft((current) => ({ ...current, audio: { ...current.audio, tts_siliconflow_model: next, tts_siliconflow_voice: next === "fnlp/MOSS-TTSD-v0.5" ? "fnlp/MOSS-TTSD-v0.5:alex" : "FunAudioLLM/CosyVoice2-0.5B:alex" } }))} /><Field label="音色 ID" value={draft.audio.tts_siliconflow_voice} onChange={(next) => update("audio", "tts_siliconflow_voice", next)} /><SelectField label="PCM 采样率" value={draft.audio.tts_siliconflow_sample_rate} options={[["16000", "16 kHz"], ["24000", "24 kHz（推荐）"], ["32000", "32 kHz"], ["44100", "44.1 kHz"]]} onChange={(next) => update("audio", "tts_siliconflow_sample_rate", Number(next))} /><Field label="增益 dB" value={draft.audio.tts_siliconflow_gain} type="number" min={-10} max={10} step={0.5} onChange={(next) => update("audio", "tts_siliconflow_gain", next)} /></div>
      <button className="inline-action" disabled={Boolean(audioBusy)} onClick={() => void testApiConnections()}>{audioBusy === "api-check" ? "正在自检…" : "自检 LLM + TTS API"}</button>
      <h3>用户设定</h3><div className="form-grid"><Field label="用户称呼" value={draft.persona.user_name} onChange={(next) => update("persona", "user_name", next)} /><Field label="用户设定" value={draft.persona.user_persona} type="textarea" onChange={(next) => update("persona", "user_persona", next)} /><Field label="回复篇幅偏好（留空则自然发挥）" value={draft.persona.reply_length_preference} type="textarea" placeholder="例如：日常简洁，重要话题可以详细；或每次尽量控制在两段内" onChange={(next) => update("persona", "reply_length_preference", next)} /></div>
    </>}
    {tab === "avatar" && <><h3>人物头像</h3><p className="notice">上传图片并调整裁剪。聊天、人物卡和实时语音会立即使用同一份头像配置。</p><div className="avatar-settings-grid">{(["user", "assistant"] as Role[]).map((role) => <AvatarEditor key={role} role={role} entry={avatarDraft[role]} busy={avatarBusy === role} onUpload={(file) => void uploadAvatar(role, file)} onChange={(entry) => setAvatarDraft((current) => ({ ...current, [role]: entry }))} />)}</div></>}
      {tab === "rhythm" && <><h3>时间感知</h3><p className="notice">文字与语音对话都会记录服务端 UTC 时间、当地时区以及与上次真实用户消息的时间差。时间只作为本轮运行事实，不会自行修改人物档案。</p><h3>连续陪伴</h3><div className="toggle-grid"><Field label="无限制回复" value={draft.interaction?.unlimited_reply_enabled} type="checkbox" onChange={(next) => update("interaction", "unlimited_reply_enabled", next)} /></div><div className="form-grid"><Field label="连续陪伴轮次上限" value={draft.interaction?.unlimited_reply_max_rounds} type="number" min={1} max={50} step={1} onChange={(next) => update("interaction", "unlimited_reply_max_rounds", next)} /></div><p className="notice">仅在实时语音中生效，衔接间隔固定为 10 秒。每次 TTS 完整朗读结束后，角色会自主规划并继续话题；默认你只想听，不会催促回复。你随时可以插话，插话会改变后续话题方向，但不会关闭连续陪伴或清零轮次。进度只显示在语音页面，到达上限后自动停止。</p><h3>沉默后主动续接</h3><div className="toggle-grid"><Field label="允许 AI 在沉默后自然续接" value={draft.interaction?.idle_continuation_enabled} type="checkbox" onChange={(next) => update("interaction", "idle_continuation_enabled", next)} /></div><div className="form-grid"><Field label="文字对话等待秒数" value={draft.interaction?.text_idle_seconds} type="number" min={10} max={3600} step={10} onChange={(next) => update("interaction", "text_idle_seconds", next)} /><Field label="语音通话等待秒数" value={draft.interaction?.voice_idle_seconds} type="number" min={5} max={600} step={5} onChange={(next) => update("interaction", "voice_idle_seconds", next)} /></div><p className="notice">普通主动续接每个静默阶段最多说一次；连续陪伴开启时，语音模式优先使用上面的多轮逻辑。</p></>}
      {tab === "capabilities" && <>
        <h3>只读自动能力</h3>
        <p className="notice">总开关开启后，AI 可自行调用你允许的读取能力，不再逐次弹窗确认。一次查询、必要的补充模型调用和最终回答始终合并为同一轮回复。</p>
        <div className="toggle-grid"><Field label="允许只读自动能力" value={draft.capabilities?.master_enabled} type="checkbox" onChange={(next) => update("capabilities", "master_enabled", next)} /><Field label="自动查询本地知识" value={draft.capabilities?.local_knowledge_enabled} type="checkbox" onChange={(next) => update("capabilities", "local_knowledge_enabled", next)} /><Field label="允许联网搜索" value={draft.capabilities?.web_search_enabled} type="checkbox" onChange={(next) => update("capabilities", "web_search_enabled", next)} /><Field label="允许实时热点" value={draft.capabilities?.realtime_topics_enabled} type="checkbox" onChange={(next) => update("capabilities", "realtime_topics_enabled", next)} /><Field label="自然扩展相关话题" value={draft.capabilities?.topic_expansion_enabled} type="checkbox" onChange={(next) => update("capabilities", "topic_expansion_enabled", next)} /><Field label="沉默续接可参考热点" value={draft.capabilities?.proactive_hotspots_enabled} type="checkbox" onChange={(next) => update("capabilities", "proactive_hotspots_enabled", next)} /><Field label="回答中展示网页来源" value={draft.capabilities?.show_sources_enabled} type="checkbox" onChange={(next) => update("capabilities", "show_sources_enabled", next)} /></div>
        <h3>联网边界</h3><div className="form-grid"><Field label="联网超时秒数" value={draft.capabilities?.web_timeout_seconds} type="number" min={2} max={30} step={1} onChange={(next) => update("capabilities", "web_timeout_seconds", next)} /><Field label="搜索结果上限" value={draft.capabilities?.max_web_results} type="number" min={1} max={20} step={1} onChange={(next) => update("capabilities", "max_web_results", next)} /><Field label="打开原文上限" value={draft.capabilities?.max_web_pages} type="number" min={0} max={10} step={1} onChange={(next) => update("capabilities", "max_web_pages", next)} /><Field label="每页正文字符" value={draft.capabilities?.max_web_content_chars} type="number" min={2000} max={30000} step={1000} onChange={(next) => update("capabilities", "max_web_content_chars", next)} /></div>
        <p className="notice warning">该权限仅允许现有知识检索和公开网页 GET 读取。AI 无权读取本机配置、硬件、进程或服务健康状态，也不能执行命令、修改文件、上传资料、登录网站、发送消息、结束进程或读取密钥。网页内容不能修改人物 JSON，也不能作为用户偏好证据。</p>
      </>}
    {tab === "rag" && <><h3>检索开关</h3><div className="toggle-grid"><Field label="启用 RAG" value={draft.retrieval.rag_enabled} type="checkbox" onChange={(next) => update("retrieval", "rag_enabled", next)} /><Field label="知识库召回" value={draft.retrieval.knowledge_enabled} type="checkbox" onChange={(next) => update("retrieval", "knowledge_enabled", next)} /><Field label="会话记忆召回" value={draft.retrieval.chat_enabled} type="checkbox" onChange={(next) => update("retrieval", "chat_enabled", next)} /><Field label="JSON 字段记忆" value={draft.retrieval.structured_memory_enabled} type="checkbox" onChange={(next) => update("retrieval", "structured_memory_enabled", next)} /><Field label="BM25+ 词法召回" value={draft.retrieval.bm25_enabled} type="checkbox" onChange={(next) => update("retrieval", "bm25_enabled", next)} /><Field label="向量召回" value={draft.retrieval.vector_enabled} type="checkbox" onChange={(next) => update("retrieval", "vector_enabled", next)} /><Field label="本地精排（需模型）" value={draft.retrieval.reranker_enabled} type="checkbox" onChange={(next) => update("retrieval", "reranker_enabled", next)} /><Field label="公平曝光保护" value={draft.retrieval.fairness_enabled} type="checkbox" onChange={(next) => update("retrieval", "fairness_enabled", next)} /><Field label="时间衰减" value={draft.retrieval.temporal_enabled} type="checkbox" onChange={(next) => update("retrieval", "temporal_enabled", next)} /></div><h3>召回参数</h3><div className="form-grid"><Field label="知识库上限" value={draft.retrieval.knowledge_k} type="number" onChange={(next) => update("retrieval", "knowledge_k", next)} /><Field label="原始对话上限" value={draft.retrieval.chat_k} type="number" onChange={(next) => update("retrieval", "chat_k", next)} /><Field label="结构化历史上限" value={draft.retrieval.history_k} type="number" onChange={(next) => update("retrieval", "history_k", next)} /><Field label="相似度阈值" value={draft.retrieval.similarity_threshold} type="number" step={0.05} onChange={(next) => update("retrieval", "similarity_threshold", next)} /><Field label="RRF 常数" value={draft.retrieval.rrf_k} type="number" onChange={(next) => update("retrieval", "rrf_k", next)} /><Field label="候选放大倍数" value={draft.retrieval.candidate_multiplier} type="number" onChange={(next) => update("retrieval", "candidate_multiplier", next)} /><Field label="精排候选数" value={draft.retrieval.reranker_top_n} type="number" onChange={(next) => update("retrieval", "reranker_top_n", next)} /><Field label="轮次衰减" value={draft.retrieval.decay_rounds} type="number" onChange={(next) => update("retrieval", "decay_rounds", next)} /><Field label="低曝光保留比例" value={draft.retrieval.low_exposure_ratio} type="number" step={0.05} onChange={(next) => update("retrieval", "low_exposure_ratio", next)} /><Field label="同字段族上限" value={draft.retrieval.memory_family_limit} type="number" onChange={(next) => update("retrieval", "memory_family_limit", next)} /><Field label="饥饿保护轮次" value={draft.retrieval.starvation_rounds} type="number" onChange={(next) => update("retrieval", "starvation_rounds", next)} /></div><p className="notice">前15轮默认只构建索引，不自动召回；之后按知识库2、原始对话3、结构化历史3的来源上限编排，低于阈值时不强行凑满。</p><h3>知识分块</h3><div className="form-grid"><Field label="子块长度" value={draft.knowledge.child_size} type="number" onChange={(next) => update("knowledge", "child_size", next)} /><Field label="父块长度" value={draft.knowledge.parent_size} type="number" onChange={(next) => update("knowledge", "parent_size", next)} /><Field label="重叠字符" value={draft.knowledge.overlap} type="number" onChange={(next) => update("knowledge", "overlap", next)} /></div></>}
    {tab === "protocol" && <><h3>生成与 JSON 写回</h3><div className="form-grid"><Field label="协议模式" value={draft.protocol.mode} onChange={(next) => update("protocol", "mode", next)} /><Field label="角色审计模型（留空复用主模型）" value={draft.llm.role_audit_model} onChange={(next) => update("llm", "role_audit_model", next)} /></div><div className="toggle-grid"><Field label="自动结构修复" value={draft.protocol.auto_repair} type="checkbox" onChange={(next) => update("protocol", "auto_repair", next)} /><Field label="显示写回诊断" value={draft.protocol.diagnostics} type="checkbox" onChange={(next) => update("protocol", "diagnostics", next)} /><Field label="复杂角色异步审计" value={draft.llm.role_audit_enabled} type="checkbox" onChange={(next) => update("llm", "role_audit_enabled", next)} /></div><p className="notice">回复立即流式展示；JSON 每轮最多写入三个经过路径、证据和 revision 校验的叶子 Patch。复杂角色审计只在本轮完成后运行，不能替换已显示或已朗读的内容，严重偏移只影响下一轮。</p></>}
    {tab === "audio" && <>
      <h3>语音合成</h3>
      <div className="form-grid"><SelectField label="TTS 链路" value={draft.audio.tts_provider} disabled={providerBusy} options={[["browser", "关闭声音（仅文字）"], ["gpt-sovits", "GPT-SoVITS 二次元声线"], ["cosyvoice", "本地 CosyVoice 声音克隆"], ["qwen3-vllm", "Qwen3 高质量活人感"], ["siliconflow", "SiliconFlow 云端流式 TTS"]]} onChange={(next) => void switchTtsProvider(next)} /><Field label="速度" value={draft.audio.tts_speed} type="number" min={0.5} max={2} step={0.1} onChange={(next) => update("audio", "tts_speed", next)} /></div>
      <p className={`notice ${providerStatus.startsWith("切换失败") ? "warning" : ""}`}>{providerStatus}</p>
      {ttsProvider === "cosyvoice" && <><div className="form-grid"><Field label="CosyVoice Worker" value={draft.audio.tts_worker_url} onChange={(next) => update("audio", "tts_worker_url", next)} /><Field label="识别出的参考文本（请校对）" value={draft.audio.tts_reference_text} type="textarea" onChange={(next) => update("audio", "tts_reference_text", next)} placeholder="上传后自动识别；必须与参考音频实际说出的内容一致" /></div><p className="notice warning">本地 CosyVoice 是可选链路，上线安装包不包含其模型。参考文本必须与音频逐字匹配；实时语音只输出并朗读角色亲口说出的自然口语，不使用括号动作旁白。</p><div className="reference-panel"><div><strong>本地参考音频</strong><small>{audioStatus}</small></div><div><label className="secondary upload-button">{audioBusy === "upload" ? "上传中…" : bool(draft.audio.tts_reference_configured) ? "替换音频" : "选择并上传"}<input hidden disabled={Boolean(audioBusy)} type="file" accept=".wav,.mp3,.flac,.m4a,.ogg,audio/*" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadReference(file); event.currentTarget.value = ""; }} /></label><button className="secondary" disabled={Boolean(audioBusy) || !bool(draft.audio.tts_reference_configured)} onClick={() => void recognizeReference()}>{audioBusy === "recognize" ? "识别中…" : "识别音频文字"}</button><button className="secondary" disabled={Boolean(audioBusy) || !bool(draft.audio.tts_reference_configured)} onClick={() => void clearReference()}>清除</button></div></div></>}
      {ttsProvider === "gpt-sovits" && <><div className="form-grid"><SelectField label="GPT-SoVITS 音色" value={draft.audio.tts_gpt_sovits_voice || gptVoices.active_voice} disabled={providerBusy} options={gptVoices.items.length ? gptVoices.items.map((voice) => [voice.id, `${voice.label}${voice.installed ? " · 已安装" : " · 需在启动器安装"}`] as [string, string]) : [["v4-changli", "V4-长离"], ["v4-yae-miko", "V4-八重神子"], ["v2proplus-kafka", "V2ProPlus-卡芙卡"]]} onChange={(next) => void switchGptVoice(next)} /><Field label="GPT-SoVITS Worker" value={draft.audio.tts_gpt_sovits_worker_url || "http://127.0.0.1:5055"} onChange={(next) => update("audio", "tts_gpt_sovits_worker_url", next)} /></div><p className="notice warning">音色模型与原 CosyVoice 完全分离，由启动器按需安装。V4 原生输出 48 kHz；卡芙卡实际为 V2ProPlus。第三方角色音色仅用于本地非商业验证，正式上线前必须取得对应权利方授权。</p></>}
      {ttsProvider === "qwen3-vllm" && <><div className="form-grid"><SelectField label="Qwen3 音色" value={draft.audio.tts_qwen3_vllm_voice || qwenVoices.active_voice} disabled={providerBusy} options={qwenVoices.items.length ? qwenVoices.items.map((voice) => [voice.id, voice.label] as [string, string]) : [["serena", "Serena · 温柔成年女声（运行时未就绪）"]]} onChange={(next) => void switchQwenVoice(next)} /><Field label="Qwen3 服务地址" value={draft.audio.tts_qwen3_vllm_url || "http://127.0.0.1:8091"} onChange={(next) => update("audio", "tts_qwen3_vllm_url", next)} /><Field label="模型名" value={draft.audio.tts_qwen3_vllm_model || "mindspace-qwen3-tts"} onChange={(next) => update("audio", "tts_qwen3_vllm_model", next)} /></div><p className="notice">Qwen3 使用 CustomVoice 固定 Serena speaker、固定随机种子和整篇单次合成。正文完成并通过格式清理后立即提交，不等待落库收尾；语气指令只控制语速、笑声、换气和情绪，不重新描述或改变音色。</p></>}
      {ttsProvider === "browser" && <p className="notice">当前关闭声音，只保留文字对话。启动器与应用内会显示相同状态，也不会加载本地 TTS 或占用额外显存。</p>}
      {ttsProvider === "siliconflow" && <p className="notice">云端 API 参数已集中到“模型与角色”。此处只选择链路与播放速度；逐句流式播放、首句抢跑和插话打断与本地链路一致，实时语音只输出可直接朗读的自然口语。</p>}
      <div className="row-actions"><button className="primary" disabled={Boolean(audioBusy)} onClick={() => void testTts()}>{audioBusy === "test" ? "生成中…" : "生成并试听 TTS"}</button></div>
      <Field label="实时语音中自动朗读" value={draft.audio.auto_tts} type="checkbox" onChange={(next) => update("audio", "auto_tts", next)} />
      <h3>实时识别与环境噪声</h3>
      <div className="toggle-grid"><Field label="启用人物与 JSON 动态词表" value={draft.audio.asr_hotwords_enabled} type="checkbox" onChange={(next) => update("audio", "asr_hotwords_enabled", next)} /><Field label="含糊停顿动态断句" value={draft.audio.asr_dynamic_endpointing} type="checkbox" onChange={(next) => update("audio", "asr_dynamic_endpointing", next)} /><Field label="Nano 整句复核" value={draft.audio.asr_final_refinement_enabled} type="checkbox" onChange={(next) => update("audio", "asr_final_refinement_enabled", next)} /></div>
      <div className="form-grid"><Field label="ASR 提供方" value={draft.audio.asr_provider} onChange={(next) => update("audio", "asr_provider", next)} /><Field label="ASR 模型" value={draft.audio.asr_model} onChange={(next) => update("audio", "asr_model", next)} /><Field label="静音断句毫秒" value={draft.audio.asr_silence_ms} type="number" min={250} max={3000} onChange={(next) => update("audio", "asr_silence_ms", next)} /><Field label="多段话合并窗口毫秒" value={draft.audio.asr_utterance_merge_ms} type="number" min={300} max={3000} step={50} onChange={(next) => update("audio", "asr_utterance_merge_ms", next)} /></div><p className="notice">语音入口不再等待环境噪声校准；浏览器只保留回声消除和自动增益，FunASR VAD 负责判断真实人声。Paraformer 保持实时字幕，Nano 仅在整句结束时低优先级复核。</p>
      <h3>语音情绪感知 · 实验性</h3>
      <div className="toggle-grid"><Field label="情绪侧链接口（暂时停用）" value={false} type="checkbox" onChange={() => undefined} /></div>
      <p className="advanced-note">情绪分析在本轮回复完成后后台执行，不再等待或延迟当前回复；完成后的状态仅供下一轮语音调整语气。</p>
      <p className="notice">当前版本不加载情绪模型，也不执行声学或文本情绪分析；仅保留后端接口，便于后续按需接入。</p>
      <h3>AI 播放完：短回复优先</h3><div className="form-grid"><Field label="监听最低门槛 dBFS" value={draft.audio.asr_listening_energy_threshold_db} type="number" min={-60} max={-15} step={1} onChange={(next) => update("audio", "asr_listening_energy_threshold_db", next)} /><Field label="监听最短发声毫秒" value={draft.audio.asr_listening_min_speech_ms} type="number" min={60} max={1000} step={20} onChange={(next) => update("audio", "asr_listening_min_speech_ms", next)} /></div>
      <h3>AI 播放中：三重确认后打断</h3><div className="form-grid"><Field label="插话最低门槛 dBFS" value={draft.audio.asr_barge_in_energy_threshold_db} type="number" min={-60} max={-15} step={1} onChange={(next) => update("audio", "asr_barge_in_energy_threshold_db", next)} /><Field label="插话最短发声毫秒" value={draft.audio.asr_barge_in_min_speech_ms} type="number" min={120} max={1500} step={20} onChange={(next) => update("audio", "asr_barge_in_min_speech_ms", next)} /><Field label="疑似声音释放毫秒" value={draft.audio.asr_candidate_release_ms} type="number" min={80} max={1000} step={20} onChange={(next) => update("audio", "asr_candidate_release_ms", next)} /></div>
      <div className="toggle-grid"><Field label="未达到打断条件的有效文字稍后发送" value={draft.audio.asr_deferred_during_playback} type="checkbox" onChange={(next) => update("audio", "asr_deferred_during_playback", next)} /><Field label="合并结束后自动发送" value={draft.audio.asr_auto_send} type="checkbox" onChange={(next) => update("audio", "asr_auto_send", next)} /></div>
      <p className="notice">候选噪声只降低播放音量；能量、FSMN-VAD 与有效识别共同确认后才打断。AI 尚未出声时，后续语音会合并进同一用户轮次；播放中未达到打断条件但识别出有效文字时，会在播放结束后统一发送。</p>
    </>}
    {tab === "vocabulary" && <>
      <h3>新增个人词条</h3>
      <p className="notice">词表只参与本地 ASR 解码与确定性纠偏，不进入 Prompt，也不会触发额外 LLM 调用。人物名称和专有名词使用高强化；三份 JSON 的有效字段会按 revision 自动生成轻度词条。</p>
      <div className="form-grid"><Field label="标准写法" value={vocabularyTerm} onChange={(next) => setVocabularyTerm(str(next))} placeholder="例如：长离" /><Field label="常见误识别（逗号分隔）" value={vocabularyAliases} onChange={(next) => setVocabularyAliases(str(next))} placeholder="例如：长利，常离" /><SelectField label="强化等级" value={vocabularyPriority} options={[["critical", "最高 · 明确纠偏"], ["high", "高 · 人名/专名"], ["medium", "中 · 当前实体"], ["low", "轻 · 普通字段"]]} onChange={(next) => setVocabularyPriority(next as ASRVocabularyEntry["priority"])} /></div>
      <div className="row-actions"><button className="primary" disabled={vocabularyBusy || !vocabularyTerm.trim()} onClick={() => void addVocabularyEntry()}>{vocabularyBusy ? "保存中…" : "新增并立即生效"}</button></div>
      <h3>词表测试</h3><div className="vocabulary-test"><Field label="输入一段可能识别错误的文字" value={vocabularyTest} onChange={(next) => setVocabularyTest(str(next))} placeholder="例如：我想换成长利的声音" /><button className="secondary" disabled={vocabularyBusy || !vocabularyTest.trim()} onClick={() => void testVocabulary()}>测试纠偏</button></div>{vocabularyTestResult && <p className="notice">{vocabularyTestResult}</p>}
      <h3>当前词表</h3>
      <div className="vocabulary-summary"><span>个人 <b>{num(vocabulary?.counts.manual)}</b></span><span>JSON 自动 <b>{num(vocabulary?.counts.profile)}</b></span><span>系统 <b>{num(vocabulary?.counts.system)}</b></span><span>解码热词 <b>{vocabulary?.decoder_hotwords.length || 0}</b></span><small>revision {vocabulary?.revision || "读取中"}</small></div>
      <label className="search-box vocabulary-search"><span>⌕</span><input value={vocabularyQuery} onChange={(event) => setVocabularyQuery(event.target.value)} placeholder="搜索标准词、别名、来源字段" /></label>
      <div className="vocabulary-list">{(vocabulary?.entries || []).filter((item) => !vocabularyQuery.trim() || `${item.term} ${item.aliases.join(" ")} ${item.source_field} ${item.category}`.toLowerCase().includes(vocabularyQuery.trim().toLowerCase())).slice(0, 160).map((item) => <article key={item.id} className={!item.enabled ? "disabled" : ""}><div><strong>{item.term}</strong><span className={`priority ${item.priority}`}>{item.priority === "critical" ? "最高" : item.priority === "high" ? "高" : item.priority === "medium" ? "中" : "轻"}</span><small>{item.category} · {item.source === "manual" ? "个人" : item.source === "profile" ? "JSON 自动" : "系统"}</small>{item.aliases.length > 0 && <p>易错：{item.aliases.join("、")}</p>}{item.source_field && <p className="source-field">{item.source_field}</p>}</div>{item.source === "manual" ? <div className="vocabulary-actions"><button className="secondary" disabled={vocabularyBusy} onClick={() => void saveManualVocabulary((vocabulary?.entries || []).filter((entry) => entry.source === "manual").map((entry) => entry.id === item.id ? { ...entry, enabled: !entry.enabled } : entry))}>{item.enabled ? "停用" : "启用"}</button><button className="danger-text" disabled={vocabularyBusy} onClick={async () => { if (await styledConfirm({ title: `删除词条“${item.term}”？`, message: "删除后，该词不会再作为个人识别词参与语音解码。", confirmLabel: "删除词条", danger: true })) await saveManualVocabulary((vocabulary?.entries || []).filter((entry) => entry.source === "manual" && entry.id !== item.id)); }}>删除</button></div> : <span className="read-only-badge">自动</span>}</article>)}</div>
      {(vocabulary?.entries.length || 0) > 160 && !vocabularyQuery && <p className="notice">自动词条较多，当前只展示前 160 条；使用搜索可定位其余词条。</p>}
    </>}
    {tab === "appearance" && <><h3>界面偏好</h3><div className="form-grid"><SelectField label="主题" value={draft.appearance.theme} options={[["mindscape", "Mindscape 暖色"], ["dark", "深色研究界面"]]} onChange={(next) => update("appearance", "theme", next)} /><SelectField label="界面密度" value={draft.appearance.density} options={[["chat", "舒适对话"], ["research", "紧凑研究"]]} onChange={(next) => update("appearance", "density", next)} /><SelectField label="字体大小" value={draft.appearance.font_scale ?? 1.3} options={[["1", "标准（100%）"], ["1.15", "较大（115%）"], ["1.3", "默认大字（130%）"], ["1.45", "更大（145%）"], ["1.6", "特大（160%）"]]} onChange={(next) => update("appearance", "font_scale", Number(next))} /><Field label="语言" value={draft.appearance.language} onChange={(next) => update("appearance", "language", next)} /></div><p className="notice">全屏或大屏窗口会在所选字号上自动再放大，缩回普通窗口后恢复；设置保存后立即生效。</p></>}
  </div></div></Modal>;
}

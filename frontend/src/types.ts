export type Role = "user" | "assistant";

export interface InteractionTag {
  id: string;
  category: "daily" | "touch" | "kiss" | "custom";
  level: number;
  action: string;
  target?: string;
  sensitivity: "normal" | "intimate";
}

export interface ChatAttachment {
  attachment_id: string;
  name: string;
  media_type: string;
  size: number;
  content?: string;
  content_missing?: boolean;
}

export interface Message {
  message_id?: string;
  role: Role;
  content: string;
  round: number;
  timestamp?: string;
  timing?: Record<string, string | null>;
  status?: "complete" | "streaming" | "cancelled" | "interrupted" | "error";
  kind?: "message" | "initiative_signal" | "initiative_response";
  initiative_trigger?: InitiativeTrigger;
  voice_cue?: string;
  hidden?: boolean;
  presentation_mode?: PresentationModeResolved;
  tool_execution?: ToolExecution | null;
  reply_to_message_id?: string;
  interactions?: InteractionTag[];
  attachments?: ChatAttachment[];
  request_snapshot?: ChatTurnRequest;
}

export interface ToolExecution {
  call_id: string;
  tool: "web" | "memory" | "task";
  level: 2 | 3;
  status: "requested" | "reviewing" | "running" | "success" | "failed" | "denied";
  parameter_summary: string;
  started_at?: string;
  completed_at?: string;
  elapsed_ms: number;
  source_count: number;
  data?: Record<string, unknown>;
  error?: string;
  receipt?: Record<string, unknown>;
}
export type PresentationModeResolved = "dialogue" | "scene";

export type InitiativeTrigger = "none" | "manual" | "idle_continuation" | "continuous_companionship";
export type VoiceInteractionMode = "call" | "face_to_face";

export interface VoiceInteractionContext {
  mode: VoiceInteractionMode;
  scene: string;
}

export interface SessionSummary {
  session_id: string;
  title: string;
  character_id?: string;
  mode?: "draw" | "custom";
  character_name?: string;
  character_avatar?: AvatarEntry;
  character_source?: CharacterSource;
  updated_at: string;
  message_count: number;
}

export interface SessionDocument {
  session_id: string;
  title: string;
  character_id?: string;
  mode?: "draw" | "custom";
  character?: CharacterSummary;
  messages: Message[];
}

export type CharacterSource = "draw" | "custom" | "imported" | "migrated";

export interface CharacterCardV2Data {
  name: string;
  description: string;
  personality: string;
  scenario: string;
  first_mes: string;
  mes_example: string;
  alternate_greetings: string[];
  tags: string[];
  creator: string;
  character_version: string;
  extensions: { mindspace?: { gender?: "男" | "女" | "不指定"; journey_id?: string; selected_card_ids?: string[]; tasks_v2?: Array<Record<string, unknown>> } };
}

export interface CharacterCardV2 {
  spec: "chara_card_v2";
  spec_version: "2.0";
  data: CharacterCardV2Data;
}

export interface CharacterSummary {
  character_id: string;
  schema_version: string;
  revision: number;
  source: CharacterSource;
  status: "active" | "archived";
  display_name: string;
  gender: "男" | "女" | "不指定";
  user_alias: string;
  relationship_label: string;
  avatar: AvatarEntry;
  created_at: string;
  updated_at: string;
  last_used_at: string;
  session_count?: number;
  latest_session_id?: string;
}

export interface CharacterRecord extends CharacterSummary {
  card: CharacterCardV2;
  memory: { preferences: string[]; tasks: string[] };
}

export interface SceneDefinition {
  scene_id: string;
  title: string;
  description: string;
  location: string;
  asset_id: string;
  asset_url?: string;
  custom?: boolean;
}

export interface ConversationScene {
  session_id: string;
  character_id: string;
  revision: number;
  scene: SceneDefinition | null;
  inherited_from_character: boolean;
  updated_at: string;
}

export interface StreamEnvelope<T = Record<string, unknown>> {
  version: string;
  event: string;
  seq: number;
  run_id: string;
  session_id: string;
  round: number;
  timestamp: string;
  data: T;
}

export interface LlmSettings {
  mode: string;
  provider?: string;
  base_url: string;
  model: string;
  credentials_configured?: boolean;
  api_key?: string;
  temperature: number;
  max_tokens: number;
  role_audit_enabled?: boolean;
  role_audit_model?: string;
}

export interface PersonaSettings {
  user_name: string;
  user_persona: string;
  character_name: string;
  system_prompt: string;
  reply_length_preference: string;
}

export interface RetrievalSettings {
  rag_enabled: boolean;
  knowledge_enabled: boolean;
  chat_enabled: boolean;
  structured_memory_enabled: boolean;
  temporal_enabled: boolean;
  knowledge_k: number;
  chat_k: number;
  history_k: number;
  similarity_threshold: number;
  decay_rounds: number;
  decay_hours: number;
  fairness_enabled: boolean;
  low_exposure_ratio: number;
  memory_family_limit: number;
  starvation_rounds: number;
  starvation_boost: number;
  bm25_enabled: boolean;
  vector_enabled: boolean;
  rrf_k: number;
  candidate_multiplier: number;
  max_total_boost: number;
  reranker_enabled: boolean;
  reranker_top_n: number;
  boosts?: object;
}

export interface KnowledgeSettings {
  parent_size: number;
  child_size: number;
  overlap: number;
}

export interface ProtocolSettings {
  mode: string;
  auto_repair: boolean;
  diagnostics: boolean;
}

export interface AudioSettings {
  asr_provider: string;
  asr_model: string;
  asr_endpoint: string;
  asr_silence_ms: number;
  asr_utterance_merge_ms: number;
  asr_listening_energy_threshold_db: number;
  asr_listening_min_speech_ms: number;
  asr_barge_in_energy_threshold_db: number;
  asr_barge_in_min_speech_ms: number;
  asr_candidate_release_ms: number;
  asr_adaptive_noise_enabled?: boolean;
  asr_noise_calibration_ms?: number;
  asr_listening_noise_margin_db?: number;
  asr_barge_in_noise_margin_db?: number;
  asr_deferred_during_playback: boolean;
  asr_hotwords_enabled: boolean;
  asr_dynamic_endpointing: boolean;
  asr_final_refinement_enabled: boolean;
  asr_auto_send?: boolean;
  asr_barge_in_cooldown_ms?: number;
  asr_duplicate_text_window_ms?: number;
  asr_false_candidate_backoff_ms?: number;
  tts_provider: string;
  tts_speed: number;
  auto_tts: boolean;
  tts_worker_url?: string;
  tts_reference_configured?: boolean;
  tts_reference_name?: string;
  tts_reference_text?: string;
  tts_gpt_sovits_voice?: string;
  tts_gpt_sovits_worker_url?: string;
  tts_qwen3_vllm_voice?: string;
  tts_qwen3_vllm_url?: string;
  tts_qwen3_vllm_model?: string;
  tts_siliconflow_base_url?: string;
  tts_siliconflow_api_key?: string;
  tts_siliconflow_credentials_configured?: boolean;
  tts_siliconflow_model?: string;
  tts_siliconflow_voice?: string;
  tts_siliconflow_sample_rate?: number;
  tts_siliconflow_gain?: number;
}

export interface InteractionSettings {
  voice_entry_mode?: VoiceInteractionMode;
  face_to_face_scene?: string;
  idle_continuation_enabled: boolean;
  text_idle_seconds: number;
  voice_idle_seconds: number;
  unlimited_reply_enabled: boolean;
  unlimited_reply_interval_seconds?: number;
  unlimited_reply_max_rounds: number;
}

export interface CapabilitySettings {
  master_enabled: boolean;
  local_knowledge_enabled: boolean;
  web_search_enabled: boolean;
  realtime_topics_enabled: boolean;
  topic_expansion_enabled: boolean;
  proactive_hotspots_enabled: boolean;
  show_sources_enabled: boolean;
  web_timeout_seconds: number;
  max_web_results: number;
  max_web_pages: number;
  max_web_content_chars: number;
}

export interface AppearanceSettings {
  theme: string;
  density: string;
  font_scale: number;
  language: string;
}

export interface ProductSettings {
  schema_version: string;
  llm: Partial<LlmSettings>;
  persona: Partial<PersonaSettings>;
  retrieval: Partial<RetrievalSettings>;
  knowledge: Partial<KnowledgeSettings>;
  protocol: Partial<ProtocolSettings>;
  audio: Partial<AudioSettings>;
  interaction?: Partial<InteractionSettings>;
  capabilities?: Partial<CapabilitySettings>;
  appearance: Partial<AppearanceSettings>;
}

export interface ProviderHttpAttempt {
  attempt: number;
  request_kind: string;
  status: "success" | "http_error" | "transport_error" | "empty" | "error";
  elapsed_ms: number;
  http_status: number | null;
  compatibility_variant: string;
  retry_reason: string;
  error: string;
}

export interface ModelCallRecord {
  kind: string;
  status: "success" | "degraded" | "denied" | "skipped";
  elapsed_ms: number;
  error: string;
}

export interface ModelDiagnostics {
  call_summary: ModelCallRecord[];
  total_calls: number;
  provider_attempts: ProviderHttpAttempt[];
  total_http_attempts: number;
}

export interface AudioStatus {
  asr_ready: boolean;
  tts_ready?: boolean;
  asr_error?: string;
  tts_error?: string;
  asr_detail?: {
    native_capture?: {
      available?: boolean;
      ready?: boolean;
      state?: string;
      error?: string;
      error_code?: string;
    };
  };
}

export interface ChatRetrievalRequest {
  rag_enabled: boolean;
  knowledge_enabled: boolean;
  chat_enabled: boolean;
  structured_memory_enabled: boolean;
  temporal_enabled: boolean;
  knowledge_k: number;
  chat_k: number;
  history_k: number;
  similarity_threshold: number;
  decay_rounds: number;
  decay_hours: number;
  fairness_enabled: boolean;
  low_exposure_ratio: number;
  memory_family_limit: number;
  starvation_rounds: number;
  starvation_boost: number;
  bm25_enabled: boolean;
  vector_enabled: boolean;
  rrf_k: number;
  candidate_multiplier: number;
  max_total_boost: number;
  reranker_enabled: boolean;
  reranker_top_n: number;
  boosts: object;
}

export interface ChatTurnRequest {
  message: string;
  session_id: string;
  character_id: string;
  reply_to_message_id: string;
  interactions: InteractionTag[];
  attachments: ChatAttachment[];
  activity_session_id: string;
  session_mode: "draw" | "custom";
  round: number;
  mode: "primary" | "regenerate";
  interaction_mode: "voice" | "text";
  presentation_mode: "auto";
  adult_mode: boolean;
  r18_style_id: string;
  initiative: boolean;
  initiative_trigger: InitiativeTrigger;
  initiative_sequence: number;
  initiative_sequence_limit: number;
  client_sent_at: string;
  client_timezone: string;
  client_utc_offset_minutes: number;
  voice_delivery: VoiceDeliveryState | null;
  voice_context: VoiceInteractionContext | null;
  input_evidence: {
    asr: {
      quality: "uncertain";
      confirmed_text: string;
      uncertain_segments: Array<{ text: string; reason: string }>;
      decision_reasons: string[];
    };
  } | null;
  user_name: string;
  user_persona: string;
  reply_length_preference: string;
  character_name: string;
  system_prompt: string;
  api: { temperature: number; max_tokens: number };
  retrieval: ChatRetrievalRequest;
}

export interface KnowledgeItem {
  chunk_id: string;
  text: string;
  source: string;
  created_at: string;
}

export interface DiagnosticReport {
  ok: boolean;
  app: Record<string, unknown>;
  paths: Record<string, string>;
  counts: Record<string, number>;
  audio: Record<string, unknown>;
  llm: Record<string, unknown>;
}

export interface InspectorEvent {
  event: string;
  label: string;
  timestamp: string;
  data?: unknown;
  state?: "active" | "done" | "error";
}

export type InspectorTab = "flow" | "context" | "prompt";

export interface PromptInspection {
  run_id: string;
  session_id: string;
  message_count: number;
  total_chars: number;
  estimated_tokens: number;
  sha256: string;
  revealed: boolean;
  layers: Array<{
    index: number;
    layer: string;
    role: string;
    chars: number;
    estimated_tokens: number;
    content: string;
  }>;
}

export interface AvatarEntry {
  src: string;
  aspect: "2 / 3" | "3 / 4" | "4 / 5" | "9 / 16" | "1 / 1";
  scale: number;
  x: number;
  y: number;
}

export interface AvatarConfig {
  user: AvatarEntry;
  assistant: AvatarEntry;
}

export type VoicePhase =
  | "idle"
  | "preparing"
  | "connecting"
  | "listening"
  | "user-speaking"
  | "collecting"
  | "deferred"
  | "transcribing"
  | "thinking"
  | "assistant-speaking"
  | "candidate-interruption"
  | "interrupted"
  | "error";

export interface ASRVocabularyEntry {
  id: string;
  term: string;
  aliases: string[];
  priority: "low" | "medium" | "high" | "critical";
  weight: number;
  scope: string;
  category: string;
  source: "manual" | "profile" | "system";
  source_field: string;
  enabled: boolean;
  hit_count: number;
  updated_at: string;
  read_only: boolean;
}

export interface ASRVocabularySnapshot {
  revision: string;
  manual_revision: number;
  profile_revisions: Record<string, number>;
  counts: Record<string, number>;
  entries: ASRVocabularyEntry[];
  decoder_hotwords: string[];
  explicit: Record<string, string>;
}

export interface VoiceSessionState {
  open: boolean;
  phase: VoicePhase;
  transcript: string;
  reply: string;
  level: number;
  error: string;
}

export interface VoiceDeliveryState {
  mode: "voice";
  run_id: string;
  assistant_message_id: string;
  delivery_status: "playing" | "completed" | "interrupted" | "cancelled";
  current_segment_id: string;
  played_audio_ms: number;
  heard_text: string;
  unheard_text: string;
  full_text_visible: boolean;
  position_confidence: number;
  interruption_cause: string;
}

export interface EmotionState {
  version: "1.0";
  turn_id: string;
  observed_at: string;
  window_ms: number;
  quality: {
    snr_db: number;
    voiced_ratio: number;
    clipping_ratio: number;
    echo_risk: number;
    usable: boolean;
  };
  acoustic: Record<string, unknown>;
  text: {
    valence: number;
    arousal: number;
    dominance: number;
    intent: string;
    needs: string[];
    emotion_distribution: Record<string, number>;
    confidence: number;
  } | null;
  fusion: {
    valence: number;
    arousal: number;
    dominance: number;
    emotion_distribution: Record<string, number>;
    confidence: number;
    agreement: number;
    conflicts: string[];
    response_guidance: {
      warmth: number;
      directness: number;
      pace: string;
      avoid: string[];
    };
  };
  persistence: "ephemeral_voice_turn";
  eligible_for_json_evidence: false;
}

export interface ProfileCardData {
  name: string;
  identity: Record<string, unknown>;
  personality: Record<string, unknown>;
  relationship: Record<string, unknown>;
  roleplay?: Record<string, unknown>;
  revision: number;
  updated_at: string;
}

export interface ProfileHistoryItem {
  version_id: string;
  revision: number;
  updated_at: string;
}

export interface MemoryItem {
  memory_key: string;
  field_code: string;
  display_name: string;
  category: string;
  value: string | number | boolean;
  scope: string;
  lifecycle: string;
  status: "active" | "invalidated";
  created_at: string;
  updated_at: string;
  invalidated_at?: string;
  reason?: string;
  session_id?: string;
  assistant_message_id?: string;
  source_text?: string;
}

export type EventMemoryCategory = "user_related" | "ai_related" | "relationship_related";

export interface EventMemoryItem {
  id: string;
  group: "pending" | "subject";
  category: EventMemoryCategory;
  title: string;
  summary: string;
  status: "active" | "completed" | "cancelled" | "replaced";
  due_at?: string | null;
  importance: number;
  created_at: string;
  updated_at: string;
  source_session_id?: string;
}

export interface EventMemorySnapshot {
  schema_version: string;
  character_id: string;
  revision: number;
  pending: EventMemoryItem[];
  subjects: Record<EventMemoryCategory, EventMemoryItem | null>;
  history: EventMemoryItem[];
  updated_at: string;
}

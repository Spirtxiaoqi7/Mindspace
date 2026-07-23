export type Role = "user" | "assistant";

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
  hidden?: boolean;
}

export type InitiativeTrigger = "none" | "manual" | "idle_continuation" | "continuous_companionship";
export type VoiceInteractionMode = "call" | "face_to_face";

export interface VoiceInteractionContext {
  mode: VoiceInteractionMode;
  scene: string;
}

export interface SessionSummary {
  session_id: string;
  title: string;
  updated_at: string;
  message_count: number;
}

export interface SessionDocument {
  session_id: string;
  title: string;
  messages: Message[];
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

export interface ProductSettings {
  schema_version: string;
  llm: Record<string, unknown>;
  persona: Record<string, unknown>;
  retrieval: Record<string, unknown>;
  knowledge: Record<string, unknown>;
  protocol: Record<string, unknown>;
  audio: Record<string, unknown>;
  interaction?: Record<string, unknown>;
  capabilities?: Record<string, unknown>;
  appearance: Record<string, unknown>;
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

"""Validated boundary models for one conversational turn."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ApiConfig(BaseModel):
    """OpenAI-compatible endpoint configuration kept outside prompt text."""

    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=64, le=32768)


class RetrievalSettings(BaseModel):
    rag_enabled: bool = True
    # This is a server-owned runtime gate.  ConversationService overwrites both
    # fields before the graph starts, so a client cannot force a cold retriever
    # onto the foreground path.
    ready: bool = True
    deferred_reason: str = Field(default="", max_length=100)
    knowledge_enabled: bool = True
    chat_enabled: bool = True
    structured_memory_enabled: bool = True
    temporal_enabled: bool = True
    knowledge_k: int = Field(default=2, ge=1, le=20)
    chat_k: int = Field(default=3, ge=1, le=30)
    history_k: int = Field(default=3, ge=1, le=30)
    similarity_threshold: float = Field(default=0.5, ge=0, le=1)
    decay_rounds: float = Field(default=20, ge=1, le=500)
    decay_hours: float = Field(default=168, ge=1, le=8760)
    fairness_enabled: bool = True
    low_exposure_ratio: float = Field(default=0.2, ge=0, le=0.5)
    memory_family_limit: int = Field(default=2, ge=1, le=10)
    starvation_rounds: int = Field(default=6, ge=1, le=100)
    starvation_boost: float = Field(default=0.12, ge=0, le=0.5)
    bm25_enabled: bool = True
    vector_enabled: bool = True
    rrf_k: int = Field(default=60, ge=1, le=500)
    candidate_multiplier: int = Field(default=4, ge=2, le=12)
    max_total_boost: float = Field(default=0.25, ge=0, le=0.5)
    knowledge_user_boost: float = Field(default=0.08, ge=0, le=0.25)
    knowledge_character_boost: float = Field(default=0.08, ge=0, le=0.25)
    knowledge_source_boost: float = Field(default=0.05, ge=0, le=0.25)
    chat_session_boost: float = Field(default=0.15, ge=0, le=0.25)
    chat_exact_boost: float = Field(default=0.10, ge=0, le=0.25)
    reranker_enabled: bool = False
    reranker_top_n: int = Field(default=12, ge=1, le=50)

    @model_validator(mode="before")
    @classmethod
    def expand_legacy_boosts(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("boosts"), dict):
            return value
        expanded = dict(value)
        boosts = value["boosts"]
        mapping = {
            "knowledge_user": "knowledge_user_boost",
            "knowledge_character": "knowledge_character_boost",
            "knowledge_source": "knowledge_source_boost",
            "chat_session": "chat_session_boost",
            "chat_exact": "chat_exact_boost",
        }
        for source, target in mapping.items():
            if source in boosts and target not in expanded:
                expanded[target] = boosts[source]
        return expanded


class ASRUncertainSegment(BaseModel):
    """A non-authoritative ASR alternative that must never become stored user text."""

    text: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="low_confidence", max_length=100)


class ASRInputEvidence(BaseModel):
    """Ephemeral recognition evidence accompanying an already confirmed utterance."""

    quality: Literal["accepted", "uncertain"] = "accepted"
    confirmed_text: str = Field(default="", max_length=10_000)
    uncertain_segments: list[ASRUncertainSegment] = Field(default_factory=list, max_length=8)
    decision_reasons: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def require_confirmed_backbone(self) -> ASRInputEvidence:
        if self.uncertain_segments and not self.confirmed_text.strip():
            raise ValueError("uncertain ASR segments require confirmed_text")
        return self


class InputEvidence(BaseModel):
    """Transport-only evidence. It is intentionally excluded from durable user content."""

    asr: ASRInputEvidence | None = None


class VoiceInteractionContext(BaseModel):
    """User-selected presentation context for one live voice session.

    The scene is transport-only context. It may shape the current response, but
    it is never authoritative profile evidence and must not become durable
    conversation history by itself.
    """

    mode: Literal["call", "face_to_face"] = "call"
    scene: str = Field(default="", max_length=2_000)

    @field_validator("scene")
    @classmethod
    def trim_scene(cls, value: str) -> str:
        return value.strip()


class ActivityPromptContext(BaseModel):
    """Server-resolved activity state; clients may send only its session id."""

    activity_session_id: str = Field(min_length=1, max_length=100)
    activity_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    phase: str = Field(default="", max_length=100)
    status: Literal["active", "completed", "interrupted"] = "active"
    state: dict[str, Any] = Field(default_factory=dict)
    rules: list[str] = Field(default_factory=list, max_length=12)
    visibility: Literal["ephemeral_activity_session"] = "ephemeral_activity_session"
    eligible_for_json_evidence: Literal[False] = False


class ScenePromptContext(BaseModel):
    """Server-resolved visual scene for the current conversation."""

    scene_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    location: str = Field(min_length=1, max_length=240)
    asset_id: str = Field(min_length=1, max_length=120)
    visibility: Literal["ephemeral_conversation_scene"] = "ephemeral_conversation_scene"
    eligible_for_json_evidence: Literal[False] = False


class ChatInteraction(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    category: Literal["daily", "touch", "kiss", "custom"]
    level: int = Field(default=0, ge=0, le=99)
    action: str = Field(min_length=1, max_length=40)
    target: str = Field(default="", max_length=40)
    sensitivity: Literal["normal", "intimate"] = "normal"

    @field_validator("id", "action", "target")
    @classmethod
    def trim_interaction_text(cls, value: str) -> str:
        return value.strip()


class ChatAttachment(BaseModel):
    attachment_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    media_type: str = Field(default="text/plain", min_length=1, max_length=120)
    size: int = Field(default=0, ge=0, le=1_048_576)
    content: str = Field(default="", max_length=20_000)

    @field_validator("attachment_id", "name", "media_type")
    @classmethod
    def trim_attachment_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("attachment fields must not be blank")
        return value


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=10_000)
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    character_id: str = Field(default="", max_length=64)
    session_mode: Literal["draw", "custom"] = "custom"
    round: int = Field(default=1, ge=1)
    mode: Literal["primary", "regenerate"] = "primary"
    interaction_mode: Literal["text", "voice"] = "text"
    presentation_mode: Literal["auto", "dialogue", "scene"] = "auto"
    voice_tts_provider: Literal["browser", "mock", "cosyvoice", "siliconflow", "gpt-sovits", "qwen3-vllm"] = "browser"
    adult_mode: bool = False
    r18_style_id: str = Field(default="high_intensity", min_length=1, max_length=64)
    initiative: bool = False
    initiative_trigger: Literal["none", "manual", "idle_continuation", "continuous_companionship"] = "none"
    initiative_sequence: int = Field(default=0, ge=0, le=50)
    initiative_sequence_limit: int = Field(default=0, ge=0, le=50)
    client_sent_at: datetime | None = None
    client_timezone: str = "UTC"
    client_utc_offset_minutes: int = Field(default=0, ge=-840, le=840)
    server_received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    voice_delivery: VoiceDeliveryState | None = None
    voice_context: VoiceInteractionContext | None = None
    activity_session_id: str = Field(default="", max_length=100)
    # Always overwritten by ConversationService from the authoritative activity
    # repository. It is present on the request model only so downstream nodes
    # receive a typed, immutable snapshot for this run.
    activity_context: ActivityPromptContext | None = None
    # Resolved only from the server-side session binding. The client cannot
    # inject an arbitrary scene sentence into the model input.
    scene_context: ScenePromptContext | None = None
    input_evidence: InputEvidence | None = None
    reply_to_message_id: str = Field(default="", max_length=64)
    reply_context: str = Field(default="", max_length=2_000)
    interactions: list[ChatInteraction] = Field(default_factory=list, max_length=12)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=5)
    user_name: str = "用户"
    user_persona: str = ""
    reply_length_preference: str = Field(default="", max_length=300)
    character_name: str = "AI助手"
    system_prompt: str = ""
    api: ApiConfig = Field(default_factory=ApiConfig)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)

    @field_validator("message")
    @classmethod
    def trim_message(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_turn_payload(self) -> ChatRequest:
        if not self.initiative and not self.message and not self.interactions and not self.attachments:
            raise ValueError("message, interaction, or attachment is required")
        if not self.adult_mode and any(item.sensitivity == "intimate" for item in self.interactions):
            raise ValueError("intimate interactions require adult_mode")
        if sum(len(item.content) for item in self.attachments) > 40_000:
            raise ValueError("attachment text exceeds the per-turn limit")
        return self

    def idempotency_digest(self) -> str:
        """Hash every client-controlled field that can change one turn's behavior.

        Server-owned resolution data is intentionally excluded because it is
        rebuilt from the durable session when a request is accepted.  Keeping
        this policy with the boundary model prevents service code from drifting
        into a partial hand-written field allowlist.
        """

        server_owned = {
            "server_received_at",
            "activity_context",
            "scene_context",
            "reply_context",
            "user_name",
            "user_persona",
            "reply_length_preference",
            "character_name",
            "system_prompt",
            "api",
            "retrieval",
        }
        payload = self.model_dump(
            mode="json",
            exclude=server_owned,
            exclude_none=True,
        )
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class VoiceDeliveryState(BaseModel):
    """Ephemeral voice-only evidence about what audio likely reached the user."""

    mode: Literal["voice"] = "voice"
    run_id: str = ""
    assistant_message_id: str = ""
    delivery_status: Literal["playing", "completed", "interrupted", "cancelled"]
    current_segment_id: str = ""
    played_audio_ms: int = Field(default=0, ge=0)
    heard_text: str = Field(default="", max_length=10_000)
    unheard_text: str = Field(default="", max_length=10_000)
    full_text_visible: bool = True
    position_confidence: float = Field(default=0, ge=0, le=1)
    interruption_cause: str = Field(default="", max_length=100)


class RetrievedChunk(BaseModel):
    chunk_id: str
    text: str
    source: Literal["knowledge", "chat", "memory"]
    score: float = Field(ge=0)
    session_id: str | None = None
    round_num: int = 1
    physical_time: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    temporal_weight: float = 1.0
    weighted_score: float = 0.0


class ProfileBundle(BaseModel):
    user_profile: dict[str, Any] = Field(default_factory=dict)
    ai_profile: dict[str, Any] = Field(default_factory=dict)
    runtime_state: dict[str, Any] = Field(default_factory=dict)
    character_memory: dict[str, Any] = Field(default_factory=dict)
    revisions: dict[str, int] = Field(default_factory=dict)


class JsonPatch(BaseModel):
    target: Literal["user_profile", "ai_profile", "runtime_state", "character_memory"]
    op: Literal["add", "replace", "remove"]
    path: str
    value: Any | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class JsonUpdatePlan(BaseModel):
    turn_id: str = "round_current"
    base_revisions: dict[str, int] = Field(default_factory=dict)
    trigger: Literal[
        "current_user",
        "current_agent",
        "profile_bootstrap",
        "deletion_reconciliation",
        "none",
    ] = "none"
    patches: list[JsonPatch] = Field(default_factory=list)


class ProtocolOutput(BaseModel):
    response: str = Field(min_length=1)
    json_update: JsonUpdatePlan


class RoleValidation(BaseModel):
    is_valid: bool
    layer: str = "all"
    message: str = ""
    suggestion: str = ""
    confidence: float = Field(default=1, ge=0, le=1)


class ModelUsage(BaseModel):
    provider: str = "openai-compatible"
    model: str = ""
    request_kind: Literal[
        "generation",
        "repair",
        "compaction",
        "role_audit",
        "task_review",
        "final_generation",
        "emotion_post",
        "memory_extract",
        "character_generate",
        "destiny_archetypes",
        "destiny_cards",
        "destiny_synthesis",
        "destiny_synthesis_repair",
    ] = "generation"
    prompt_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cache_source: str = "unreported"
    requested_max_tokens: int = Field(default=0, ge=0)
    finish_reason: str = ""


class ModelCallRecord(BaseModel):
    kind: Literal[
        "generation",
        "task_review",
        "final_generation",
        "protocol_repair",
        "memory_extract",
    ]
    status: Literal["success", "degraded", "denied", "skipped"]
    elapsed_ms: float = Field(default=0, ge=0)
    error: str = Field(default="", max_length=500)


class ProviderHttpAttempt(BaseModel):
    """One real HTTP request issued to an LLM provider."""

    attempt: int = Field(ge=1)
    request_kind: str = Field(min_length=1, max_length=64)
    status: Literal["success", "http_error", "transport_error", "empty", "error"]
    elapsed_ms: float = Field(default=0, ge=0)
    http_status: int | None = Field(default=None, ge=100, le=599)
    compatibility_variant: str = Field(default="", max_length=80)
    retry_reason: str = Field(default="", max_length=160)
    error: str = Field(default="", max_length=500)


class ModelDiagnostics(BaseModel):
    call_summary: list[ModelCallRecord] = Field(default_factory=list)
    total_calls: int = Field(default=0, ge=0, le=5)
    provider_attempts: list[ProviderHttpAttempt] = Field(default_factory=list)
    total_http_attempts: int = Field(default=0, ge=0)


class RoleAuditResult(BaseModel):
    is_consistent: bool = True
    severity: Literal["none", "style", "identity", "boundary", "reality"] = "none"
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=5)
    next_turn_instruction: str = Field(default="", max_length=500)
    recent_event_summary: str = Field(default="", max_length=600)
    event_progression: str = Field(default="", max_length=600)
    open_threads: list[str] = Field(default_factory=list, max_length=5)


class JsonUpdateValidation(BaseModel):
    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    normalized_plan: JsonUpdatePlan | None = None


class DeletionEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    turn_id: str
    round: int
    message_id: str
    role: Literal["assistant"] = "assistant"
    deleted_content: str
    deleted_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    associated_write_receipt: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "resolved"] = "pending"


class JsonWriteReceipt(BaseModel):
    turn_id: str
    applied: bool = False
    patches: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    session_id: str
    round: int
    status: Literal["success", "error"]
    reply: str = ""
    assistant_message_id: str = ""
    presentation_mode: Literal["dialogue", "scene"] = "dialogue"
    writeback_applied: bool = False
    retrieval_counts: dict[str, int] = Field(default_factory=dict)
    tool_execution: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    llm_call_count: int = 0
    model_usage: list[ModelUsage] = Field(default_factory=list)
    model: ModelDiagnostics = Field(default_factory=ModelDiagnostics)
    writeback_context: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
    completed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

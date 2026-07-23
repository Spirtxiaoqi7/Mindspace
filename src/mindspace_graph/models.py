"""Validated boundary models for one conversational turn."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ApiConfig(BaseModel):
    """OpenAI-compatible endpoint configuration kept outside prompt text."""

    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=2000, ge=64, le=8192)


class RetrievalSettings(BaseModel):
    rag_enabled: bool = True
    knowledge_enabled: bool = True
    chat_enabled: bool = True
    structured_memory_enabled: bool = True
    temporal_enabled: bool = True
    knowledge_k: int = Field(default=5, ge=1, le=20)
    chat_k: int = Field(default=10, ge=1, le=30)
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


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    round: int = Field(default=1, ge=1)
    mode: Literal["primary", "regenerate"] = "primary"
    interaction_mode: Literal["text", "voice"] = "text"
    initiative: bool = False
    initiative_trigger: Literal[
        "none", "manual", "idle_continuation", "continuous_companionship"
    ] = "none"
    initiative_sequence: int = Field(default=0, ge=0, le=50)
    initiative_sequence_limit: int = Field(default=0, ge=0, le=50)
    client_sent_at: datetime | None = None
    client_timezone: str = "UTC"
    client_utc_offset_minutes: int = Field(default=0, ge=-840, le=840)
    server_received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    voice_delivery: VoiceDeliveryState | None = None
    voice_context: VoiceInteractionContext | None = None
    voice_emotion_tokens: list[str] = Field(default_factory=list, max_length=8)
    input_evidence: InputEvidence | None = None
    user_name: str = "用户"
    user_persona: str = ""
    character_name: str = "AI助手"
    system_prompt: str = ""
    api: ApiConfig = Field(default_factory=ApiConfig)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)

    @field_validator("message")
    @classmethod
    def trim_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


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
    revisions: dict[str, int] = Field(default_factory=dict)


class JsonPatch(BaseModel):
    target: Literal["user_profile", "ai_profile", "runtime_state"]
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
        "generation", "repair", "compaction", "role_audit", "capability_plan", "preflight",
        "research_review", "emotion_post", "memory_extract"
    ] = "generation"
    prompt_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cache_source: str = "unreported"


class ModelCallRecord(BaseModel):
    kind: Literal[
        "planner",
        "research_review",
        "generation",
        "protocol_repair",
        "memory_extract",
    ]
    status: Literal["success", "degraded", "skipped"]
    elapsed_ms: float = Field(default=0, ge=0)
    error: str = Field(default="", max_length=500)


class ModelDiagnostics(BaseModel):
    call_summary: list[ModelCallRecord] = Field(default_factory=list)
    total_calls: int = Field(default=0, ge=0, le=5)


class RoleAuditResult(BaseModel):
    is_consistent: bool = True
    severity: Literal["none", "style", "identity", "boundary", "reality"] = "none"
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=5)
    next_turn_instruction: str = Field(default="", max_length=500)


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
    writeback_applied: bool = False
    retrieval_counts: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    llm_call_count: int = 0
    model_usage: list[ModelUsage] = Field(default_factory=list)
    model: ModelDiagnostics = Field(default_factory=ModelDiagnostics)
    completed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

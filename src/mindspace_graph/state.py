"""The explicit, serializable state passed between LangGraph nodes."""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from mindspace_graph.emotion import EmotionState, TextEmotionState
from mindspace_graph.tool_chain import ToolExecutionResult, ToolInstruction
from mindspace_graph.models import (
    ChatRequest,
    ChatResponse,
    DeletionEvent,
    JsonUpdatePlan,
    JsonUpdateValidation,
    ModelUsage,
    ProfileBundle,
    ProtocolOutput,
    RetrievedChunk,
    RoleValidation,
)
from mindspace_graph.profile_bootstrap import ProfileBootstrap


class TurnState(TypedDict, total=False):
    request_id: str
    request: ChatRequest
    profiles: ProfileBundle
    recent_history: list[dict[str, Any]]
    retrieval_query: str
    retrieval_query_mode: str
    adult_recall_opened: bool
    chat_chunks: list[RetrievedChunk]
    ranked_context: list[RetrievedChunk]
    deletion_events: list[DeletionEvent]
    profile_bootstrap: ProfileBootstrap
    tool_hint: str
    tool_instruction: ToolInstruction
    tool_result: ToolExecutionResult
    tool_parse_error: str
    task_review_allowed: bool
    task_review_reason: str
    text_emotion: TextEmotionState
    emotion_state: EmotionState
    prompt_messages: list[dict[str, str]]
    prompt_pending_events: list[dict[str, Any]]
    context_epoch_id: int
    context_estimated_tokens: int
    context_emergency_truncated: bool
    model_usage: list[ModelUsage]
    llm_call_count: int
    llm_call_counts: dict[str, int]
    model_call_summary: list[dict[str, Any]]
    raw_candidate: str
    fallback_response: str
    protocol: ProtocolOutput
    protocol_errors: list[str]
    protocol_attempts: int
    role_validation: RoleValidation
    role_attempts: int
    json_update_plan: JsonUpdatePlan
    json_update_validation: JsonUpdateValidation
    writeback_applied: bool
    response: ChatResponse
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[str], operator.add]

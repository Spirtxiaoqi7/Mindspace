"""Public HTTP contracts for chat endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from mindspace_graph import models as model_types
from mindspace_graph.models import (
    ActivityPromptContext,
    ApiConfig,
    ChatRequest,
    ScenePromptContext,
)


class ChatTurnCreateRequest(ChatRequest):
    """Public chat input with server-resolved fields hidden from OpenAPI.

    The hidden fields remain typed compatibility inputs so older clients keep
    their existing validation and idempotency behavior. ConversationService
    still replaces every one of them with authoritative server state.
    """

    session_mode: SkipJsonSchema[Literal["draw", "custom"]] = "custom"
    voice_tts_provider: SkipJsonSchema[
        Literal["browser", "mock", "cosyvoice", "siliconflow", "gpt-sovits", "qwen3-vllm"]
    ] = "browser"
    server_received_at: SkipJsonSchema[datetime] = Field(default_factory=lambda: datetime.now(UTC))
    activity_context: SkipJsonSchema[ActivityPromptContext | None] = None
    scene_context: SkipJsonSchema[ScenePromptContext | None] = None
    reply_context: SkipJsonSchema[str] = Field(default="", max_length=2_000)
    system_prompt: SkipJsonSchema[str] = ""
    api: SkipJsonSchema[ApiConfig] = Field(default_factory=ApiConfig)

    def to_internal(self) -> ChatRequest:
        """Convert the validated HTTP boundary object into the workflow model."""

        return ChatRequest.model_validate(self.model_dump())


# ChatRequest deliberately uses postponed annotations for types declared later
# in models.py. A subclass defined in this HTTP-contract module needs the
# original model namespace when Pydantic builds FastAPI's schema.
ChatTurnCreateRequest.model_rebuild(
    _types_namespace={**vars(model_types), **globals()},
)

__all__ = ["ChatTurnCreateRequest"]

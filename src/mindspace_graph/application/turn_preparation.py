"""Prepare client chat input with authoritative server-side turn state."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from mindspace_graph.models import (
    ActivityPromptContext,
    ApiConfig,
    ChatRequest,
    ScenePromptContext,
)
from mindspace_graph.ports import Dependencies
from mindspace_graph.role_runtime import build_runtime_role_state
from mindspace_graph.roleplay import effective_roleplay_max_tokens, effective_roleplay_temperature
from mindspace_graph.settings import AppSettings

RETRIEVAL_INDEX_ONLY_ROUNDS = 15
_EXPLICIT_RECALL = re.compile(r"(?:查(?:一下)?知识库|检索知识库|回忆(?:一下)?|你还记得|帮我想起|找回以前)")


class TurnPreparationService:
    """Resolve every server-owned field before a durable turn is created."""

    def __init__(
        self,
        settings: AppSettings,
        dependencies: Dependencies,
        *,
        retrieval_is_ready: Callable[[str, str], bool],
    ) -> None:
        self.settings = settings
        self.dependencies = dependencies
        self._retrieval_is_ready = retrieval_is_ready

    def prepare(
        self,
        request: ChatRequest,
        session_snapshot: list[dict[str, Any]] | None = None,
    ) -> ChatRequest:
        """用服务端模型地址/密钥/模型名覆盖客户端值，只保留本轮采样参数。"""

        history = (
            list(session_snapshot)
            if session_snapshot is not None
            else self.dependencies.sessions.load_all(request.session_id)
        )

        characters = self.dependencies.characters
        if characters is None:
            raise RuntimeError("character repository is unavailable")
        character = characters.get(request.character_id) if request.character_id else characters.default()
        if character.get("status") != "active":
            raise ValueError("selected character is archived")
        character_id = str(character["character_id"])
        session_mode = "draw" if character.get("source") == "draw" else "custom"
        profiles = self.dependencies.profiles.load_bundle(character_id)
        current_role_state = build_runtime_role_state(
            ai_profile=profiles.ai_profile,
            character_memory=profiles.character_memory,
            user_profile=profiles.user_profile,
            request_user_name=request.user_name,
            request_character_name=request.character_name,
        )
        session = self.dependencies.sessions.ensure_session(
            request.session_id,
            character_id=character_id,
            mode=session_mode,
            role_state=current_role_state,
        )
        session_role_state = session.get("role_state")
        if not isinstance(session_role_state, dict):
            session_role_state = current_role_state
        user_identity = profiles.user_profile.get("identity", {})
        ai_identity = profiles.ai_profile.get("identity", {})
        reply_context = ""
        if request.reply_to_message_id:
            for item in reversed(history):
                if str(item.get("message_id") or "") != request.reply_to_message_id or item.get("hidden"):
                    continue
                role_label = "用户" if item.get("role") == "user" else str(ai_identity.get("name") or "角色")
                content = str(item.get("content") or "").strip()
                if content:
                    reply_context = f"{role_label}：{content[:1800]}"
                break
        characters.touch(character_id)
        activity_context = None
        if request.activity_session_id:
            activities = self.dependencies.activities
            if activities is None:
                raise RuntimeError("activity service is unavailable")
            activity_context = ActivityPromptContext.model_validate(
                activities.prompt_context(request.activity_session_id, character_id=character_id)
            )
        scene_context = None
        if self.dependencies.activities is not None:
            resolved_scene = self.dependencies.activities.scene_prompt_context(
                request.session_id, character_id=character_id
            )
            if resolved_scene is not None:
                scene_context = ScenePromptContext.model_validate(resolved_scene)
        api = ApiConfig(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            model=self.settings.llm_model,
            temperature=effective_roleplay_temperature(
                request,
                history,
            ),
            max_tokens=effective_roleplay_max_tokens(request, history),
        )
        explicit_recall = bool(_EXPLICIT_RECALL.search(request.message))
        default_retrieval_open = request.round > RETRIEVAL_INDEX_ONLY_ROUNDS
        index_ready = self._retrieval_is_ready(request.session_id, character_id)
        retrieval_ready = bool(
            not request.retrieval.rag_enabled or (index_ready and (default_retrieval_open or explicit_recall))
        )
        if retrieval_ready:
            deferred_reason = ""
        elif request.round <= RETRIEVAL_INDEX_ONLY_ROUNDS:
            deferred_reason = "index_build_only_first_15_rounds"
        else:
            deferred_reason = "index_warmup_pending"
        retrieval = request.retrieval.model_copy(
            update={
                "ready": retrieval_ready,
                "deferred_reason": deferred_reason,
            }
        )
        return request.model_copy(
            update={
                "api": api,
                "server_received_at": datetime.now(UTC),
                "voice_tts_provider": self.settings.tts_provider,
                "character_id": character_id,
                "session_mode": session_mode,
                "user_name": str(
                    session_role_state.get("user_name") or user_identity.get("preferred_name") or request.user_name
                ),
                "character_name": str(
                    session_role_state.get("character_name") or ai_identity.get("name") or request.character_name
                ),
                "system_prompt": str(character.get("system_prompt") or ""),
                "activity_context": activity_context,
                "scene_context": scene_context,
                "reply_context": reply_context,
                "retrieval": retrieval,
            }
        )


__all__ = ["TurnPreparationService"]

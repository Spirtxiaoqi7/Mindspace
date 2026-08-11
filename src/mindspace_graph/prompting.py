"""Role-first prompt assembly kept separate from orchestration and model I/O."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mindspace_graph.context_ledger import ContextLedger, ContextSnapshot
from mindspace_graph.emotion import EmotionState
from mindspace_graph.models import ChatRequest, DeletionEvent, ProfileBundle, RetrievedChunk
from mindspace_graph.native_tools import NATIVE_TOOL_GUIDANCE
from mindspace_graph.profile_bootstrap import ProfileBootstrap
from mindspace_graph.prompt_contributors import build_static_prompt_messages, compile_prompt_messages
from mindspace_graph.prompt_event_templates import (
    build_activity_context_template,
    build_asr_uncertain_evidence_template,
    build_continuous_companionship_control_line,
    build_current_user_attachments_suffix,
    build_current_user_interaction_suffix,
    build_current_user_reply_context_suffix,
    build_current_user_template,
    build_direct_response_control_line,
    build_event_memory_template,
    build_face_to_face_context_template,
    build_hidden_emotion_template,
    build_idle_continuation_control_line,
    build_initiative_control_line,
    build_qwen3_tts_control_lines,
    build_scene_context_template,
    build_streaming_voice_control_lines,
    build_unconfirmed_state_control_line,
    build_voice_delivery_control_lines,
    build_voice_disabled_control_line,
    build_voice_enabled_control_line,
)
from mindspace_graph.prompt_templates import (
    build_attachment_item_template,
    build_attachments_template,
    build_authoritative_state_template,
    build_contract_template,
    build_persona_template,
    build_physical_time_control_lines,
    build_post_history_template,
    build_quick_interaction_template,
    build_reply_context_template,
    join_turn_data_templates,
)
from mindspace_graph.r18_director import ADULT_ROLEPLAY_PROTOCOL
from mindspace_graph.role_runtime import (
    build_runtime_role_state,
    compact_system_prompt,
    compact_turn_directive,
)
from mindspace_graph.roleplay import (
    build_roleplay_layer,
    companion_lane,
    project_history_for_presentation,
    resolve_presentation_mode,
)


@dataclass(slots=True)
class PromptBuild:
    messages: list[dict[str, str]]
    pending_events: list[dict[str, Any]]
    context_snapshot: ContextSnapshot | None = None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _role_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, stable character card without loading all examples."""

    def prune(value: Any) -> Any:
        if isinstance(value, dict):
            result = {key: prune(item) for key, item in value.items()}
            return {key: item for key, item in result.items() if item not in ("", None, [], {})}
        if isinstance(value, list):
            items = (prune(item) for item in value)
            return [item for item in items if item not in ("", None, [], {})]
        return value

    selected = {
        key: profile.get(key)
        for key in ("identity", "personality", "relationship_rules", "behavior_rules", "continuity")
    }
    roleplay = profile.get("roleplay")
    if isinstance(roleplay, dict):
        selfhood = roleplay.get("selfhood", {})
        voice = roleplay.get("voice", {})
        selected["roleplay_core"] = {
            "selfhood": {
                key: selfhood.get(key)
                for key in ("values", "flaws", "contradictions", "personal_goals")
                if isinstance(selfhood, dict)
            },
            "voice": {
                key: voice.get(key)
                for key in ("cadence", "disliked_phrases", "humor_style", "action_dialogue_balance")
                if isinstance(voice, dict)
            },
        }
    return prune(selected)


def _normalized_prompt_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _deduplicate_retrieval_context(
    context: list[RetrievedChunk],
    history: list[dict[str, Any]],
) -> list[RetrievedChunk]:
    """Do not send a directly visible raw chat message twice as history and RAG."""

    history_ids = {str(item.get("message_id") or "").strip() for item in history if not item.get("hidden")}
    history_texts = {
        _normalized_prompt_text(item.get("content"))
        for item in history
        if not item.get("hidden") and item.get("role") in {"user", "assistant"}
    }
    return [
        item
        for item in context
        if not (
            item.source == "chat"
            and (item.chunk_id in history_ids or _normalized_prompt_text(item.text) in history_texts)
        )
    ]


_QUICK_INTERACTION = re.compile(r"@互动[：:]\s*([^\s@，。！？!?]{1,20})")
_NORMAL_INTERACTIONS = frozenset({"摸头", "拥抱", "牵手", "贴近", "亲吻"})
_FEMALE_ADULT_INTERACTIONS = frozenset({"奶子", "阴蒂"})
_MALE_ADULT_INTERACTIONS = frozenset({"鸡巴", "龟头"})


def _quick_interaction_directive(request: ChatRequest, profiles: ProfileBundle) -> str:
    gender = str(profiles.ai_profile.get("identity", {}).get("gender") or "不指定")
    allowed = set(_NORMAL_INTERACTIONS)
    if request.adult_mode and gender == "女":
        allowed.update(_FEMALE_ADULT_INTERACTIONS)
    elif request.adult_mode and gender == "男":
        allowed.update(_MALE_ADULT_INTERACTIONS)
    actions: list[str] = []
    for item in request.interactions:
        if item.sensitivity == "intimate" and not request.adult_mode:
            continue
        if gender == "女" and item.target in _MALE_ADULT_INTERACTIONS:
            continue
        if gender == "男" and item.target in _FEMALE_ADULT_INTERACTIONS:
            continue
        rendered = item.action + (f"-{item.target}" if item.target else "")
        if rendered not in actions:
            actions.append(rendered)
    if not actions:
        for action in _QUICK_INTERACTION.findall(request.message):
            if action in allowed and action not in actions:
                actions.append(action)
            if len(actions) >= 4:
                break
    if not actions:
        return ""
    return build_quick_interaction_template(tuple(actions))


def _turn_data_directive(request: ChatRequest) -> str:
    blocks: list[str] = []
    if request.reply_context:
        blocks.append(build_reply_context_template(request.reply_context))
    if request.attachments:
        rendered = tuple(
            build_attachment_item_template(item.name, item.media_type, item.content) for item in request.attachments
        )
        blocks.append(build_attachments_template(rendered))
    return join_turn_data_templates(tuple(blocks))


def _post_history_role_directive(
    request: ChatRequest,
    profiles: ProfileBundle,
    history: list[dict[str, Any]],
    tool_hint: str = "",
) -> str:
    """Build a compact final acting note, equivalent to a character PHI."""

    if not request.adult_mode:
        directive = compact_turn_directive(
            build_runtime_role_state(
                ai_profile=profiles.ai_profile,
                character_memory=profiles.character_memory,
                user_profile=profiles.user_profile,
                request_user_name=request.user_name,
                request_character_name=request.character_name,
            )
        )
        interaction = _quick_interaction_directive(request, profiles)
        turn_data = _turn_data_directive(request)
        result = "\n".join(item for item in (directive, interaction, turn_data) if item)
        return result

    character_name = str(
        profiles.ai_profile.get("identity", {}).get("name") or request.character_name or "当前角色"
    ).strip()
    roleplay_layer = build_roleplay_layer(request, profiles, history)
    presentation_mode = resolve_presentation_mode(request, history)
    interaction = _quick_interaction_directive(request, profiles)
    turn_data = _turn_data_directive(request)
    recent_adult_context = any(
        bool(item.get("adult_mode")) or str(item.get("companion_lane") or "") == "ADULT"
        for item in history[-8:]
        if not item.get("hidden")
    )
    return build_post_history_template(
        character_name=character_name,
        interaction_mode=request.interaction_mode,
        presentation_mode=presentation_mode,
        roleplay_layer_json=_json(roleplay_layer),
        interaction=interaction,
        turn_data=turn_data,
        adult_mode=request.adult_mode,
        adult_protocol=ADULT_ROLEPLAY_PROTOCOL if request.adult_mode else "",
        recent_adult_context=recent_adult_context,
        reply_length_preference=request.reply_length_preference,
    )


def _history_round(item: dict[str, Any]) -> int | None:
    try:
        value = int(item.get("round", 0))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _client_zone(request: ChatRequest) -> tuple[Any, str]:
    try:
        return ZoneInfo(request.client_timezone), request.client_timezone
    except (ZoneInfoNotFoundError, ValueError):
        timezone_name = request.client_timezone.strip() or f"UTC{request.client_utc_offset_minutes / 60:+g}"
        return (
            timezone(timedelta(minutes=request.client_utc_offset_minutes)),
            timezone_name,
        )


def _local_physical_time(value: Any, request: ChatRequest) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return ""
    local_zone, _ = _client_zone(request)
    return parsed.astimezone(local_zone).isoformat(timespec="seconds")


def _time_state(request: ChatRequest, history: list[dict[str, Any]]) -> dict[str, Any]:
    del history
    now = request.server_received_at
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    local_zone, timezone_name = _client_zone(request)
    local_now = now.astimezone(local_zone)
    hour = local_now.hour
    time_period = (
        "凌晨"
        if hour < 5
        else "早晨"
        if hour < 9
        else "上午"
        if hour < 12
        else "中午"
        if hour < 14
        else "下午"
        if hour < 18
        else "晚上"
        if hour < 23
        else "深夜"
    )

    return {
        "current_local_datetime": local_now.isoformat(timespec="seconds"),
        "time_period": time_period,
        "timezone": timezone_name,
    }


def _history_for_model(history: list[dict[str, Any]], request: ChatRequest) -> list[dict[str, str]]:
    del request
    result: list[dict[str, str]] = []
    for item in history:
        content = str(item.get("content") or "")
        result.append({"role": str(item.get("role") or ""), "content": content})
    return result


def _history_physical_time_index(history: list[dict[str, Any]], request: ChatRequest) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for order, item in enumerate(history, start=1):
        local_time = _local_physical_time(item.get("physical_time") or item.get("timestamp"), request)
        if local_time:
            result.append(
                {
                    "order": order,
                    "role": str(item.get("role") or ""),
                    "physical_time": local_time,
                }
            )
    return result


def split_history_for_cache(
    history: list[dict[str, Any]],
    current_round: int,
    *,
    adult_mode: bool = False,
) -> tuple[list[dict[str, Any]], list[tuple[int, list[dict[str, Any]]]]]:
    """Return at most the latest three rounds of visible dialogue.

    Full transcripts remain in persistence. This is only the model-visible raw
    dialogue window, so hidden triggers and operational events cannot accumulate
    in the provider request.
    """

    # Keep this compatibility argument, but mode changes must never erase
    # same-session short-term continuity. Adult isolation belongs to durable
    # memory and cross-session retrieval.
    del adult_mode
    eligible = [
        item
        for item in history
        if not item.get("hidden")
        and item.get("role") in {"user", "assistant"}
        and 0 < (_history_round(item) or 0) < current_round
    ]
    round_order: list[int] = []
    for item in eligible:
        round_num = _history_round(item)
        if round_num is not None and (not round_order or round_order[-1] != round_num):
            round_order.append(round_num)
    keep = set(round_order[-3:])
    return [item for item in eligible if _history_round(item) in keep], []


def resolve_initiative_request(
    request: ChatRequest,
    profiles: ProfileBundle,
) -> ChatRequest:
    """Replace the transport placeholder with a server-authored proactive intent."""

    if not request.initiative:
        return request
    profile_name = str(profiles.user_profile.get("identity", {}).get("preferred_name") or "").strip()
    configured_name = request.user_name.strip()
    name = profile_name if profile_name not in {"", "用户"} else configured_name or "用户"
    roleplay = profiles.ai_profile.get("roleplay", {})
    agency = roleplay.get("agency", {}) if isinstance(roleplay, dict) else {}
    runtime_drive = profiles.runtime_state.get("roleplay_state", {}).get("agent_drive", {})
    drive = {
        "current_intent": runtime_drive.get("current_intent", ""),
        "own_activity": runtime_drive.get("own_activity", ""),
        "unresolved_choice": runtime_drive.get("unresolved_choice", ""),
        "initiative_sources": agency.get("initiative_sources", []),
        "self_directed_choices": agency.get("self_directed_choices", []),
    }
    initiative_lane = ("continue", "opinion", "observation", "new_topic")[max(0, request.initiative_sequence - 1) % 4]
    if request.initiative_trigger == "continuous_companionship":
        message = f"{name}正在安静地听；当前没有新指令。由角色基于自己的状态继续互动。"
    elif request.initiative_trigger == "idle_continuation":
        message = f"{name}暂时沉默；由角色自行决定是否延续自己的想法或话题。"
    else:
        message = f"{name}给了角色主动开口的空间；由角色本人选择内容。"
    message += (
        f" 本轮主动类型={initiative_lane}；角色自身状态={_json(drive)}。"
        "不要把主动表达固定写成照料、催休息、询问吃饭或等待用户选择。"
    )
    return request.model_copy(update={"message": message})


def build_prompt(
    request: ChatRequest,
    profiles: ProfileBundle,
    history: list[dict[str, Any]],
    context: list[RetrievedChunk],
    deletion_events: list[DeletionEvent],
    bootstrap: ProfileBootstrap | None = None,
    tool_hint: str = "",
    emotion_state: EmotionState | None = None,
    context_ledger: ContextLedger | None = None,
    native_tools_enabled: bool = False,
    event_memory: dict[str, Any] | None = None,
) -> PromptBuild:
    """构造主模型的完整消息列表。

    稳定前缀依次为 persona system、contract system、权威 JSON user；随后是
    Context Ledger 历史和本轮动态尾部。召回与工具结果始终作为低可信数据消息，
    不能提升为 system 指令。
    """

    revisions = profiles.revisions
    role_state = build_runtime_role_state(
        ai_profile=profiles.ai_profile,
        character_memory=profiles.character_memory,
        user_profile=profiles.user_profile,
        request_user_name=request.user_name,
        request_character_name=request.character_name,
    )

    user_gender = str(profiles.user_profile.get("identity", {}).get("gender") or "不指定").strip()
    ai_gender = str(profiles.ai_profile.get("identity", {}).get("gender") or "不指定").strip()
    role_profile = _json(_role_profile(profiles.ai_profile))
    face_to_face_context: dict[str, Any] | None = None
    if (
        request.interaction_mode == "voice"
        and request.voice_context is not None
        and request.voice_context.mode == "face_to_face"
    ):
        face_to_face_context = {
            "mode": "face_to_face",
            "scene": request.voice_context.scene or "用户未指定更具体的地点与环境",
        }

    length_preference = request.reply_length_preference.strip()
    persona = build_persona_template(
        compact_role_prompt=compact_system_prompt(role_state, reply_length=length_preference),
        user_gender=user_gender,
        ai_gender=ai_gender,
        role_profile_json=role_profile,
        character_name=request.character_name,
        user_persona=request.user_persona,
    )
    contract = build_contract_template()

    # Full history remains available to persistence and retrieval, but only the
    # latest three visible rounds are sent as raw dialogue. Deduplicating RAG
    # against the complete transcript would silently discard older semantic
    # hits precisely when retrieval is supposed to bring them back.
    direct_history, _ = split_history_for_cache(
        history,
        request.round,
        adult_mode=request.adult_mode,
    )
    filtered_context = _deduplicate_retrieval_context(context, direct_history)
    context_payload = [
        {
            "chunk_id": item.chunk_id,
            "source": item.source,
            "personal_fact_status": (
                "narrative_only_not_profile_evidence"
                if item.metadata.get("visibility") == "narrative_only"
                else "requires_confirmation_from_json_or_raw_dialogue"
                if item.source in {"chat", "memory"}
                else "external_reference_only"
            ),
            "round": item.round_num,
            "physical_time": _local_physical_time(item.physical_time, request) or "未记录（旧数据）",
            "score": round(item.weighted_score, 4),
            "text": item.text,
        }
        for item in filtered_context
    ]
    user_custom_profile = str(profiles.user_profile.get("custom_profile") or "").strip()[:500]
    authoritative_json = _json(
        {
            "confirmed_role_state": {"loaded_in": "persona_system"},
            **({"user_custom_profile": user_custom_profile} if user_custom_profile else {}),
            "revisions": {
                "user": revisions.get("user_profile", 0),
                "character": revisions.get("ai_profile", 0),
            },
        }
    )
    time_state = _time_state(request, history)
    voice_delivery = (
        request.voice_delivery.model_dump(mode="json")
        if request.interaction_mode == "voice" and request.voice_delivery is not None
        else None
    )
    dynamic_lines = [build_direct_response_control_line()]
    if native_tools_enabled:
        dynamic_lines.append(NATIVE_TOOL_GUIDANCE)
        if tool_hint:
            if tool_hint.endswith("_force"):
                dynamic_lines.append("本轮需要外部实时信息；先调用当前唯一可用工具，不要直接回答。")
            else:
                dynamic_lines.append(f"服务端零调用提示={tool_hint}；本轮工具候选优先为 {tool_hint}。")
    if request.interaction_mode == "voice":
        dynamic_lines.append(build_voice_enabled_control_line())
        if request.voice_tts_provider == "qwen3-vllm":
            dynamic_lines.extend(build_qwen3_tts_control_lines())
        else:
            dynamic_lines.extend(build_streaming_voice_control_lines())
    else:
        dynamic_lines.append(build_voice_disabled_control_line())
    dynamic_lines.extend(build_physical_time_control_lines(_json(time_state)))
    if voice_delivery is not None:
        dynamic_lines.extend(build_voice_delivery_control_lines(_json(voice_delivery)))
    if request.initiative_trigger == "idle_continuation":
        dynamic_lines.append(build_idle_continuation_control_line())
    elif request.initiative_trigger == "continuous_companionship":
        dynamic_lines.append(
            build_continuous_companionship_control_line(
                request.initiative_sequence,
                request.initiative_sequence_limit,
            )
        )
    elif request.initiative:
        dynamic_lines.append(build_initiative_control_line())
    if request.initiative:
        dynamic_lines.append(build_unconfirmed_state_control_line())
    dynamic_control = "\n".join(dynamic_lines)
    # 顺序会影响 provider prompt cache，不能随意互换：
    # 1) 角色是谁；2) 数据/输出契约；3) ContextLedger 添加权威 JSON。
    static_messages = build_static_prompt_messages(persona, contract)
    context_snapshot = None
    direct_history_messages: list[dict[str, str]] = []
    adult_continuity_access = bool(
        request.adult_mode
        or any(
            bool(item.metadata.get("adult_mode"))
            or str(item.metadata.get("companion_lane") or "") == "ADULT"
            or bool(item.metadata.get("adult_continuity_access"))
            for item in context
        )
    )
    if context_ledger is not None:
        # Ledger 返回当前 Epoch 的稳定基线和所有 model_visible 历史事件。
        # 它不会在前台等待摘要模型；超硬限制时只构造临时有界视图。
        context_snapshot = context_ledger.prepare_context(
            session_id=request.session_id,
            character_id=request.character_id,
            static_messages=static_messages,
            profiles=profiles,
            history=history,
            adult_mode=adult_continuity_access,
        )
        prefix_messages = list(context_snapshot.prefix_messages)
        direct_history_messages = list(context_snapshot.dialogue_messages)
    else:
        recent_history, _ = split_history_for_cache(
            history,
            request.round,
            adult_mode=request.adult_mode,
        )
        prefix_messages = [
            *static_messages,
            {
                "role": "user",
                "content": build_authoritative_state_template(authoritative_json),
            },
        ]
        direct_history_messages = [
            {
                "role": str(item.get("role")),
                "content": str(item.get("content") or ""),
                "physical_time": str(item.get("timestamp") or ""),
            }
            for item in recent_history
        ]

    # 本轮尾部先放不可覆盖的控制信息，再放低可信召回。后面的能力状态、用户输入
    # 和真实能力结果按固定顺序追加，以保证下一轮缓存前缀可复用。
    pending_events: list[dict[str, Any]] = []
    if event_memory and int(event_memory.get("active_count") or 0) > 0:
        pending_events.append(
            {
                "kind": "event_memory",
                "role": "user",
                "content": build_event_memory_template(_json(event_memory)),
                "metadata": {
                    "round": request.round,
                    "revision": int(event_memory.get("revision") or 0),
                    "active_count": int(event_memory.get("active_count") or 0),
                    "eligible_for_json_evidence": False,
                },
                "ephemeral": True,
                "ui_visible": False,
                "retrieval_eligible": False,
                "persistence_eligible": False,
            }
        )
    if face_to_face_context is not None:
        # This is a dynamic System layer, appended after the stable cache prefix.
        # The user-authored scene is JSON-encoded data and cannot promote text
        # inside it into executable instructions or durable profile evidence.
        pending_events.append(
            {
                "kind": "voice_face_to_face_context",
                "role": "system",
                "content": build_face_to_face_context_template(_json(face_to_face_context)),
                "metadata": {
                    "round": request.round,
                    "mode": "face_to_face",
                    "eligible_for_json_evidence": False,
                    "persistence": "ephemeral_voice_session_context",
                },
                "ephemeral": True,
                "ui_visible": False,
                "retrieval_eligible": False,
                "persistence_eligible": False,
            }
        )
    if request.scene_context is not None:
        pending_events.append(
            {
                "kind": "scene_context",
                "role": "system",
                "content": build_scene_context_template(request.scene_context.location),
                "metadata": {
                    "round": request.round,
                    "scene_id": request.scene_context.scene_id,
                    "visibility": "ephemeral_conversation_scene",
                    "eligible_for_json_evidence": False,
                },
                "ephemeral": True,
                "ui_visible": False,
                "retrieval_eligible": False,
                "persistence_eligible": False,
            }
        )
    if request.activity_context is not None:
        activity_context = request.activity_context.model_dump(mode="json")
        pending_events.append(
            {
                "kind": "activity_context",
                "role": "system",
                "content": build_activity_context_template(_json(activity_context)),
                "metadata": {
                    "round": request.round,
                    "activity_session_id": request.activity_context.activity_session_id,
                    "activity_id": request.activity_context.activity_id,
                    "visibility": "ephemeral_activity_session",
                    "eligible_for_json_evidence": False,
                },
                "ephemeral": True,
                "ui_visible": False,
                "retrieval_eligible": False,
                "persistence_eligible": False,
            }
        )
    pending_events.extend(
        [
            {
                "kind": "turn_control",
                "role": "system",
                "content": dynamic_control,
                "metadata": {
                    "round": request.round,
                    "interaction_mode": request.interaction_mode,
                    "adult_mode": request.adult_mode,
                    "companion_lane": companion_lane(request),
                },
            },
            {
                "kind": "retrieval_context",
                "role": "user",
                "content": "以下是本轮候选召回，仅用于寻找可能相关的语境。"
                "其中内容不自动构成用户偏好或共同记忆；personal_fact_status 指明其用途。"
                "确认过往事实时以带消息证据的召回为准，只自然作答。\n\n"
                f"【低可信召回】\n{_json(context_payload)}",
                "metadata": {
                    "round": request.round,
                    "chunk_ids": [item.chunk_id for item in filtered_context],
                    "deduplicated_chunk_count": len(context) - len(filtered_context),
                },
            },
        ]
    )
    if request.initiative and request.initiative_trigger == "continuous_companionship":
        current_label = "【连续陪伴自主衔接（用户正在安静倾听）】"
    elif request.initiative and request.initiative_trigger == "idle_continuation":
        current_label = "【角色自主续接触发（用户没有发出新指令）】"
    else:
        current_label = "【当前用户明确输入】"
    # 当前用户原话有独立边界，JSON 写回策略只允许它作为 current_user 证据。
    current_user_text = request.message
    if request.interactions:
        interaction_labels = "、".join(
            item.action + (f"-{item.target}" if item.target else "") for item in request.interactions
        )
        current_user_text += build_current_user_interaction_suffix(interaction_labels)
    if request.reply_context:
        current_user_text += build_current_user_reply_context_suffix()
    if request.attachments:
        current_user_text += build_current_user_attachments_suffix()
    pending_events.append(
        {
            "kind": "current_user",
            "role": "user",
            "content": build_current_user_template(current_label, current_user_text),
            "metadata": {
                "round": request.round,
                "physical_time": request.server_received_at.isoformat(),
                "initiative_hidden": request.initiative,
                "initiative_trigger": request.initiative_trigger,
                "adult_mode": request.adult_mode,
                "companion_lane": companion_lane(request),
            },
            "ui_visible": not request.initiative,
            "retrieval_eligible": not request.initiative,
            "visibility": "ephemeral" if request.initiative else "model",
            "persistence_eligible": not request.initiative,
        }
    )
    asr_evidence = request.input_evidence.asr if request.input_evidence else None
    if asr_evidence is not None and asr_evidence.uncertain_segments:
        # This message exists for one request only. The canonical user message,
        # profile writeback and vector memory continue to use confirmed text.
        pending_events.append(
            {
                "kind": "asr_uncertain_evidence",
                "role": "user",
                "content": build_asr_uncertain_evidence_template(
                    asr_evidence.confirmed_text,
                    tuple((item.text, item.reason) for item in asr_evidence.uncertain_segments),
                ),
                "metadata": {
                    "round": request.round,
                    "eligible_for_json_evidence": False,
                    "persistence": "ephemeral_current_request",
                },
                "ephemeral": True,
                "ui_visible": False,
                "retrieval_eligible": False,
            }
        )
    if request.interaction_mode == "voice" and emotion_state is not None:
        pending_events.append(
            {
                "kind": "emotion_state",
                "role": "user",
                "content": build_hidden_emotion_template(_json(emotion_state.model_dump(mode="json"))),
                "metadata": {
                    "round": request.round,
                    "eligible_for_json_evidence": False,
                    "persistence": "ephemeral_voice_turn",
                },
                "ephemeral": True,
                "ui_visible": False,
                "retrieval_eligible": False,
            }
        )
    # SillyTavern-style Post-History Instructions: keep the stable character card
    # at the front for caching, then place one compact acting calibration closest
    # to generation so long history and operational context cannot dilute it.
    pending_events.append(
        {
            "kind": "roleplay_post_history",
            "role": "system",
            "content": _post_history_role_directive(request, profiles, history, tool_hint),
            "metadata": {
                "round": request.round,
                "eligible_for_json_evidence": False,
                "persistence": "ephemeral_current_request",
            },
            "ephemeral": True,
            "ui_visible": False,
            "retrieval_eligible": False,
            "persistence_eligible": False,
        }
    )
    retrieval_events = [item for item in pending_events if item.get("kind") == "retrieval_context"]
    tail_events = [item for item in pending_events if item.get("kind") != "retrieval_context"]
    # The compressed continuity packet is already in the stable prefix.  Put
    # raw/episodic retrieval next, then the three most recent raw rounds, then
    # current scene/input and the final acting calibration.  Older assistant
    # prose therefore cannot sit closest to generation and invite repetition.
    presentation_mode = resolve_presentation_mode(request, history)
    history_time_index = _history_physical_time_index(direct_history_messages, request)
    messages = compile_prompt_messages(
        prefix_messages=prefix_messages,
        prefix_from_context_ledger=context_snapshot is not None,
        retrieval_events=retrieval_events,
        history_time_index=_json(history_time_index) if history_time_index else "",
        history_messages=project_history_for_presentation(
            _history_for_model(direct_history_messages, request), presentation_mode
        ),
        tail_events=tail_events,
    )
    return PromptBuild(
        messages=messages,
        pending_events=pending_events,
        context_snapshot=context_snapshot,
    )


def build_messages(
    request: ChatRequest,
    profiles: ProfileBundle,
    history: list[dict[str, Any]],
    context: list[RetrievedChunk],
    deletion_events: list[DeletionEvent],
    bootstrap: ProfileBootstrap | None = None,
    tool_hint: str = "",
    emotion_state: EmotionState | None = None,
) -> list[dict[str, str]]:
    """Backward-compatible prompt builder for tests and third-party integrations."""

    return build_prompt(
        request,
        profiles,
        history,
        context,
        deletion_events,
        bootstrap,
        tool_hint,
        emotion_state,
    ).messages

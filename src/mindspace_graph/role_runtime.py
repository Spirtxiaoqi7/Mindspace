"""Compact, confirmed role state shared by every chat provider."""

from __future__ import annotations

from typing import Any


DEFAULT_USER_NAME = "用户"


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _non_default(value: Any) -> str:
    text = _text(value, 120)
    return "" if text in {"", DEFAULT_USER_NAME} else text


def _items(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, 180)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def build_runtime_role_state(
    *,
    ai_profile: dict[str, Any],
    character_memory: dict[str, Any] | None,
    user_profile: dict[str, Any],
    request_user_name: str = "",
    request_character_name: str = "",
) -> dict[str, Any]:
    """Resolve the small source of truth used when a session is opened."""

    card = ai_profile.get("v2_card") if isinstance(ai_profile.get("v2_card"), dict) else {}
    extensions = card.get("extensions") if isinstance(card.get("extensions"), dict) else {}
    mindspace = extensions.get("mindspace") if isinstance(extensions.get("mindspace"), dict) else {}
    identity = ai_profile.get("identity") if isinstance(ai_profile.get("identity"), dict) else {}
    user_identity = user_profile.get("identity") if isinstance(user_profile.get("identity"), dict) else {}
    memory = character_memory if isinstance(character_memory, dict) else card.get("memory")
    memory = memory if isinstance(memory, dict) else {}
    user_name = (
        _non_default(mindspace.get("user_name"))
        or _non_default(request_user_name)
        or _non_default(user_identity.get("preferred_name"))
        or DEFAULT_USER_NAME
    )
    character_name = (
        _text(card.get("name"), 80)
        or _text(identity.get("name"), 80)
        or _text(request_character_name, 80)
        or "当前角色"
    )
    relationship = _text(mindspace.get("relationship"), 160) or _text(card.get("scenario"), 500)
    return {
        "character_name": character_name,
        "user_name": user_name,
        "user_alias": _text(mindspace.get("user_alias"), 80) or user_name,
        "relationship": relationship,
        "description": _text(card.get("description") or identity.get("self_description"), 640),
        "personality": _text(card.get("personality"), 640),
        "scenario": _text(card.get("scenario"), 480),
        "preferences": _items(memory.get("preferences")),
        "tasks": _items(memory.get("tasks")),
    }


def compact_system_prompt(state: dict[str, Any], *, reply_length: str = "") -> str:
    """Provider-neutral role instruction with no dialogue examples."""

    lines = [
        f"你是{state['character_name']}，正在和{state['user_name']}聊天。",
        f"关系：{state['relationship'] or '以角色卡为准'}；对用户称呼：{state['user_alias']}。",
    ]
    if state.get("description"):
        lines.append(f"基础信息：{state['description']}")
    if state.get("personality"):
        lines.append(f"性格与相处方式：{state['personality']}")
    lines.extend(
        [
            "先回应用户真正的重点，再自然延续。",
            "只输出自然聊天文本，不使用括号动作、舞台说明或小说式镜头描写。",
            "当用户明确提出领证、办理、提交或线下安排等现实事务时，先表达珍视和愿意，再给出用户可执行的下一步和自己能在聊天中协助的准备；不要声称已经或能够亲自完成现实行为。",
            "不补造过去、时间、物品或共同经历。",
            "只输出角色正文。",
        ]
    )
    if reply_length:
        lines.append(f"回复篇幅：{_text(reply_length, 120)}")
    return "\n".join(lines)


def compact_turn_directive(state: dict[str, Any]) -> str:
    facts = [
        f"用户名称={state['user_name']}",
        f"称呼={state['user_alias']}",
        f"关系={state['relationship'] or '未知'}",
    ]
    if state.get("preferences"):
        facts.append("偏好=" + "；".join(state["preferences"]))
    if state.get("tasks"):
        facts.append("任务=" + "；".join(state["tasks"]))
    return "已确认状态：" + "；".join(facts) + "。先回应本轮重点；未知内容保持未知。"

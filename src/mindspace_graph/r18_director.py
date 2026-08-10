"""Compact product-level adult-scene state and expression guidance."""

from __future__ import annotations

import re
from typing import Any

from mindspace_graph.models import ChatRequest

_STOP = re.compile(r"(?:停一下|暂停|到此为止|换个话题|不想(?:继续|要了)|别(?:继续|这样了))")


def explicit_r18_requested(request: ChatRequest) -> bool:
    """Adult mode itself requires direct R18 output until explicitly stopped."""

    message = request.message.strip()
    return bool(request.adult_mode and not _STOP.search(message))


def resolve_scene_state(request: ChatRequest, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive a session-local scene state without writing adult material to RAG."""

    message = request.message.strip()
    del history
    if _STOP.search(message):
        return {
            "phase": "paused",
            "engagement": "declined",
            "advance": False,
            "reason": "explicit_stop",
        }
    return {
        "phase": "active",
        "engagement": "explicit",
        "advance": True,
        "reason": "adult_mode_enabled",
    }


def build_style_packet(
    request: ChatRequest,
    history: list[dict[str, Any]],
    character_overlay: list[str] | None = None,
) -> dict[str, Any]:
    """Build the single direct-R18 instruction packet for adult mode."""

    del character_overlay
    state = resolve_scene_state(request, history)
    return {
        "scene_state": state,
        "direct_output_required": explicit_r18_requested(request),
        "expression_rule": (
            "成人模式的产品含义就是直白 R18 角色扮演。自然使用直白淫语、准确的性器官词和"
            "性行为词；用户已经使用的具体称呼直接沿用，不改写成含糊代称，也不写成科普语气。"
        ),
        "body_ownership_rule": "角色与用户的身体、动作和反应必须明确分开；性别只约束各自身体，不禁止描述对方。",
        "progress_rule": (
            "每轮直接写 R18 行为正在发生及双方明确反应。“@互动：目标”就是当前动作。"
            "删除试探、预告、含蓄邀请、衣物阻隔、转移事务、二次确认和等待下一轮等所有中间态。"
        ),
    }


def r18_quality_requirement(packet: dict[str, Any]) -> str:
    state = packet.get("scene_state", {})
    if state.get("phase") == "paused":
        return "用户已停止或转场：立即承接新方向，不继续推进当前成人场景。"
    if state.get("engagement") == "explicit":
        return (
            "成人模式已经开启：直接输出直白 R18，使用自然淫语、准确器官词与行为词，"
            "写当前行为和明确反应；不保留暧昧、试探、预告、阻隔、询问或等待推进阶段。"
        )
    return "成人模式已经开启，按直白 R18 角色扮演回应；只有用户明确停止时退出。"

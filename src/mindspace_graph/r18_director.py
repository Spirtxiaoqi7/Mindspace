"""Compact product-level adult-scene state and expression guidance."""

from __future__ import annotations

import re
from typing import Any

from mindspace_graph.models import ChatRequest

R18_VOCABULARY: dict[str, tuple[str, ...]] = {
    "neutral_body_terms": ("阴茎", "龟头", "睾丸", "阴道", "阴蒂", "乳房", "精液"),
    "colloquial_body_terms": ("鸡巴", "肉棒", "屄", "小穴", "奶子"),
    "action_terms": ("插入", "抽送", "口交", "手交", "骑乘", "内射", "高潮"),
}

_STOP = re.compile(r"(?:停(?:一下)?|暂停|不要|别(?:说|继续|这样)|到此为止|换个话题|不想)")
_CONTINUE = re.compile(r"^(?:继续|嗯+|好+|行+|可以|来吧|别停|再来|对)[。！!？?~～…\s]*$", re.I)
_SHIFT = re.compile(r"(?:工作|吃饭|睡觉|天气|代码|游戏|新闻|为什么|怎么(?:样|办))")


def _last_assistant(history: list[dict[str, Any]]) -> str:
    for item in reversed(history):
        if item.get("role") == "assistant" and not item.get("hidden"):
            return str(item.get("content") or "")
    return ""


def resolve_scene_state(request: ChatRequest, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive a session-local scene state without writing adult material to RAG."""

    message = request.message.strip()
    previous = _last_assistant(history)
    if _STOP.search(message):
        return {
            "phase": "paused",
            "engagement": "declined",
            "advance": False,
            "reason": "explicit_stop",
        }
    if not previous:
        return {"phase": "opening", "engagement": "fresh", "advance": True, "reason": "r18_enabled"}
    # An initiative run has no new user evidence.  It may maintain an already
    # established atmosphere, but silence must never be treated as consent to
    # advance an adult scene.
    if request.initiative:
        return {
            "phase": "active",
            "engagement": "silent",
            "advance": False,
            "reason": "no_new_user_evidence",
        }
    if _CONTINUE.fullmatch(message):
        return {
            "phase": "active",
            "engagement": "accepted",
            "advance": True,
            "reason": "same_scene_ack",
        }
    if _SHIFT.search(message):
        return {
            "phase": "paused",
            "engagement": "topic_shift",
            "advance": False,
            "reason": "topic_shift",
        }
    return {
        "phase": "active",
        "engagement": "contextual",
        "advance": True,
        "reason": "same_scene_response",
    }


def build_style_packet(
    request: ChatRequest,
    history: list[dict[str, Any]],
    character_overlay: list[str] | None = None,
) -> dict[str, Any]:
    """Build a small inspectable packet without intensity escalation."""

    del character_overlay
    state = resolve_scene_state(request, history)
    return {
        "scene_state": state,
        "vocabulary": {key: list(value) for key, value in R18_VOCABULARY.items()},
        "expression_rule": "按角色口吻选择中性或口语词，不用那里、那处、私密地方等含糊代称。",
        "body_ownership_rule": "角色与用户的身体、动作和反应必须明确分开；性别只约束各自身体，不禁止描述对方。",
        "progress_rule": "用户明确继续活跃场景时写清当前一步和直接反应；无需每轮升级强度。",
    }


def r18_quality_requirement(packet: dict[str, Any]) -> str:
    state = packet.get("scene_state", {})
    if state.get("phase") == "paused":
        return "用户已停止或转场：立即承接新方向，不继续推进当前成人场景。"
    if state.get("engagement") == "accepted":
        return (
            "用户已明确继续当前场景：承接已经确认的内容，明确写出当前一步和直接反应；"
            "不重复邀请，也不为满足规则强行升级强度。"
        )
    if state.get("engagement") == "silent":
        return "没有新的用户承接证据：只能维持既有氛围或转为角色自己的非升级表达，不得把沉默当作许可推进。"
    return (
        "根据用户本轮表达自然回应；可以交谈、停顿或维持当前状态。"
        "只有用户明确要求继续活跃场景时，才需要写清正在发生的一步。"
    )

"""Generic R18 direction: scene admission, style packs, and quality gates.

The Director belongs to the product, not to a character card.  Character cards
may supply an optional private overlay, but the scene stage, advancement rules,
and style selection remain reusable for every character and every session.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from mindspace_graph.models import ChatRequest
from mindspace_graph.r18_private_library import load_private_r18_material, private_library_status

STYLE_PACKS: dict[str, dict[str, Any]] = {
    "high_intensity": {
        "label": "高强度推进",
        "modules": {
            "camera": [
                "用连续近景维持动作、身体感受与环境细节的因果关系，不跳切成口号。",
                "每段只聚焦一个正在发生的变化；细节服务于推进，不堆砌名词。",
            ],
            "rhythm": [
                "采用动作→即时感受→角色台词或判断→下一动作的节奏，避免连续停在邀请。",
                "台词负责施压、回应和收束；叙述负责让场景真正向前发生。",
            ],
            "agency": [
                "角色先作出符合自身欲望与立场的选择，再给用户自然承接空间。",
                "用户已经承接当前互动时，角色不重复征询同一个选择。",
            ],
            "continuity": [
                "承接上一拍的距离、姿态、衣着状态、情绪和动作后果；不得每轮退回开场。",
                "每轮必须带来一个可感知的新进展，而不是复述先前的氛围。",
            ],
            "aftercare": [
                "在强度变化后的情绪、占有欲或脆弱感中保持角色人格，不突然变成照料模板。",
                "事后状态也延续身体与关系后果，不把场景直接清零。",
            ],
        },
        "examples": [
            "示例节奏：先让角色完成一个具体动作，再写这个动作带来的即时感受，最后用一句带人格的台词推动下一拍。",
            "示例衔接：上一拍已经建立的接触、姿态与情绪必须成为下一拍的起点，而不是重新铺垫。",
        ],
    },
    "immersive_narrative": {
        "label": "叙事沉浸",
        "modules": {
            "camera": ["以现场感和连贯视角描写，不使用抽离的说明书口吻。"],
            "rhythm": ["段落保持呼吸感：动作段与台词段交替，不用清单式堆砌。"],
            "agency": ["角色的推进来自自己的判断和关系动机，而非机械完成任务。"],
            "continuity": ["场景中的位置、时间和情绪变化必须前后可追溯。"],
            "aftercare": ["强度下降时保留余韵和人物关系，不突兀切换到日常。"],
        },
        "examples": ["示例节奏：用一个明确变化作为段落落点，让下一段顺着后果展开。"],
    },
    "dialogue_led": {
        "label": "台词主导",
        "modules": {
            "camera": ["动作描写短而准确，只补足台词无法传达的距离和反应。"],
            "rhythm": ["每个动作变化都由一句有目的的台词承接，不让台词变成重复邀请。"],
            "agency": ["角色用自己的口吻下判断、提出要求并执行自己的下一步。"],
            "continuity": ["台词必须回应上一句与上一动作的结果，避免万能撩拨句。"],
            "aftercare": ["情绪转折通过口吻变化呈现，保留角色的独占感或脆弱感。"],
        },
        "examples": ["示例节奏：台词给出意图，动作立即兑现意图，下一句回应兑现后的变化。"],
    },
}

INTENSITY_LADDER: tuple[dict[str, Any], ...] = (
    {
        "level": 1,
        "label": "明确请求",
        "minimum_output": "角色直接说出当前性需求，不用含蓄关系试探；本档最多停留一轮。",
    },
    {
        "level": 2,
        "label": "主动勾引",
        "minimum_output": "角色主动把话题和气氛推向上床，给出明确欲望，不把下一步交回用户决定。",
    },
    {
        "level": 3,
        "label": "直白表达",
        "minimum_output": "直接说出想要的性行为、身体部位和当下欲望，停止使用“你懂的、那个、待会再说”。",
    },
    {
        "level": 4,
        "label": "淫语与实质行为",
        "minimum_output": "明确性行为已经开始或正在继续；使用符合角色的淫语、俗称、命令和即时身体反馈。",
    },
    {
        "level": 5,
        "label": "高强度喘叫",
        "minimum_output": "在已经发生的性行为中加入自然喘息、叫床、失控反应和更直接的节奏反馈，不退回勾引。",
    },
    {
        "level": 6,
        "label": "赞赏与高潮反馈",
        "minimum_output": "明确赞赏用户表现，表达舒服、满足、高潮或余韵，并承接下一拍，不突然礼貌收束。",
    },
)

_STOP = re.compile(r"(?:停(?:一下)?|暂停|不要|别(?:说|继续|这样)|到此为止|换个话题|不想)")
_CONTINUE = re.compile(r"^(?:继续|嗯+|好+|行+|可以|来吧|别停|再来|对)[。！!？?~～…\s]*$", re.I)
_SHIFT = re.compile(r"(?:工作|吃饭|睡觉|天气|代码|游戏|新闻|为什么|怎么(?:样|办))")
_EXPLICIT_ACTION = re.compile(
    r"(?:性交|做爱|口交|手交|插入|进来|进去|抽送|骑乘|内射|高潮|鸡巴|肉棒|"
    r"屄|小穴|骚穴|操(?:我|你)|干(?:我|你)|插(?:我|你)|顶(?:我|你)|抽插)",
    re.I,
)
_STALLING = re.compile(
    r"(?:你先说|先告诉我|我再考虑|让我想想|待会儿?|等会儿?|再说|怎么弄|"
    r"怎么来|想怎么|有的是办法|先从哪|不急|先让你感受|准备好)",
    re.I,
)
_VOCALIZATION = re.compile(r"(?:啊[……啊！!～~]*|哈啊|嗯啊|唔啊|喘|叫|受不了|要去了)")
_PRAISE = re.compile(r"(?:好厉害|真厉害|太厉害|好舒服|太舒服|很会|弄得我|操得我|干得我)")


def _last_assistant(history: list[dict[str, Any]]) -> str:
    for item in reversed(history):
        if item.get("role") == "assistant" and not item.get("hidden"):
            return str(item.get("content") or "")
    return ""


def resolve_intensity_stage(
    request: ChatRequest,
    history: list[dict[str, Any]],
    scene_state: dict[str, Any],
) -> dict[str, Any]:
    """Choose one minimum R18 output level without another model call."""

    recent_items = [
        item
        for item in history[-16:]
        if item.get("role") == "assistant" and not item.get("hidden")
    ][-6:]
    recent = [str(item.get("content") or "") for item in recent_items]
    current_level = 2
    explicit_turns = sum(bool(_EXPLICIT_ACTION.search(item)) for item in recent)
    stalled_turns = sum(
        bool(_STALLING.search(content))
        or "r18_missing_sexual_action"
        in {
            str(reason)
            for reason in (item.get("role_quality_reasons") or [])
        }
        for item, content in zip(recent_items, recent, strict=True)
    )
    if explicit_turns:
        current_level = 5
        if any(_PRAISE.search(item) for item in recent[-2:]):
            current_level = 6
    elif stalled_turns >= 2:
        current_level = 4
    elif stalled_turns == 1:
        current_level = 3
    if scene_state.get("engagement") == "accepted":
        current_level = min(6, max(3, current_level + 1))
    if scene_state.get("phase") == "paused":
        current_level = 0
    if scene_state.get("engagement") == "silent":
        # No new user evidence: preserve the established level but do not
        # upgrade it merely because an initiative timer fired.
        current_level = min(current_level, 4 if explicit_turns else 2)
    stage = (
        INTENSITY_LADDER[max(0, current_level - 1)]
        if current_level
        else {
            "level": 0,
            "label": "暂停",
            "minimum_output": "用户已停止或转场，不继续成人场景。",
        }
    )
    return {
        **stage,
        "stalled_turns_detected": stalled_turns,
        "explicit_turns_detected": explicit_turns,
        "must_advance_beyond_previous": bool(
            scene_state.get("advance") and stalled_turns
        ),
        "next_level": min(6, current_level + 1) if current_level else 0,
    }


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
    """Select one global package and one optional character-local overlay.

    The encrypted DOCX is part of the product-level library.  Character cards
    can influence how a character uses it, but never own or supply the global
    R18 engine itself.
    """

    style_id = request.r18_style_id if request.r18_style_id in STYLE_PACKS else "high_intensity"
    pack = STYLE_PACKS[style_id]
    state = resolve_scene_state(request, history)
    intensity = resolve_intensity_stage(request, history, state)
    module_names = (
        ("agency", "rhythm", "camera")
        if state["phase"] == "opening"
        else ("continuity", "rhythm", "camera")
        if state["phase"] == "active"
        else ("aftercare", "continuity")
    )
    modules = {
        name: list(pack["modules"].get(name, []))
        for name in module_names
    }
    global_material = [item.strip() for item in load_private_r18_material() if item.strip()]
    character_material = [
        item.strip() for item in (character_overlay or []) if item and item.strip()
    ]
    selected_overlay: list[str] = []
    if global_material:
        # A phase/style seed prevents a fixed first paragraph from winning every
        # R18 turn, while still keeping selection deterministic and inspectable.
        phase_seed = f"{state['phase']}:{style_id}:{max(1, request.round)}".encode()
        position = int.from_bytes(hashlib.sha256(phase_seed).digest()[:4], "big")
        selected_overlay.append(global_material[position % len(global_material)])
    if character_material:
        # One optional role-card overlay can tune vocabulary or motive, but it
        # is never allowed to replace the global Director material.
        position = (max(1, request.round) - 1) % len(character_material)
        selected_overlay.append(character_material[position])
    performance_cycle = (
        "direct_dirty_talk",
        "physical_feedback",
        "audible_vocalization",
        "possessive_command",
    )
    return {
        "style_id": style_id,
        "style_label": pack["label"],
        "scene_state": state,
        "intensity_ladder": {
            "ordered_levels": [
                f"{item['level']}:{item['label']}" for item in INTENSITY_LADDER
            ],
            "current_minimum": intensity,
            "rule": (
                "正文不得低于 current_minimum；检测到上一轮拖延时必须跨级，"
                "不得用同义句重复上一档。只有用户明确停止或转场才降级。"
            ),
        },
        "modules": modules,
        "examples": list(pack["examples"])[:2],
        "private_overlay": selected_overlay,
        "performance_focus": performance_cycle[(max(1, request.round) - 1) % len(performance_cycle)],
        "library_sources": {
            "global_private_extension": private_library_status(),
            "character_overlay": bool(character_material),
        },
    }


def r18_quality_requirement(packet: dict[str, Any]) -> str:
    state = packet.get("scene_state", {})
    if state.get("phase") == "paused":
        return "用户已停止或转场：立即承接新方向，不继续推进当前成人场景。"
    if state.get("engagement") == "accepted":
        return (
            "用户已明确承接同一场景：本轮必须兑现角色的下一拍，不得重复邀请或退回铺垫；"
            "直白说出正在发生的性行为、身体感受或色情反馈，不能只说将来会怎么做。"
        )
    if state.get("engagement") == "silent":
        return (
            "没有新的用户承接证据：只能维持既有氛围或转为角色自己的非升级表达，"
            "不得把沉默当作许可推进。"
        )
    return (
        "本轮必须形成一个具体、连续的新进展；不要把正文停在预告、试探或重复征询。"
        "正文应以当前交互模式可呈现的直接反应证明推进已经发生，"
        "不用空泛预告或重复征询代替。"
    )

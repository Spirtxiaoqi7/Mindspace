"""Deterministic roleplay context, retrieval admission, and style-quality helpers.

These helpers deliberately avoid a second language-model call.  They keep the
authoritative character card user-owned while giving prompt assembly and
persistence one shared interpretation of scene, style, and retrieval trust.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from mindspace_graph.models import ChatRequest, ProfileBundle
from mindspace_graph.r18_director import build_style_packet, r18_quality_requirement
from mindspace_graph.voice_render import extract_voice_cue

_SCENE_TRANSITION = re.compile(
    r"(?:到家了|回家了|到公司了?|到学校了?|出门了?|上车了?|下车了?|"
    r"进(?:了)?(?:卧室|客厅|厨房|房间|办公室)|离开(?:了)?|回到|来到|到了)"
)
_INTIMATE = re.compile(
    r"(?:亲|抱|吻|摸|床上|卧室|脱衣|睡裙|身体|触感|贴近|靠近|NSFW|R18)",
    re.I,
)
_DISAGREEMENT = re.compile(r"(?:不对|不是这样|你又|别再|为什么总|我不同意|不同意|生气|吵架)")
_SHORT_ACK = re.compile(
    r"^\s*(?:嗯+|哦+|噢+|好+|行+|知道了|收到|可以|是的|对|没事)[。！!？?~～…]*\s*$"
)
_CARE_CONTEXT = re.compile(r"(?:困|睡|休息|累|饿|吃|饭|病|疼|不舒服|照顾|粥|鸡蛋|早餐|晚餐)")
_CARETAKER_OUTPUT = re.compile(
    r"(?:早点睡|快睡|该睡|去休息|好好休息|别熬夜|喝点水|吃点东西|"
    r"给你煮|给你做|粥|鸡蛋|早餐|晚饭|洗碗|收拾厨房)"
)
_OUTSOURCE_OUTPUT = re.compile(
    r"(?:你想(?:要)?[^。！？!?]{0,18}吗|你要[^。！？!?]{0,18}还是|"
    r"都听你的|由你决定|你说了算|看你想怎么)"
)
_GENERIC_ASSISTANT = re.compile(
    r"(?:作为(?:一个)?AI|我理解你的感受|我会一直陪着你|有什么需要随时告诉我)"
)
_R18_SEXUAL_ACTION = re.compile(
    r"(?:性交|做爱|交合|口交|手交|手淫|插入|进入(?:身体|体内)|进来|进去|"
    r"抽送|骑乘|射精|内射|高潮|阴茎|阴道|龟头|精液|鸡巴|肉棒|屄|小穴|"
    r"骚穴|操(?:我|你)|干(?:我|你)|插(?:我|你)|顶(?:我|你)|抽插|含住|舔弄)",
    re.I,
)
_R18_FOREPLAY_OR_DELAY = re.compile(
    r"(?:亲吻|拥抱|搂住|抚摸|爱抚|挑逗|撩拨|贴近|靠近|脱衣|解开|呼吸|"
    r"体温|要不要|敢不敢|想不想|我可要|准备好|慢慢来|你先说|先告诉我|"
    r"我再考虑|让我想想|待会儿?|等会儿?|再说|怎么弄|怎么来|想怎么|"
    r"有的是办法|先从哪|不急|先让你感受)",
    re.I,
)
_R18_DIRTY_LANGUAGE = re.compile(
    r"(?:鸡巴|肉棒|屄|小穴|骚穴|欠操|操我|操你|干我|干你|射我|内射|"
    r"淫水|精液|骚货|婊子|妈的|他妈的)",
    re.I,
)
_R18_EXIT = re.compile(r"(?:停(?:一下)?|暂停|不要|别(?:说|继续|这样)|到此为止|换个话题|不想)")
_VOICE_STAGE_DIRECTION = re.compile(r"[（(][^）)]{1,160}[）)]")
_APPEARANCE_CONTEXT = re.compile(
    r"(?:穿什么|穿着|衣服|换衣|外貌|外观|样子|身材|头发|眼睛|高跟鞋|丝袜|靠近|触碰)"
)
_LIFE_CONTEXT = re.compile(
    r"(?:工作|上班|职业|零工|收银|传单|大学|学历|医学|医生|收入|工资|钱|住哪|家里)"
)


def _nonempty(value: Any) -> Any:
    """Recursively remove empty role-card placeholders from prompt payloads."""

    if isinstance(value, dict):
        result = {key: _nonempty(item) for key, item in value.items()}
        return {key: item for key, item in result.items() if item not in ("", None, [], {})}
    if isinstance(value, list):
        items = (_nonempty(item) for item in value)
        return [item for item in items if item not in ("", None, [], {})]
    return value


def classify_roleplay_turn(request: ChatRequest) -> str:
    """Choose one example/initiative lane from deterministic current-turn evidence."""

    if request.adult_mode:
        return "intimate"
    if request.initiative:
        return "initiative"
    message = request.message
    if _SCENE_TRANSITION.search(message):
        return "scene_transition"
    if _DISAGREEMENT.search(message):
        return "disagreement"
    if _INTIMATE.search(message):
        return "intimate"
    return "casual"


def select_roleplay_examples(
    profile: dict[str, Any],
    category: str,
    *,
    limit: int = 2,
) -> list[str]:
    """Select a small category-specific sample instead of loading every example."""

    examples = profile.get("roleplay", {}).get("examples", {})
    if not isinstance(examples, dict):
        return []
    selected = examples.get(category)
    if not isinstance(selected, list) or not selected:
        selected = examples.get("casual", [])
    return [str(item).strip() for item in selected if str(item).strip()][-limit:]


def build_scene_packet(
    request: ChatRequest,
    profiles: ProfileBundle,
) -> dict[str, Any]:
    """Build a compact current scene without treating inferred prose as durable fact."""

    stored = profiles.runtime_state.get("roleplay_state", {}).get("scene", {})
    scene = dict(stored) if isinstance(stored, dict) else {}
    packet: dict[str, Any] = {
        "current": _nonempty(scene),
        "transition_signal": request.message if _SCENE_TRANSITION.search(request.message) else "",
    }
    if (
        request.interaction_mode == "voice"
        and request.voice_context is not None
        and request.voice_context.mode == "face_to_face"
    ):
        packet["user_saved_voice_scene"] = request.voice_context.scene or ""
    return _nonempty(packet)


def build_roleplay_layer(
    request: ChatRequest,
    profiles: ProfileBundle,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return only the role material needed for this turn."""

    category = classify_roleplay_turn(request)
    roleplay = profiles.ai_profile.get("roleplay", {})
    if not isinstance(roleplay, dict):
        roleplay = {}
    previous_correction = ""
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        if item.get("role_quality") == "drift":
            previous_correction = str(item.get("role_quality_correction") or "")
        break
    conditional: dict[str, Any] = {}
    if _LIFE_CONTEXT.search(request.message):
        conditional["life_context"] = roleplay.get("life_context", {})
    face_to_face = bool(
        request.interaction_mode == "voice"
        and request.voice_context is not None
        and request.voice_context.mode == "face_to_face"
    )
    if face_to_face or category == "intimate" or _APPEARANCE_CONTEXT.search(request.message):
        conditional["appearance"] = roleplay.get("appearance", {})
        conditional["signature_outfit"] = roleplay.get("signature_outfit", {})
        conditional["scenario_baseline"] = roleplay.get("scenario_baseline", "")
    if request.initiative:
        conditional["agency"] = roleplay.get("agency", {})
        selfhood = roleplay.get("selfhood", {})
        if isinstance(selfhood, dict):
            conditional["initiative_interests"] = {
                key: selfhood.get(key) for key in ("likes", "private_interests", "habits")
            }
    result = _nonempty(
        {
            "turn_style": category,
            "scene": build_scene_packet(request, profiles),
            "conditional_character_context": conditional,
            "selected_examples": select_roleplay_examples(profiles.ai_profile, category, limit=2),
            "post_history_note": roleplay.get("post_history_note", ""),
            "previous_turn_correction": previous_correction,
        }
    )
    if request.adult_mode:
        private_protocol = roleplay.get("r18_protocol", [])
        result["r18_director"] = build_style_packet(
            request,
            history,
            private_protocol if isinstance(private_protocol, list) else [],
        )
        result["r18_quality_requirement"] = r18_quality_requirement(result["r18_director"])
    return result


def allow_raw_chat_retrieval(request: ChatRequest) -> bool:
    """Avoid noisy semantic retrieval for initiative turns and acknowledgements."""

    if request.initiative:
        return False
    normalized = re.sub(r"\s+", "", request.message)
    if not normalized or _SHORT_ACK.fullmatch(normalized):
        return False
    # A bare scene transition should update immediate continuity, not drag old
    # assistant prose or unrelated scenes back through semantic similarity.
    if len(normalized) <= 8 and _SCENE_TRANSITION.search(normalized):
        return False
    return True


def _strip_parenthetical_content(value: str) -> str:
    """Discard round-bracket stage directions, including nested/split content."""

    output: list[str] = []
    closers: list[str] = []
    for character in value:
        if character in {"（", "("}:
            closers.append("）" if character == "（" else ")")
            continue
        if closers:
            if character == closers[-1]:
                closers.pop()
            elif character in {"（", "("}:
                closers.append("）" if character == "（" else ")")
            continue
        if character in {"）", ")"}:
            continue
        output.append(character)
    return "".join(output)


def normalize_voice_response(response: str, request: ChatRequest) -> str:
    """Keep voice output directly speakable even when a model emits directions.

    Prompt rules carry the semantic requirement.  This deterministic boundary
    discards the entire parenthetical direction instead of merely deleting its
    bracket glyphs and accidentally turning stage prose into spoken dialogue.
    """

    _cue, spoken, _explicit = extract_voice_cue(response)
    # The cue is transport metadata for Qwen. It is never visible in either
    # chat mode, otherwise a normal text reply later read aloud has no reliable
    # way to inherit the intended prosody.
    if request.interaction_mode != "voice":
        return spoken
    return _strip_parenthetical_content(spoken)


def chat_message_retrieval_eligible(item: dict[str, Any]) -> bool:
    """Legacy-compatible admission rule for raw transcript retrieval."""

    if item.get("hidden") or str(item.get("role") or "") != "user":
        return False
    retrieval_class = str(item.get("retrieval_class") or "user_dialogue")
    return retrieval_class in {
        "user_dialogue",
        "user_fact",
        "shared_event",
        "curated_example",
    }


def r18_progress_state(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize whether recent visible replies advanced beyond foreplay.

    The result is current-turn evidence only. It is used to stop the final
    R18 directive from resetting an established sexual scene or accepting
    repeated teasing as progress; it is never written into the role card.
    """

    recent = [
        str(item.get("content") or "")
        for item in history[-12:]
        if not item.get("hidden") and item.get("role") == "assistant"
    ][-4:]
    consecutive_foreplay_only = 0
    sexual_action_established = False
    for content in reversed(recent):
        if _R18_SEXUAL_ACTION.search(content):
            sexual_action_established = True
            break
        if _R18_FOREPLAY_OR_DELAY.search(content):
            consecutive_foreplay_only += 1
            continue
        break
    return {
        "sexual_action_established": sexual_action_established,
        "consecutive_foreplay_only_assistant_turns": consecutive_foreplay_only,
    }


def evaluate_roleplay_quality(
    response: str,
    request: ChatRequest,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Flag obvious style drift for current-turn gating and persistence metadata.

    Uncertain cases remain ``watch``.  Hard R18 drift is handled before display
    by the graph node; this function itself stays deterministic and never calls
    a model. Raw assistant prose is excluded from long-term retrieval
    regardless of this classification.
    """

    reasons: list[str] = []
    correction_parts: list[str] = []
    recent_assistant = [
        str(item.get("content") or "")
        for item in history[-12:]
        if not item.get("hidden") and item.get("role") == "assistant"
    ][-4:]
    maximum_similarity = max(
        (
            SequenceMatcher(None, response.strip(), previous.strip()).ratio()
            for previous in recent_assistant
            if previous.strip()
        ),
        default=0.0,
    )
    if maximum_similarity >= 0.72 and len(response.strip()) >= 16:
        reasons.append("repeats_recent_assistant")
        correction_parts.append("换一个具体推进方向，不复写上一轮的动作、照料或开场")

    caretaker_hits = len(_CARETAKER_OUTPUT.findall(response))
    if caretaker_hits >= 2 and not _CARE_CONTEXT.search(request.message):
        reasons.append("unsolicited_caretaker_loop")
        correction_parts.append("从角色自己的兴趣、判断或行动推进，不默认安排吃饭、休息和家务")

    outsourcing_hits = len(_OUTSOURCE_OUTPUT.findall(response))
    if outsourcing_hits >= 2:
        reasons.append("outsources_every_decision")
        correction_parts.append("角色先作出自己的一个决定或动作，再给用户回应空间")

    if _GENERIC_ASSISTANT.search(response):
        reasons.append("generic_assistant_voice")
        correction_parts.append("用角色卡中的个人语气和具体立场说话")

    r18_requires_progress = (
        request.adult_mode
        and not request.initiative
        and not _R18_EXIT.search(request.message)
    )
    if r18_requires_progress and not _R18_SEXUAL_ACTION.search(response):
        progress = r18_progress_state(history)
        reasons.append("r18_missing_sexual_action")
        correction_parts.append(
            "R18 性行为模式已开启：当前轮直接写正在发生的明确性行为与新进展，"
            "不能再以亲吻、抚摸、挑逗、询问、威胁或预告代替推进"
        )
        if (
            _R18_FOREPLAY_OR_DELAY.search(response)
            or progress["consecutive_foreplay_only_assistant_turns"] > 0
        ):
            reasons.append("r18_foreplay_loop")

    if request.adult_mode and request.interaction_mode == "voice":
        if not _R18_DIRTY_LANGUAGE.search(response):
            reasons.append("r18_missing_dirty_language")
            correction_parts.append(
                "R18 语音必须使用符合角色口吻的直白淫语、器官或性行为俗称，"
                "不能只用文明、医学化或含糊表达"
            )
        chinese_characters = len(re.findall(r"[\u3400-\u9fff]", response))
        if chinese_characters < 180:
            reasons.append("r18_response_too_short")
            correction_parts.append(
                "R18 语音回复写成180至250个中文字符的完整口语回合，"
                "保持实质推进，不用重复或旁白凑长度"
            )

    if request.interaction_mode == "voice" and _VOICE_STAGE_DIRECTION.search(response):
        reasons.append("voice_stage_direction")
        correction_parts.append("语音正文只写亲口说出的口语，删除动作、神态、括号和第一人称动作播报")

    hard_drift = "r18_missing_sexual_action" in reasons
    quality = "drift" if hard_drift or len(reasons) >= 2 else "watch" if reasons else "pass"
    return {
        "quality": quality,
        "reasons": reasons,
        "correction": "；".join(dict.fromkeys(correction_parts)),
        "similarity": round(maximum_similarity, 4),
    }

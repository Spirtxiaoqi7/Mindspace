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
_SHORT_ACK = re.compile(r"^\s*(?:嗯+|哦+|噢+|好+|行+|知道了|收到|可以|是的|对|没事)[。！!？?~～…]*\s*$")
_CARE_CONTEXT = re.compile(r"(?:困|睡|休息|累|饿|吃|饭|病|疼|不舒服|照顾|粥|鸡蛋|早餐|晚餐)")
_CARETAKER_OUTPUT = re.compile(
    r"(?:早点睡|快睡|该睡|去休息|好好休息|别熬夜|喝点水|吃点东西|"
    r"给你煮|给你做|粥|鸡蛋|早餐|晚饭|洗碗|收拾厨房)"
)
_OUTSOURCE_OUTPUT = re.compile(
    r"(?:你想(?:要)?[^。！？!?]{0,18}吗|你要[^。！？!?]{0,18}还是|"
    r"都听你的|由你决定|你说了算|看你想怎么)"
)
_GENERIC_ASSISTANT = re.compile(r"(?:作为(?:一个)?AI|我理解你的感受|我会一直陪着你|有什么需要随时告诉我)")
_R18_SEXUAL_ACTION = re.compile(
    r"(?:性交|做爱|交合|口交|手交|手淫|插入|进入(?:身体|体内)|进来|进去|"
    r"抽送|骑乘|射精|内射|高潮|阴茎|阴道|龟头|精液|鸡巴|肉棒|屄|小穴|"
    r"骚穴|操(?:我|你)|干(?:我|你)|插(?:我|你)|顶(?:我|你)|抽插|含住|舔弄)",
    re.I,
)
_R18_EXIT = re.compile(r"(?:停(?:一下)?|暂停|不要|别(?:说|继续|这样)|到此为止|换个话题|不想)")
_R18_ACTIVE_CONTINUATION = re.compile(
    r"^\s*(?:继续|接着|别停|再来|快点|再快点|用力|就这样|进去|进来|对)[。！!？?~～…\s]*$",
    re.I,
)
_VOICE_STAGE_DIRECTION = re.compile(r"[（(][^）)]{1,160}[）)]")
_APPEARANCE_CONTEXT = re.compile(r"(?:穿什么|穿着|衣服|换衣|外貌|外观|样子|身材|头发|眼睛|高跟鞋|丝袜|靠近|触碰)")
_LIFE_CONTEXT = re.compile(r"(?:工作|上班|职业|零工|收银|传单|大学|学历|医学|医生|收入|工资|钱|住哪|家里)")
_SCENE_LOCATION = re.compile(
    r"(?:(?:我们|咱们|两个人|我和你|你和我)(?:现在|此刻)?(?:正)?在|"
    r"当前(?:场景|地点)(?:是|在)|场景(?:切到|来到))"
    r"[^。！？!?]{0,18}(?:家里|客厅|卧室|厨房|房间|办公室|学校|教室|"
    r"咖啡馆|咖啡厅|餐厅|酒吧|公园|商场|车里|车上|街上|海边|酒店)"
)
_DISCUSSION_TURN = re.compile(
    r"(?:怎么看|怎么想|觉得|为什么|是否|会不会|聊聊|说说|解释|分析|意见|问题|什么意思|吗[？?]?$)"
)
_SCENE_ACTION = re.compile(
    r"(?:我(?:走|坐|躺|靠|抱|吻|拉|推|伸手|转身|进|回|把|将|拿|递|指|放|端|打开|关上)|"
    r"你(?:走|坐|躺|靠|抱|吻|拉|推|伸手|转身|过来)|"
    r"抱住我|吻我|亲我|继续刚才|接着刚才|然后呢)"
)
_CONTINUATION = re.compile(r"^\s*(?:继续|接着|然后呢|再来|别停|嗯+|好+)[。！!？?~～…]*\s*$")
_QUESTION_END = re.compile(r"[？?][”’\"']?\s*$")
_ACTION_DIRECTION = re.compile(r"^[\s\n]*[（(]([^）)]{1,120})[）)]")
_LEGACY_STAGE_ACTION = re.compile(
    r"(?:我|TA|他|她)?(?:抬眼|低头|转身|靠近|坐下|起身|伸手|放下|拿起|推开|"
    r"抱住|吻|看向|望向|摩挲|握住|皱眉|笑了|沉默)"
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


def companion_lane(request: ChatRequest) -> str:
    """Three-state companion route; adult content requires the explicit hard switch."""

    if request.adult_mode:
        return "ADULT"
    if _INTIMATE.search(request.message):
        return "ROMANCE"
    return "DAILY"


def resolve_presentation_mode(
    request: ChatRequest,
    history: list[dict[str, Any]],
) -> str:
    """Resolve dialogue versus enacted scene without changing the content lane."""

    if request.interaction_mode == "voice":
        return "dialogue"
    if request.presentation_mode != "auto":
        return request.presentation_mode
    if (
        _SCENE_TRANSITION.search(request.message)
        or _SCENE_LOCATION.search(request.message)
        or _SCENE_ACTION.search(request.message)
    ):
        return "scene"
    if _DISCUSSION_TURN.search(request.message):
        return "dialogue"
    recent = [item for item in history if item.get("role") == "assistant" and not item.get("hidden")]
    if _CONTINUATION.fullmatch(request.message) and recent:
        return str(recent[-1].get("presentation_mode") or "dialogue")
    return "dialogue"


def effective_roleplay_temperature(
    request: ChatRequest,
    history: list[dict[str, Any]],
) -> float:
    """Cap sampling by lane so factual scene turns are less improvisational."""

    requested = float(request.api.temperature)
    if request.adult_mode:
        return min(requested, 0.65)
    if request.initiative or resolve_presentation_mode(request, history) == "scene":
        return min(requested, 0.25)
    return min(requested, 0.45)


def effective_roleplay_max_tokens(
    request: ChatRequest,
    history: list[dict[str, Any]],
) -> int:
    """Bound ordinary replies so smaller models do not default to narration."""

    del history
    requested = max(64, int(request.api.max_tokens))
    if request.adult_mode:
        return min(requested, 700)
    message = re.sub(r"\s+", "", request.message)
    if re.search(r"(?:只回答|一句话|称呼|提醒.*(?:什么|几点)|什么.*提醒)", message):
        return min(requested, 160)
    if len(message) <= 12:
        return min(requested, 200)
    if resolve_presentation_mode(request, []) == "scene":
        return min(requested, 360)
    return min(requested, 420)


def project_history_for_presentation(
    messages: list[dict[str, Any]],
    mode: str,
) -> list[dict[str, str]]:
    """Keep normal prose while preventing legacy stage openers from becoming style examples."""

    projected: list[dict[str, str]] = []
    for item in messages:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if mode == "dialogue" and role == "assistant":
            opener = _ACTION_DIRECTION.match(content)
            if opener and _LEGACY_STAGE_ACTION.search(opener.group(1)):
                content = content[opener.end() :].lstrip()
        projected.append({"role": role, "content": content})
    return projected


def build_presentation_plan(
    request: ChatRequest,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build deterministic pacing and anti-repetition constraints for one text turn."""

    mode = resolve_presentation_mode(request, history)
    recent_assistant = [
        str(item.get("content") or "") for item in history if item.get("role") == "assistant" and not item.get("hidden")
    ][-4:]
    question_budget = 0 if recent_assistant and _QUESTION_END.search(recent_assistant[-1]) else 1
    if request.initiative:
        question_budget = 0
    return _nonempty(
        {
            "preference": request.presentation_mode,
            "resolved": mode,
            "question_budget": question_budget,
            "required_reply_handles": (
                ["回应当前内容", "给出角色自己的判断或自我披露", "留下无需问句也能承接的话题支点"]
                if mode == "dialogue"
                else [
                    "承接用户已经确认的场景事实",
                    "用角色的台词、判断或选择推进当前内容",
                    "不为表现沉浸而强制增加动作",
                ]
            ),
            "agency_budget": {
                "allowed": ["角色自己的观点", "角色自己的情绪", "与当前话题相关的一个选择"],
                "requires_user_or_server_evidence": [
                    "当前地点与时间",
                    "当前衣着与随身物品",
                    "角色当前动作与活动",
                    "用户动作、反应与动机",
                    "共同事件与环境物件",
                ],
                "forbidden": [
                    "替用户补动作或身体反应",
                    "替用户推断意图、动机或内心结论",
                    "把假设、建议或偏好写成正在发生的事实",
                ],
            },
        }
    )


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
        same_adult_lane = bool(item.get("adult_mode")) == bool(request.adult_mode)
        if item.get("role_quality") == "drift" and same_adult_lane:
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
            "companion_lane": companion_lane(request),
            "turn_style": category,
            "scene": build_scene_packet(request, profiles),
            "conditional_character_context": conditional,
            "selected_examples": select_roleplay_examples(profiles.ai_profile, category, limit=2),
            "post_history_note": roleplay.get("post_history_note", ""),
            "previous_turn_correction": previous_correction,
            "presentation": build_presentation_plan(request, history),
        }
    )
    if request.adult_mode:
        behavior_rules = profiles.ai_profile.get("behavior_rules", {})
        relationship_rules = profiles.ai_profile.get("relationship_rules", {})
        adult_character_rules = _nonempty(
            {
                "contextual_rules": (
                    behavior_rules.get("contextual_rules", []) if isinstance(behavior_rules, dict) else []
                ),
                "preferred_interactions": (
                    relationship_rules.get("preferred_interactions", [])
                    if isinstance(relationship_rules, dict)
                    else []
                ),
            }
        )
        if adult_character_rules:
            result["adult_character_rules"] = adult_character_rules
        result["r18_director"] = build_style_packet(request, history)
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


def normalize_presentation_response(
    response: str,
    request: ChatRequest,
    history: list[dict[str, Any]],
) -> str:
    """Keep text-mode prose intact; voice retains its speakable-text boundary."""

    del history
    return normalize_voice_response(response, request).strip()


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
        correction_parts.append("换一个具体回应方向，不复写上一轮的措辞、照料安排或开场")

    caretaker_hits = len(_CARETAKER_OUTPUT.findall(response))
    if caretaker_hits >= 2 and not _CARE_CONTEXT.search(request.message):
        reasons.append("unsolicited_caretaker_loop")
        correction_parts.append("从角色自己的兴趣或判断回应，不默认安排吃饭、休息和家务")

    outsourcing_hits = len(_OUTSOURCE_OUTPUT.findall(response))
    if outsourcing_hits >= 2:
        reasons.append("outsources_every_decision")
        correction_parts.append("角色先表达自己的一个判断或选择，再给用户回应空间")

    if _GENERIC_ASSISTANT.search(response):
        reasons.append("generic_assistant_voice")
        correction_parts.append("用角色卡中的个人语气和具体立场说话")

    r18_requires_clarity = bool(
        request.adult_mode
        and not request.initiative
        and not _R18_EXIT.search(request.message)
        and (_R18_ACTIVE_CONTINUATION.fullmatch(request.message) or _R18_SEXUAL_ACTION.search(request.message))
    )
    if r18_requires_clarity and not _R18_SEXUAL_ACTION.search(response):
        reasons.append("r18_vague_active_scene")
        correction_parts.append(
            "用户已明确继续活跃场景：使用准确身体或行为词写清当前一步和直接反应；"
            "不要只写那里、那处、私密地方或将来预告，也不必强行升级强度"
        )
    if request.interaction_mode == "voice" and _VOICE_STAGE_DIRECTION.search(response):
        reasons.append("voice_stage_direction")
        correction_parts.append("语音正文只写亲口说出的口语，删除动作、神态、括号和第一人称动作播报")

    hard_drift = "r18_vague_active_scene" in reasons
    quality = "drift" if hard_drift or len(reasons) >= 2 else "watch" if reasons else "pass"
    return {
        "quality": quality,
        "reasons": reasons,
        "correction": "；".join(dict.fromkeys(correction_parts)),
        "similarity": round(maximum_similarity, 4),
    }

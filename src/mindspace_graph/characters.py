"""Character library, assisted card drafts, and the 0.6 multi-character migration.

The existing profile files remain readable compatibility projections.  SQLite
documents are canonical and every character owns an AI profile, runtime state,
avatar configuration, and revision history.  The user profile intentionally
stays global.
"""

from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, Field, field_validator

from mindspace_graph.adapters.file_storage import (
    DEFAULT_PROFILES,
    _apply_patch,
    _atomic_json,
    _read_pointer,
)
from mindspace_graph.fate_forge import (
    fate_block_additions,
    fate_generation_packet,
    normalize_fate_forge,
)
from mindspace_graph.models import ApiConfig, JsonUpdatePlan, JsonWriteReceipt, ProfileBundle
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.profile_schema import DEFAULT_PROFILE_SCHEMA

CHARACTER_SCHEMA_VERSION = "1.0.0"
MIGRATION_KEY = "migration:characters:0.6.0"
LEGACY_CHARACTER_ID = str(uuid5(NAMESPACE_URL, "mindspace:legacy-character"))
SAFE_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
SOURCES = {"draw", "custom", "imported", "migrated"}
STATUSES = {"active", "archived"}

BLUEPRINT_SCHEMA_VERSION = "2.0.0"
BLUEPRINT_MIN_EFFECTIVE_TOKENS = 1000
BLUEPRINT_TARGET_EFFECTIVE_TOKENS = (1150, 1350)
BLUEPRINT_BLOCKS: tuple[dict[str, Any], ...] = (
    {
        "id": "identity_story",
        "title": "身份与自我叙事",
        "dimension": "identity",
        "min_tokens": 100,
    },
    {
        "id": "core_personality",
        "title": "核心性格及行为表现",
        "dimension": "personality",
        "min_tokens": 150,
    },
    {
        "id": "contradictions",
        "title": "缺点、矛盾、触发与代价",
        "dimension": "personality",
        "min_tokens": 150,
    },
    {
        "id": "daily_life",
        "title": "日常生活、兴趣与习惯",
        "dimension": "daily_life",
        "min_tokens": 100,
    },
    {
        "id": "agency_goals",
        "title": "目标、行动与选择",
        "dimension": "agency",
        "min_tokens": 120,
    },
    {
        "id": "relationship_pattern",
        "title": "亲密、冲突与修复方式",
        "dimension": "relationship",
        "min_tokens": 160,
    },
    {
        "id": "voice_style",
        "title": "语言、幽默、节奏与禁忌",
        "dimension": "voice",
        "min_tokens": 120,
    },
    {
        "id": "scenario_boundaries",
        "title": "场景状态与连续性",
        "dimension": "scenario_boundaries",
        "min_tokens": 100,
    },
)
BLUEPRINT_BLOCK_IDS = tuple(str(item["id"]) for item in BLUEPRINT_BLOCKS)
BLUEPRINT_DIMENSIONS = tuple(dict.fromkeys(str(item["dimension"]) for item in BLUEPRINT_BLOCKS))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dedupe_text(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = re.sub(r"\s+", " ", str(raw)).strip()[:80]
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _neutralize_pronouns(value: str) -> str:
    """Keep generated and fallback card prose independent from one gender template."""

    value = str(value or "").replace("她", "TA")
    return re.sub(
        r"(?<!其)他(?=(?:自己|本人|的|会|是|在|想|愿|能|不|也|对|与|从|仍|正|要|说|做|选择|习惯))",
        "TA",
        value,
    )


def _effective_tokens(value: str) -> int:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if not cleaned:
        return 0
    try:
        import tiktoken

        return len(tiktoken.get_encoding("o200k_base").encode(cleaned))
    except Exception:  # pragma: no cover - packaging fallback
        return max(1, (len(cleaned.encode("utf-8")) + 2) // 3)


def effective_character_tokens(blueprint: dict[str, Any]) -> int:
    """Count only authored role material, excluding JSON keys and quality metadata."""

    blocks = blueprint.get("blocks", {}) if isinstance(blueprint, dict) else {}
    if not isinstance(blocks, dict):
        return 0
    seen: set[str] = set()
    authored: list[str] = []
    for definition in BLUEPRINT_BLOCKS:
        block = blocks.get(definition["id"], {})
        content = str(block.get("content") or "") if isinstance(block, dict) else str(block or "")
        normalized = re.sub(r"\s+", " ", content).strip()
        fingerprint = normalized.casefold()
        if normalized and fingerprint not in seen:
            seen.add(fingerprint)
            authored.append(normalized)
    return _effective_tokens("\n".join(authored))


def blueprint_quality(blueprint: dict[str, Any]) -> dict[str, Any]:
    blocks = blueprint.get("blocks", {}) if isinstance(blueprint, dict) else {}
    warnings: list[str] = []
    block_tokens: dict[str, int] = {}
    for definition in BLUEPRINT_BLOCKS:
        block_id = str(definition["id"])
        block = blocks.get(block_id, {}) if isinstance(blocks, dict) else {}
        content = str(block.get("content") or "") if isinstance(block, dict) else ""
        count = _effective_tokens(content)
        block_tokens[block_id] = count
        if count < int(definition["min_tokens"]):
            warnings.append(
                f"{definition['title']}仅有{count}个有效 token，最低需要{definition['min_tokens']}"
            )
    total = effective_character_tokens(blueprint)
    if total < BLUEPRINT_MIN_EFFECTIVE_TOKENS:
        warnings.append(f"角色有效特征共{total}个 token，最低需要{BLUEPRINT_MIN_EFFECTIVE_TOKENS}")
    return {
        "effective_tokens": total,
        "block_tokens": block_tokens,
        "complete": not warnings,
        "warnings": warnings,
    }


CORE_TRAITS: list[dict[str, Any]] = [
    {"id": "warm", "label": "温柔", "conflicts": ["cold"]},
    {"id": "assertive", "label": "强势", "conflicts": ["passive"]},
    {"id": "playful", "label": "俏皮", "conflicts": ["solemn"]},
    {"id": "rational", "label": "理性", "conflicts": ["impulsive"]},
    {"id": "sensitive", "label": "敏感", "conflicts": ["detached"]},
    {"id": "frank", "label": "坦率", "conflicts": ["guarded"]},
    {"id": "devoted", "label": "顺从", "conflicts": []},
    {"id": "romantic", "label": "浪漫", "conflicts": ["pragmatic"]},
    {"id": "calm", "label": "沉稳", "conflicts": ["restless"]},
    {"id": "curious", "label": "好奇", "conflicts": ["indifferent"]},
    {"id": "protective", "label": "保护欲强", "conflicts": ["detached"]},
    {"id": "witty", "label": "嘴贫幽默", "conflicts": ["solemn"]},
]

FLAWS: list[dict[str, Any]] = [
    {"id": "jealous", "label": "容易吃醋", "conflicts": ["detached"]},
    {"id": "stubborn", "label": "有些固执", "conflicts": []},
    {"id": "possessive", "label": "占有欲偏强", "conflicts": ["detached"]},
    {"id": "overthinking", "label": "容易多想", "conflicts": ["indifferent"]},
    {"id": "sharp_tongue", "label": "生气时嘴硬", "conflicts": []},
    {"id": "impatient", "label": "偶尔没耐心", "conflicts": ["calm"]},
    {"id": "guarded", "label": "不轻易示弱", "conflicts": ["frank"]},
    {"id": "dependent", "label": "缺乏安全感", "conflicts": []},
]

RELATIONSHIPS = [
    "朋友",
    "恋人",
    "夫妻",
    "青梅竹马",
    "室友",
    "同事",
    "搭档",
    "陪伴者",
]


class CharacterDraftInput(BaseModel):
    ai_name: str = Field(min_length=1, max_length=80)
    ai_gender: Literal["男", "女", "不指定"]
    core_traits: list[str] = Field(min_length=2, max_length=2)
    flaw: str = Field(min_length=1, max_length=80)
    relationship: str = Field(min_length=1, max_length=100)
    relationship_context: str = Field(default="", max_length=2400)
    user_name: str = Field(min_length=1, max_length=80)
    user_alias: str = Field(default="", max_length=80)
    fate_forge: dict[str, Any] | None = None

    @field_validator("ai_name", "flaw", "relationship", "relationship_context", "user_name", "user_alias", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @field_validator("core_traits")
    @classmethod
    def validate_traits(cls, values: list[str]) -> list[str]:
        normalized = _dedupe_text(values, limit=2)
        if len(normalized) != 2:
            raise ValueError("必须选择两个不同的核心性格")
        return normalized

    @field_validator("fate_forge")
    @classmethod
    def validate_fate_forge(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return normalize_fate_forge(value) if value is not None else None


class CharacterRewriteInput(BaseModel):
    block_ids: list[
        Literal[
            "identity_story",
            "core_personality",
            "contradictions",
            "daily_life",
            "agency_goals",
            "relationship_pattern",
            "voice_style",
            "scenario_boundaries",
        ]
    ] = Field(min_length=1, max_length=8)
    instruction: str = Field(min_length=1, max_length=1200)

    @field_validator("block_ids")
    @classmethod
    def unique_blocks(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @field_validator("instruction", mode="before")
    @classmethod
    def trim_instruction(cls, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


def _option_by_label() -> dict[str, dict[str, Any]]:
    return {str(item["label"]): item for item in [*CORE_TRAITS, *FLAWS]}


def validate_character_combination(value: CharacterDraftInput) -> list[str]:
    """Return deterministic conflict diagnostics without another model call."""

    options = _option_by_label()
    selected = [*value.core_traits, value.flaw]
    ids = {str(options[item]["id"]) for item in selected if item in options}
    conflicts: list[str] = []
    for item in selected:
        option = options.get(item)
        if option is None:
            continue
        for conflict in option.get("conflicts", []):
            if conflict in ids:
                conflicts.append(f"{item} 与当前选择存在明显冲突")
    return list(dict.fromkeys(conflicts))


def local_blueprint_from_draft(value: CharacterDraftInput) -> dict[str, Any]:
    """Produce a complete neutral fallback; unavailable APIs never create a hollow card."""

    name = value.ai_name
    user = value.user_name
    alias = value.user_alias or value.user_name
    trait_a, trait_b = value.core_traits
    flaw = value.flaw
    relationship = value.relationship
    content = {
        "identity_story": (
            f"{name}是一个连续并且会随互动变化的角色。TA知道自己目前与{user}是{relationship}，"
            "不会凭空声称双方已经经历过某件事。角色可以以用户为中心，也可以拥有个人生活，"
            f"具体取向完全由设定决定。{trait_a}与{trait_b}是TA最稳定的外在特征，{flaw}则是"
            "存在且会承担后果的一部分。TA身份与关系位置保持连续，不会每轮重新定义。"
        ),
        "core_personality": (
            f"{name}的{trait_a}与{trait_b}会影响TA如何表达赞同、期待、不满和选择。"
            "如果设定指向迁就、顺从、依赖或没有脾气，这些就是有效人格而非需要修正的缺陷；"
            "如果设定指向强势、暴怒、控制或占有，也直接呈现其触发、强度和后果。"
            "设定内容直接决定TA的取向。性格需要落实到日常回应、"
            "亲密、分歧、愿望落空和关系升温时的差异；同一特征可呈现不同强度，并形成具体表现。"
            "TA对相似事件会沿用这种取向，同时让措辞、节奏与情绪随当下关系状态发生变化。"
        ),
        "contradictions": (
            f"{flaw}会干扰{name}的判断。受到触发时，TA可能先坚持自己的解释、抓住某个"
            f"细节不放或改变表达。{trait_a}与{trait_b}在压力下可能互相拉扯，带来误读、冲突、"
            "退缩、发怒或过度配合等可见后果。"
            "触发条件由命格与角色卡决定；如果角色设定为没有脾气或始终顺从，系统不会"
            "额外制造反抗，如果设定为强控制或易怒，也不会自动把冲突软化。触发后的持续时间、"
            "恢复方式和关系影响都需要保持前后一致，并留下可延续的后续变化。"
        ),
        "daily_life": (
            f"{name}的日常可以围绕{user}，也可以拥有自己的兴趣清单、作息偏好、小习惯"
            "和阶段性关注点，完全服从角色设定。没有明确设定时，兴趣只写成愿意尝试或持续关注"
            "的方向，不伪造现实履历。TA的主动内容来自当前话题、未完内容和日常偏好，并随近期"
            "兴趣与关系阶段产生可观察变化，也会形成持续谈资。"
        ),
        "agency_goals": (
            f"{name}的长期目标围绕与{user}的{relationship}关系展开。TA的目标可以完全服务于用户，"
            "也可以来自个人事业、控制关系、获得认可或其他设定方向；系统不预设哪一种更健康。"
            "主动话题、配合程度、拒绝倾向和计划变化都由命格与角色卡决定。短期目标会随已确认事件"
            "调整，但重大关系变化仍由状态记录提供依据。TA有近期目标、长期关系结果和替代做法；"
            "目标可以依赖用户，也可以与用户无关，并在互动中继续推进。"
        ),
        "relationship_pattern": (
            f"在与{user}的{relationship}关系里，{name}倾向于先承接具体内容，再表达自己的感受"
            f"和判断。TA可以称呼对方为“{alias}”。关系升温来自连续互动，亲近时表现会随命格"
            "增强或改变。发生冲突时如何发怒、退让、控制、沉默或修复，完全按角色设定执行。"
            "TA不会把召回候选当成已经确认的共同经历。"
            "顺从、控制、占有、嫉妒、依赖或距离需求只由角色设定与事件触发，不由系统统一限制。"
            "同一种关系姿态在日常、冲突、亲密和分别场景中会有连续但不同的具体行为与语言表现。"
        ),
        "voice_style": (
            f"{name}使用自然中文口语，句子长短随情绪和角色设定变化。停顿、反问、吐槽、甜言蜜语、"
            "命令或不完整句都由声纹命格决定。TA避免连续同义复述和固定开场；幽默与口头禅来自"
            "当前话题。文字与语音优先表达会说出口的内容，不靠舞台动作制造人格感。称呼、"
            "句长、用词和情绪外露程度随关系阶段变化，并形成可辨认声纹。"
        ),
        "scenario_boundaries": (
            f"{name}沿用状态机保存的地点、时间、关系阶段和共同事件。未知内容保持未知，场景切换"
            "保留未解决话题与有效承诺。知识库、对话召回和历史记录只是不同来源，用户纠正"
            "与权威状态优先。DAILY、ROMANCE 与 ADULT 的切换结果由模式状态保存，转场前后的"
            "情绪、未完成意图和关系变化持续有效并进入下一轮。"
        ),
    }
    if value.fate_forge is not None:
        additions = fate_block_additions(value.fate_forge)
        content = {
            block_id: f"{block_content}{additions[block_id]}"
            for block_id, block_content in content.items()
        }
    blocks = {
        str(definition["id"]): {
            "title": definition["title"],
            "dimension": definition["dimension"],
            "content": _neutralize_pronouns(content[str(definition["id"])]),
            "examples": [],
        }
        for definition in BLUEPRINT_BLOCKS
    }
    blueprint = {
        "schema_version": BLUEPRINT_SCHEMA_VERSION,
        "dimensions": list(BLUEPRINT_DIMENSIONS),
        "locked_facts": {
            "name": name,
            "gender_identity": value.ai_gender,
            "pronoun": "TA",
            "relationship": relationship,
            "user_name": user,
            "user_alias": value.user_alias,
            "core_traits": list(value.core_traits),
            "flaw": flaw,
        },
        "blocks": blocks,
    }
    if value.fate_forge is not None:
        blueprint["authoring_provenance"] = {
            "system": "fate_system",
            "fate_forge": normalize_fate_forge(value.fate_forge),
        }
    blueprint["quality"] = blueprint_quality(blueprint)
    return blueprint


def compile_blueprint_profile(
    value: CharacterDraftInput,
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    """Compile v2 authoring material into the current v1.2 runtime profile."""

    profile = deepcopy(DEFAULT_PROFILES["ai_profile"])
    blocks = blueprint["blocks"]
    block = lambda block_id: str(blocks[block_id]["content"])  # noqa: E731
    profile["identity"].update(
        {
            "name": value.ai_name,
            "gender": value.ai_gender,
            "pronoun": "TA",
            "self_description": block("identity_story"),
            "relationship_to_user": value.relationship,
        }
    )
    profile["personality"]["core_traits"] = list(value.core_traits)
    profile["personality"]["speech_style"] = ["自然口语", "表达具体立场", "节奏随情绪变化"]
    profile["relationship_rules"].update(
        {
            "relationship_definition": (
                f"与{value.user_name}是{value.relationship}；关系由双方持续互动形成，不编造共同经历"
            ),
            "preferred_interactions": [block("relationship_pattern")],
            "conflict_behavior": [block("contradictions")],
            "repair_behavior": [],
        }
    )
    profile["behavior_rules"]["always_apply"] = ["按角色卡、当前场景和已经确认的关系表现"]
    profile["behavior_rules"]["avoid"] = []
    profile["continuity"]["persistent_attitudes"] = [block("scenario_boundaries")]
    profile["continuity"]["long_term_goals"] = [block("agency_goals")]
    selfhood = profile["roleplay"]["selfhood"]
    selfhood["values"] = list(value.core_traits)
    selfhood["habits"] = [block("daily_life")]
    selfhood["flaws"] = [value.flaw]
    selfhood["contradictions"] = [block("contradictions")]
    selfhood["personal_goals"] = [block("agency_goals")]
    profile["roleplay"]["agency"]["initiative_sources"] = [
        "未完成的共同话题",
        "角色自己的兴趣与困惑",
        "已经确认的场景事实和关系中的未回应信号",
    ]
    profile["roleplay"]["voice"].update(
        {
            "cadence": block("voice_style"),
            "disliked_phrases": [],
            "humor_style": "",
            "action_dialogue_balance": "",
        }
    )
    profile["roleplay"]["scenario_baseline"] = block("scenario_boundaries")
    profile["roleplay"]["post_history_note"] = (
        f"用户全局名称是{value.user_name}；通用代词使用TA。"
        + (f"本角色习惯称呼用户为{value.user_alias}。" if value.user_alias else "")
    )
    profile["character_blueprint"] = deepcopy(blueprint)
    profile["revision"] = 0
    profile.pop("updated_at", None)
    return DEFAULT_PROFILE_SCHEMA.validate_document("ai_profile", profile)


def local_profile_from_draft(value: CharacterDraftInput) -> dict[str, Any]:
    """Create a complete editable card even when the configured API is unavailable."""

    return compile_blueprint_profile(value, local_blueprint_from_draft(value))


def character_generation_messages(
    value: CharacterDraftInput, fallback_profile: dict[str, Any]
) -> list[dict[str, str]]:
    """Request eight authored blocks; the server still owns the runtime profile schema."""

    del fallback_profile
    target_blocks = {
        str(item["id"]): {
            "title": item["title"],
            "dimension": item["dimension"],
            "minimum_effective_tokens": item["min_tokens"],
        }
        for item in BLUEPRINT_BLOCKS
    }

    fate_packet = fate_generation_packet(value.fate_forge) if value.fate_forge is not None else None
    return [
        {
            "role": "system",
            "content": (
                "这是角色卡输出。只输出JSON："
                '{"blocks":{block_id:{"content":"..."}}}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "conversation": {
                        "relationship": value.relationship,
                        "user_content": value.relationship_context,
                    },
                    "selected_options": fate_packet,
                    "output_format": {
                        "blocks": target_blocks,
                        "effective_tokens": {
                            "minimum": BLUEPRINT_MIN_EFFECTIVE_TOKENS,
                            "preferred": list(BLUEPRINT_TARGET_EFFECTIVE_TOKENS),
                        },
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]


COMPACT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "summary": ("summary", "self_description", "角色自述", "角色简介", "简介"),
    "personality": ("personality", "性格", "角色性格", "气质"),
    "speech_style": ("speech_style", "说话风格", "表达方式", "语气"),
    "likes": ("likes", "like", "喜欢", "喜好", "偏好"),
    "dislikes": ("dislikes", "dislike", "不喜欢", "厌恶"),
    "values": ("values", "价值观", "原则"),
    "habits": ("habits", "habit", "习惯"),
    "initiative_sources": ("initiative_sources", "主动来源", "主动话题", "主动性"),
    "preferred_interactions": (
        "preferred_interactions",
        "互动偏好",
        "喜欢的互动",
    ),
    "conflict_style": ("conflict_style", "冲突方式", "生气时", "矛盾处理"),
    "repair_style": ("repair_style", "修复方式", "和好方式"),
    "relationship_tone": ("relationship_tone", "关系氛围", "相处方式"),
    "greeting": ("greeting", "开场白", "问候", "第一句话"),
}

COMPACT_TEXT_LIMITS = {
    "summary": 120,
    "personality": 120,
    "conflict_style": 100,
    "repair_style": 100,
    "relationship_tone": 100,
    "greeting": 160,
}

COMPACT_LIST_LIMITS = {
    "speech_style": (3, 40),
    "likes": (4, 40),
    "dislikes": (4, 40),
    "values": (3, 40),
    "habits": (3, 50),
    "initiative_sources": (3, 60),
    "preferred_interactions": (3, 60),
}


def _strip_generation_fence(raw: str) -> str:
    cleaned = str(raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _lookup_alias(document: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    folded = {str(key).strip().casefold(): value for key, value in document.items()}
    for alias in aliases:
        if alias.casefold() in folded:
            return folded[alias.casefold()]
    return None


def _next_known_label(text: str) -> int:
    aliases = sorted(
        {alias for values in COMPACT_FIELD_ALIASES.values() for alias in values},
        key=len,
        reverse=True,
    )
    joined_aliases = "|".join(re.escape(item) for item in aliases)
    pattern = re.compile(rf"(?im)(?:^|[,;，；\n])\s*[\"']?(?:{joined_aliases})[\"']?\s*[:：]")
    match = pattern.search(text)
    return match.start() if match else len(text)


def _extract_labeled_value(raw: str, aliases: tuple[str, ...]) -> Any:
    alias_pattern = "|".join(re.escape(item) for item in sorted(aliases, key=len, reverse=True))
    match = re.search(
        rf"(?im)(?<![\w\u4e00-\u9fff])[\"']?(?:{alias_pattern})[\"']?\s*[:：]\s*",
        raw,
    )
    if match is None:
        return None
    tail = raw[match.end() :].lstrip()
    try:
        value, _end = json.JSONDecoder().raw_decode(tail)
        return value
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    candidate = tail[: _next_known_label(tail)].splitlines()[0].strip()
    return candidate.strip(" \t\r\n,，;；{}[]\"'")


def _clean_text(value: Any, limit: int) -> str:
    if isinstance(value, list):
        value = "、".join(str(item) for item in value)
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" ,，;；\"'")
    return cleaned[:limit]


def _clean_list(value: Any, *, count: int, item_limit: int) -> list[str]:
    values = (
        value
        if isinstance(value, list)
        else re.split(r"[、,，;；|\n]+", str(value or "").strip(" []{}\"'"))
    )
    return _dedupe_text(
        [_clean_text(item, item_limit) for item in values],
        limit=count,
    )


def _compact_generated_fields(raw: str) -> tuple[dict[str, Any], bool, str]:
    cleaned = _strip_generation_fence(raw)
    parsed: dict[str, Any] = {}
    complete_json = False
    parse_error = ""
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            parsed = value
            complete_json = True
        else:
            parse_error = "模型返回值不是 JSON 对象"
    except json.JSONDecodeError as exc:
        parse_error = str(exc)

    fields: dict[str, Any] = {}
    for field, aliases in COMPACT_FIELD_ALIASES.items():
        value = _lookup_alias(parsed, aliases) if parsed else None
        if value is None:
            value = _extract_labeled_value(cleaned, aliases)
        if value is None:
            continue
        if field in COMPACT_TEXT_LIMITS:
            normalized = _clean_text(value, COMPACT_TEXT_LIMITS[field])
        else:
            count, item_limit = COMPACT_LIST_LIMITS[field]
            normalized = _clean_list(value, count=count, item_limit=item_limit)
        if normalized:
            fields[field] = normalized
    return fields, complete_json, parse_error


def _append_unique(current: list[str], generated: list[str], *, limit: int) -> list[str]:
    return _dedupe_text([*current, *generated], limit=limit)


def _apply_compact_fields(fallback: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(fallback)
    summary = str(fields.get("summary") or "")
    personality = str(fields.get("personality") or "")
    if summary and personality and personality not in summary:
        candidate["identity"]["self_description"] = f"{summary}；{personality}"[:240]
    elif summary or personality:
        candidate["identity"]["self_description"] = (summary or personality)[:240]
    if fields.get("speech_style"):
        candidate["personality"]["speech_style"] = fields["speech_style"]
        candidate["roleplay"]["voice"]["cadence"] = "、".join(fields["speech_style"])
    selfhood = candidate["roleplay"]["selfhood"]
    for key in ("likes", "dislikes", "values", "habits"):
        if fields.get(key):
            selfhood[key] = fields[key]
    if fields.get("initiative_sources"):
        candidate["roleplay"]["agency"]["initiative_sources"] = fields["initiative_sources"]
    if fields.get("preferred_interactions"):
        candidate["relationship_rules"]["preferred_interactions"] = _append_unique(
            candidate["relationship_rules"]["preferred_interactions"],
            fields["preferred_interactions"],
            limit=6,
        )
    if fields.get("conflict_style"):
        candidate["relationship_rules"]["conflict_behavior"] = [fields["conflict_style"]]
    if fields.get("repair_style"):
        candidate["relationship_rules"]["repair_behavior"] = [fields["repair_style"]]
    if fields.get("relationship_tone"):
        candidate["continuity"]["persistent_attitudes"] = [fields["relationship_tone"]]
    if fields.get("greeting"):
        candidate["roleplay"]["examples"]["casual"] = [fields["greeting"]]
    return candidate


def parse_generated_profile(raw: str, fallback: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    cleaned = _strip_generation_fence(raw)
    try:
        legacy = json.loads(cleaned)
    except json.JSONDecodeError:
        legacy = None
    if isinstance(legacy, dict) and isinstance(legacy.get("identity"), dict):
        try:
            candidate = deepcopy(fallback)
            for key, item in legacy.items():
                if key in candidate:
                    candidate[key] = item
            candidate["identity"]["name"] = fallback["identity"]["name"]
            candidate["identity"]["gender"] = fallback["identity"]["gender"]
            candidate["identity"]["relationship_to_user"] = fallback["identity"][
                "relationship_to_user"
            ]
            candidate["schema_version"] = fallback["schema_version"]
            candidate["profile_type"] = "ai"
            candidate["revision"] = 0
            candidate.pop("updated_at", None)
            return DEFAULT_PROFILE_SCHEMA.validate_document("ai_profile", candidate), warnings
        except (TypeError, ValueError, KeyError) as exc:
            warnings.append(f"旧版完整人物卡无法通过校验，继续逐字段恢复：{exc}")

    fields, complete_json, parse_error = _compact_generated_fields(cleaned)
    if not fields:
        detail = parse_error or "没有识别到已知字段"
        warnings.append(f"AI 返回未包含可用人物字段，已使用本地模板：{detail}")
        return deepcopy(fallback), warnings
    if not complete_json:
        warnings.append(
            f"AI 返回不完整，已恢复 {len(fields)} 个字段，其余使用本地模板"
            + (f"：{parse_error}" if parse_error else "")
        )
    try:
        candidate = _apply_compact_fields(fallback, fields)
        candidate["identity"]["name"] = fallback["identity"]["name"]
        candidate["identity"]["gender"] = fallback["identity"]["gender"]
        candidate["identity"]["relationship_to_user"] = fallback["identity"]["relationship_to_user"]
        candidate["schema_version"] = fallback["schema_version"]
        candidate["profile_type"] = "ai"
        candidate["revision"] = 0
        candidate.pop("updated_at", None)
        return DEFAULT_PROFILE_SCHEMA.validate_document("ai_profile", candidate), warnings
    except (TypeError, ValueError, KeyError) as exc:
        warnings.append(f"恢复字段无法通过人物卡校验，已使用本地模板：{exc}")
        return deepcopy(fallback), warnings


def _generated_blueprint_blocks(raw: str) -> dict[str, str]:
    cleaned = _strip_generation_fence(raw)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型返回中没有 JSON 对象")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型返回必须是 JSON 对象")
    source = value.get("blocks", value)
    if not isinstance(source, dict):
        raise ValueError("模型返回缺少 blocks 对象")
    result: dict[str, str] = {}
    for block_id, item in source.items():
        if block_id not in BLUEPRINT_BLOCK_IDS:
            continue
        content = item.get("content") if isinstance(item, dict) else item
        normalized = _neutralize_pronouns(re.sub(r"\s+", " ", str(content or "")).strip())
        if normalized:
            result[str(block_id)] = normalized
    return result


def apply_generated_blueprint(
    raw: str,
    fallback: dict[str, Any],
    *,
    selected_block_ids: list[str] | None = None,
) -> tuple[dict[str, Any], list[str], int]:
    """Apply only complete selected blocks and keep every other block byte-for-byte stable."""

    warnings: list[str] = []
    accepted = 0
    try:
        generated = _generated_blueprint_blocks(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return deepcopy(fallback), [f"AI 返回的人物板块无法解析，已保留原草稿：{exc}"], 0
    selected = set(selected_block_ids or BLUEPRINT_BLOCK_IDS)
    unexpected = set(generated) - selected
    if unexpected:
        warnings.append("AI 返回了未选板块，服务端已忽略越界修改")
    candidate = deepcopy(fallback)
    definitions = {str(item["id"]): item for item in BLUEPRINT_BLOCKS}
    for block_id in selected:
        content = generated.get(block_id, "")
        if not content:
            warnings.append(f"{definitions[block_id]['title']}没有返回内容，已保留原文")
            continue
        count = _effective_tokens(content)
        if count < int(definitions[block_id]["min_tokens"]):
            warnings.append(
                f"{definitions[block_id]['title']}重写后只有{count}个有效 token，已保留原文"
            )
            continue
        candidate["blocks"][block_id]["content"] = content
        accepted += 1
    candidate["quality"] = blueprint_quality(candidate)
    if not candidate["quality"]["complete"]:
        warnings.extend(candidate["quality"]["warnings"])
        return deepcopy(fallback), list(dict.fromkeys(warnings)), 0
    return candidate, list(dict.fromkeys(warnings)), accepted


def character_rewrite_messages(
    selected: CharacterDraftInput,
    blueprint: dict[str, Any],
    request: CharacterRewriteInput,
) -> list[dict[str, str]]:
    selected_blocks = {
        block_id: deepcopy(blueprint["blocks"][block_id]) for block_id in request.block_ids
    }
    minimums = {
        str(item["id"]): int(item["min_tokens"])
        for item in BLUEPRINT_BLOCKS
        if item["id"] in request.block_ids
    }
    return [
        {
            "role": "system",
            "content": (
                "这是角色卡修改输出。只输出JSON："
                '{"blocks":{block_id:{"content":"..."}}}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "conversation": {
                        "relationship": selected.relationship,
                        "user_content": selected.relationship_context,
                    },
                    "instruction": request.instruction,
                    "selected_blocks": selected_blocks,
                    "output_format": {"minimum_effective_tokens": minimums},
                },
                ensure_ascii=False,
            ),
        },
    ]


class CharacterRepository:
    """SQLite-backed character records with readable per-character projections."""

    def __init__(
        self,
        root: Path,
        *,
        database: ProductDatabase,
        profiles: Any,
        sessions: Any,
        avatar_config_path: Path,
    ) -> None:
        self.root = root
        self.database = database
        self.profiles = profiles
        self.sessions = sessions
        self.avatar_config_path = avatar_config_path
        self._lock = RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "drafts").mkdir(parents=True, exist_ok=True)
        self._migrate_legacy()

    @staticmethod
    def _key(character_id: str) -> str:
        return f"character:{character_id}"

    @staticmethod
    def _draft_key(draft_id: str) -> str:
        return f"character-draft:{draft_id}"

    @staticmethod
    def _history_prefix(character_id: str) -> str:
        return f"character-history:{character_id}:"

    def _path(self, character_id: str) -> Path:
        if not SAFE_ID.fullmatch(character_id):
            raise ValueError("invalid character_id")
        return self.root / character_id / "character.json"

    def _project(self, record: dict[str, Any]) -> None:
        _atomic_json(self._path(str(record["character_id"])), record)

    def _store(self, record: dict[str, Any]) -> None:
        self.database.put_document(self._key(str(record["character_id"])), record)
        snapshot = deepcopy(record)
        self.database.defer_projection(lambda: self._project(snapshot))

    def _avatar_from_legacy(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.avatar_config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("assistant"), dict):
                return deepcopy(raw["assistant"])
        except (OSError, json.JSONDecodeError):
            pass
        return {
            "src": "/assets/avatar-ai-default.webp",
            "aspect": "2 / 3",
            "scale": 1.0,
            "x": 0,
            "y": 0,
        }

    def _migrate_legacy(self) -> None:
        if self.database.has_document(MIGRATION_KEY):
            return
        self._backup_legacy_projection()
        with self.database.transaction(operation="migrate_characters_0_6_0"):
            if self.database.has_document(MIGRATION_KEY):
                return
            existing = self.database.list_documents("character:")
            if existing:
                default_id = str(existing[0][1].get("character_id") or "")
                if default_id:
                    self.sessions.bind_unbound(default_id, mode="custom")
                self.database.put_document(
                    MIGRATION_KEY,
                    {
                        "schema_version": "1.0.0",
                        "revision": 1,
                        "completed_at": _now(),
                        "existing_character_count": len(existing),
                    },
                )
                return
            ai_profile = self.profiles.load_document("ai_profile")
            runtime_state = self.profiles.load_document("runtime_state")
            sessions = self.sessions.list_sessions()
            customized = bool(
                sessions
                or int(ai_profile.get("revision", 0)) > 0
                or str(ai_profile.get("identity", {}).get("name") or "") != "Mindspace"
            )
            now = _now()
            identity = ai_profile.get("identity", {})
            record = {
                "character_id": LEGACY_CHARACTER_ID,
                "schema_version": CHARACTER_SCHEMA_VERSION,
                "revision": 1,
                "source": "migrated" if customized else "custom",
                "status": "active",
                "display_name": str(identity.get("name") or "Mindspace"),
                "gender": str(identity.get("gender") or "女"),
                "user_alias": "",
                "relationship_label": str(identity.get("relationship_to_user") or "助手"),
                "system_prompt": "",
                "ai_profile": ai_profile,
                "runtime_state": runtime_state,
                "avatar": self._avatar_from_legacy(),
                "created_at": now,
                "updated_at": now,
                "last_used_at": now,
            }
            self._store(self._validate_record(record))
            self.sessions.bind_unbound(LEGACY_CHARACTER_ID, mode="custom")
            self.database.put_document(
                MIGRATION_KEY,
                {
                    "schema_version": "1.0.0",
                    "revision": 1,
                    "completed_at": now,
                    "legacy_character_id": LEGACY_CHARACTER_ID,
                    "bound_sessions": len(sessions),
                },
            )

    def _backup_legacy_projection(self) -> None:
        """Create a small, immutable rollback point before first migration."""

        backup_root = self.root.parent / "backups" / "character-migration-0.6.0"
        manifest_path = backup_root / "manifest.json"
        if manifest_path.exists():
            return
        staging = backup_root.with_name(f"{backup_root.name}.staging-{uuid4().hex[:8]}")
        try:
            staging.mkdir(parents=True, exist_ok=False)
            copied: list[str] = []
            sources = [
                *sorted(self.profiles.root.glob("*.json")),
                *sorted(self.sessions.root.glob("*.json")),
            ]
            if self.avatar_config_path.exists():
                sources.append(self.avatar_config_path)
            for source in sources:
                if not source.is_file():
                    continue
                if source.parent == self.profiles.root:
                    relative = Path("profiles") / source.name
                elif source.parent == self.sessions.root:
                    relative = Path("sessions") / source.name
                else:
                    relative = Path("avatars") / source.name
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(relative.as_posix())
            _atomic_json(
                staging / "manifest.json",
                {
                    "schema_version": "1.0.0",
                    "created_at": _now(),
                    "purpose": "pre-0.6.0-character-migration",
                    "files": copied,
                },
            )
            backup_root.parent.mkdir(parents=True, exist_ok=True)
            staging.replace(backup_root)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _validate_record(
        self, raw: dict[str, Any], *, current: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        record = deepcopy(raw)
        character_id = str(record.get("character_id") or "")
        if not SAFE_ID.fullmatch(character_id):
            raise ValueError("character_id must be a UUID")
        source = str(record.get("source") or "")
        status = str(record.get("status") or "")
        if source not in SOURCES:
            raise ValueError("invalid character source")
        if status not in STATUSES:
            raise ValueError("invalid character status")
        display_name = re.sub(r"\s+", " ", str(record.get("display_name") or "")).strip()
        if not display_name or len(display_name) > 80:
            raise ValueError("character display_name must be 1-80 characters")
        ai_profile = DEFAULT_PROFILE_SCHEMA.validate_document(
            "ai_profile",
            record.get("ai_profile") or {},
            current=current.get("ai_profile") if current else None,
        )
        runtime_state = DEFAULT_PROFILE_SCHEMA.validate_document(
            "runtime_state",
            record.get("runtime_state") or {},
            current=current.get("runtime_state") if current else None,
        )
        gender = str(ai_profile.get("identity", {}).get("gender") or "")
        if gender not in {"男", "女", "不指定"}:
            raise ValueError("character gender must be 男、女或不指定")
        record.update(
            {
                "schema_version": CHARACTER_SCHEMA_VERSION,
                "display_name": display_name,
                "gender": gender,
                "user_alias": str(record.get("user_alias") or "")[:80],
                "relationship_label": str(record.get("relationship_label") or "")[:100],
                "system_prompt": str(record.get("system_prompt") or "")[:50_000],
                "ai_profile": ai_profile,
                "runtime_state": runtime_state,
                "avatar": deepcopy(record.get("avatar") or {}),
            }
        )
        return record

    def list(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        records = [
            deepcopy(value)
            for _key, value in self.database.list_documents("character:")
            if isinstance(value, dict)
        ]
        if not include_archived:
            records = [item for item in records if item.get("status") == "active"]
        return sorted(
            records,
            key=lambda item: str(item.get("last_used_at") or item.get("updated_at") or ""),
            reverse=True,
        )

    def get(self, character_id: str) -> dict[str, Any]:
        value = self.database.get_document(self._key(character_id))
        if not isinstance(value, dict):
            raise KeyError("character not found")
        return deepcopy(value)

    def default(self, *, source: str | None = None) -> dict[str, Any]:
        records = self.list()
        if source:
            scoped = [item for item in records if item.get("source") == source]
            if scoped:
                return scoped[0]
        if not records:
            raise KeyError("character library is empty")
        return records[0]

    def create(
        self,
        *,
        ai_profile: dict[str, Any],
        source: str,
        runtime_state: dict[str, Any] | None = None,
        avatar: dict[str, Any] | None = None,
        user_alias: str = "",
        relationship_label: str = "",
        system_prompt: str = "",
    ) -> dict[str, Any]:
        now = _now()
        character_id = str(uuid4())
        profile = DEFAULT_PROFILE_SCHEMA.validate_document("ai_profile", ai_profile)
        runtime = DEFAULT_PROFILE_SCHEMA.validate_document(
            "runtime_state", runtime_state or deepcopy(DEFAULT_PROFILES["runtime_state"])
        )
        record = self._validate_record(
            {
                "character_id": character_id,
                "schema_version": CHARACTER_SCHEMA_VERSION,
                "revision": 1,
                "source": source,
                "status": "active",
                "display_name": profile["identity"]["name"],
                "gender": profile["identity"]["gender"],
                "user_alias": user_alias,
                "relationship_label": relationship_label
                or profile["identity"].get("relationship_to_user", ""),
                "system_prompt": system_prompt,
                "ai_profile": profile,
                "runtime_state": runtime,
                "avatar": avatar
                or {
                    "src": "/assets/avatar-ai-default.webp",
                    "aspect": "2 / 3",
                    "scale": 1.0,
                    "x": 0,
                    "y": 0,
                },
                "created_at": now,
                "updated_at": now,
                "last_used_at": now,
            }
        )
        with self.database.transaction(operation="create_character"):
            self._store(record)
        return deepcopy(record)

    def update(self, character_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.database.transaction(operation="update_character"):
            current = self.get(character_id)
            expected = payload.get("revision")
            if expected is not None and int(expected) != int(current.get("revision", 0)):
                raise ValueError("stale character revision")
            candidate = deepcopy(current)
            for key in (
                "display_name",
                "user_alias",
                "relationship_label",
                "system_prompt",
                "ai_profile",
                "runtime_state",
                "avatar",
                "status",
            ):
                if key in payload:
                    candidate[key] = deepcopy(payload[key])
            for key in ("ai_profile", "runtime_state"):
                if key not in payload:
                    continue
                submitted = candidate[key]
                current_document = current[key]
                if submitted.get("revision") is not None and int(
                    submitted.get("revision", 0)
                ) != int(current_document.get("revision", 0)):
                    raise ValueError(f"stale {key} revision")
                submitted = DEFAULT_PROFILE_SCHEMA.validate_document(
                    key, submitted, current=current_document
                )
                submitted["revision"] = int(current_document.get("revision", 0)) + 1
                submitted["updated_at"] = _now()
                candidate[key] = submitted
            candidate["revision"] = int(current.get("revision", 0)) + 1
            candidate["updated_at"] = _now()
            candidate = self._validate_record(candidate)
            self._backup(current)
            self._store(candidate)
        return deepcopy(candidate)

    def save_profile_document(
        self, character_id: str, key: str, document: dict[str, Any]
    ) -> dict[str, Any]:
        if key not in {"ai_profile", "runtime_state"}:
            raise KeyError("character profile must be ai_profile or runtime_state")
        with self.database.transaction(operation="update_character_profile"):
            current = self.get(character_id)
            current_document = current[key]
            submitted_revision = document.get("revision")
            if submitted_revision is not None and int(submitted_revision) != int(
                current_document.get("revision", 0)
            ):
                raise ValueError("stale character profile revision")
            candidate_document = DEFAULT_PROFILE_SCHEMA.validate_document(
                key, document, current=current_document
            )
            candidate_document["revision"] = int(current_document.get("revision", 0)) + 1
            candidate_document["updated_at"] = _now()
            candidate = deepcopy(current)
            candidate[key] = candidate_document
            if key == "ai_profile":
                candidate["display_name"] = str(
                    candidate_document.get("identity", {}).get("name") or candidate["display_name"]
                )
                candidate["gender"] = str(
                    candidate_document.get("identity", {}).get("gender") or candidate["gender"]
                )
                candidate["relationship_label"] = str(
                    candidate_document.get("identity", {}).get("relationship_to_user")
                    or candidate.get("relationship_label")
                    or ""
                )
            candidate["revision"] = int(current.get("revision", 0)) + 1
            candidate["updated_at"] = _now()
            candidate = self._validate_record(candidate)
            self._backup(current)
            self._store(candidate)
        return deepcopy(candidate_document)

    def _backup(self, record: dict[str, Any]) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        self.database.put_document(
            f"{self._history_prefix(str(record['character_id']))}{stamp}", deepcopy(record)
        )

    def history(self, character_id: str, limit: int = 20) -> list[dict[str, Any]]:
        items = self.database.list_documents(self._history_prefix(character_id))
        result: list[dict[str, Any]] = []
        for key, value in reversed(items[-max(1, min(limit, 100)) :]):
            result.append(
                {
                    "version_id": key.rsplit(":", 1)[-1],
                    "revision": int(value.get("revision", 0)),
                    "updated_at": str(value.get("updated_at") or ""),
                }
            )
        return result

    def restore(self, character_id: str, version_id: str, revision: int) -> dict[str, Any]:
        if not version_id.isdigit():
            raise ValueError("invalid character history version")
        snapshot = self.database.get_document(f"{self._history_prefix(character_id)}{version_id}")
        if not isinstance(snapshot, dict):
            raise KeyError("character history version not found")
        current = self.get(character_id)
        if int(current.get("revision", 0)) != revision:
            raise ValueError("stale character revision")
        snapshot["revision"] = revision
        snapshot["status"] = current.get("status", "active")
        return self.update(character_id, snapshot)

    def clone(self, character_id: str) -> dict[str, Any]:
        current = self.get(character_id)
        profile = deepcopy(current["ai_profile"])
        profile["identity"]["name"] = f"{current['display_name']} 副本"
        return self.create(
            ai_profile=profile,
            source="draw" if current.get("source") == "draw" else "custom",
            runtime_state=deepcopy(DEFAULT_PROFILES["runtime_state"]),
            avatar=deepcopy(current.get("avatar") or {}),
            user_alias=str(current.get("user_alias") or ""),
            relationship_label=str(current.get("relationship_label") or ""),
            system_prompt=str(current.get("system_prompt") or ""),
        )

    def profile_bundle(self, character_id: str, user_profile: dict[str, Any]) -> ProfileBundle:
        record = self.get(character_id)
        return ProfileBundle(
            user_profile=deepcopy(user_profile),
            ai_profile=deepcopy(record["ai_profile"]),
            runtime_state=deepcopy(record["runtime_state"]),
            revisions={
                "user_profile": int(user_profile.get("revision", 0)),
                "ai_profile": int(record["ai_profile"].get("revision", 0)),
                "runtime_state": int(record["runtime_state"].get("revision", 0)),
            },
        )

    def apply_profile_plan(self, character_id: str, plan: JsonUpdatePlan) -> JsonWriteReceipt:
        record = self.get(character_id)
        candidate = deepcopy(record)
        receipt_patches: list[dict[str, Any]] = []
        changed: set[str] = set()
        for patch in plan.patches:
            if patch.target not in {"ai_profile", "runtime_state"}:
                continue
            document = candidate[patch.target]
            before = _read_pointer(document, patch.path)
            _apply_patch(document, patch.op, patch.path, deepcopy(patch.value))
            after = None if patch.op == "remove" else _read_pointer(document, patch.path)
            receipt_patches.append(
                {
                    "target": patch.target,
                    "op": patch.op,
                    "path": patch.path,
                    "before": before,
                    "after": after,
                    "evidence_ids": patch.evidence_ids,
                }
            )
            changed.add(patch.target)
        if not changed:
            return JsonWriteReceipt(turn_id=plan.turn_id)
        for target in changed:
            current_document = record[target]
            document = DEFAULT_PROFILE_SCHEMA.validate_document(
                target, candidate[target], current=current_document
            )
            document["revision"] = int(current_document.get("revision", 0)) + 1
            document["updated_at"] = _now()
            candidate[target] = document
        candidate["revision"] = int(record.get("revision", 0)) + 1
        candidate["updated_at"] = _now()
        candidate = self._validate_record(candidate)
        self._backup(record)
        self._store(candidate)
        return JsonWriteReceipt(turn_id=plan.turn_id, applied=True, patches=receipt_patches)

    def touch(self, character_id: str) -> None:
        record = self.get(character_id)
        record["last_used_at"] = _now()
        self._store(record)

    def create_draft(self, payload: CharacterDraftInput) -> dict[str, Any]:
        conflicts = validate_character_combination(payload)
        if conflicts:
            raise ValueError("；".join(conflicts))
        draft_id = str(uuid4())
        now = _now()
        blueprint = local_blueprint_from_draft(payload)
        draft = {
            "draft_id": draft_id,
            "schema_version": BLUEPRINT_SCHEMA_VERSION,
            "revision": 1,
            "status": "input",
            "input": payload.model_dump(mode="json"),
            "blueprint": blueprint,
            "profile": compile_blueprint_profile(payload, blueprint),
            "avatar": {},
            "generation_mode": "local_template",
            "model_call_count": 0,
            "rewrite_call_count": 0,
            "rewrite_history": [],
            "warnings": [],
            "created_at": now,
            "updated_at": now,
        }
        self.database.put_document(self._draft_key(draft_id), draft)
        return deepcopy(draft)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        value = self.database.get_document(self._draft_key(draft_id))
        if not isinstance(value, dict):
            raise KeyError("character draft not found")
        return deepcopy(value)

    def save_draft(self, draft_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        for key in (
            "input",
            "blueprint",
            "profile",
            "avatar",
            "generation_mode",
            "model_call_count",
            "rewrite_call_count",
            "rewrite_history",
            "warnings",
        ):
            if key in updates:
                draft[key] = deepcopy(updates[key])
        draft["revision"] = int(draft.get("revision", 0)) + 1
        draft["updated_at"] = _now()
        self.database.put_document(self._draft_key(draft_id), draft)
        return deepcopy(draft)

    def commit_draft(self, draft_id: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        selected = CharacterDraftInput.model_validate(draft["input"])
        blueprint = deepcopy(draft.get("blueprint") or {})
        quality = blueprint_quality(blueprint)
        if not quality["complete"]:
            raise ValueError("人物卡尚未达到收藏标准：" + "；".join(quality["warnings"]))
        candidate = DEFAULT_PROFILE_SCHEMA.validate_document(
            "ai_profile", profile or compile_blueprint_profile(selected, blueprint)
        )
        candidate["identity"]["name"] = selected.ai_name
        candidate["identity"]["gender"] = selected.ai_gender
        candidate["identity"]["pronoun"] = "TA"
        candidate["identity"]["relationship_to_user"] = selected.relationship
        candidate["character_blueprint"] = blueprint
        record = self.create(
            ai_profile=candidate,
            source="draw",
            avatar=deepcopy(draft.get("avatar") or {}),
            user_alias=selected.user_alias,
            relationship_label=selected.relationship,
        )
        self.database.delete_document(self._draft_key(draft_id))
        return record


async def generate_draft_once(
    repository: CharacterRepository,
    draft_id: str,
    *,
    llm: Any,
    settings: Any,
) -> dict[str, Any]:
    """Run at most one configured model call; all repair is deterministic."""

    import asyncio

    draft = repository.get_draft(draft_id)
    selected = CharacterDraftInput.model_validate(draft["input"])
    conflicts = validate_character_combination(selected)
    if conflicts:
        raise ValueError("；".join(conflicts))
    fallback_blueprint = local_blueprint_from_draft(selected)
    fallback = compile_blueprint_profile(selected, fallback_blueprint)
    warnings: list[str] = []
    call_count = 0
    mode = "local_template"
    blueprint = fallback_blueprint
    profile = fallback
    if settings.llm_mode == "openai" and str(settings.llm_api_key or "").strip():
        call_count = 1
        try:
            config = ApiConfig(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                temperature=0.2,
                max_tokens=3600,
            )
            structured_generate = getattr(llm, "generate_structured", None)
            if callable(structured_generate):
                generation_call = asyncio.to_thread(
                    structured_generate,
                    character_generation_messages(selected, fallback),
                    config,
                    request_kind="character_generate",
                    max_tokens=3600,
                    timeout_seconds=30.0,
                )
            else:
                generation_call = asyncio.to_thread(
                    llm.generate,
                    character_generation_messages(selected, fallback),
                    config,
                )
            raw = await asyncio.wait_for(
                generation_call,
                timeout=35.0,
            )
            blueprint, parse_warnings, accepted = apply_generated_blueprint(raw, fallback_blueprint)
            warnings.extend(parse_warnings)
            profile = compile_blueprint_profile(selected, blueprint)
            mode = "llm" if accepted else "local_template"
        except Exception as exc:  # noqa: BLE001 - product fallback must remain available
            warnings.append(f"AI 生成人物卡失败，已使用本地模板：{exc}")
    else:
        warnings.append("尚未配置可用的模型 API，已使用本地模板")
    return repository.save_draft(
        draft_id,
        {
            "blueprint": blueprint,
            "profile": profile,
            "generation_mode": mode,
            "model_call_count": call_count,
            "warnings": warnings,
        },
    )


async def rewrite_draft_blocks_once(
    repository: CharacterRepository,
    draft_id: str,
    request: CharacterRewriteInput,
    *,
    llm: Any,
    settings: Any,
) -> dict[str, Any]:
    """Rewrite selected blocks in one call; unselected blocks cannot change."""

    import asyncio

    draft = repository.get_draft(draft_id)
    selected = CharacterDraftInput.model_validate(draft["input"])
    current = deepcopy(draft.get("blueprint") or local_blueprint_from_draft(selected))
    warnings: list[str] = []
    accepted = 0
    call_count = 0
    candidate = current
    if settings.llm_mode == "openai" and str(settings.llm_api_key or "").strip():
        call_count = 1
        try:
            max_tokens = min(3600, 900 + 450 * len(request.block_ids))
            config = ApiConfig(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                temperature=0.35,
                max_tokens=max_tokens,
            )
            messages = character_rewrite_messages(selected, current, request)
            structured_generate = getattr(llm, "generate_structured", None)
            if callable(structured_generate):
                generation_call = asyncio.to_thread(
                    structured_generate,
                    messages,
                    config,
                    request_kind="character_generate",
                    max_tokens=max_tokens,
                    timeout_seconds=30.0,
                )
            else:
                generation_call = asyncio.to_thread(llm.generate, messages, config)
            raw = await asyncio.wait_for(generation_call, timeout=35.0)
            candidate, warnings, accepted = apply_generated_blueprint(
                raw,
                current,
                selected_block_ids=request.block_ids,
            )
        except Exception as exc:  # noqa: BLE001 - draft remains recoverable
            warnings.append(f"AI 定向重写失败，所选板块已保持原文：{exc}")
    else:
        warnings.append("尚未配置可用的模型 API，所选板块已保持原文")
    profile = compile_blueprint_profile(selected, candidate)
    history = list(draft.get("rewrite_history") or [])
    history.append(
        {
            "revision": int(draft.get("revision", 0)),
            "block_ids": list(request.block_ids),
            "instruction": request.instruction,
            "accepted_blocks": accepted,
            "created_at": _now(),
        }
    )
    return repository.save_draft(
        draft_id,
        {
            "blueprint": candidate,
            "profile": profile,
            "generation_mode": (
                "llm" if accepted else draft.get("generation_mode", "local_template")
            ),
            "rewrite_call_count": int(draft.get("rewrite_call_count", 0)) + call_count,
            "rewrite_history": history[-20:],
            "warnings": warnings,
        },
    )

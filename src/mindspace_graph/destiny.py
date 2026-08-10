"""The V7 Destiny Canvas authoring journey.

This module intentionally keeps generation small and deterministic: one call for
eight directions, one visible 96-card action backed by two independent 48-card
calls, and one call for the V2 character card. A malformed provider response is
saved as a failed batch and is never "fixed" with a hidden follow-up request.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from mindspace_graph.character_card import normalize_appearance, normalize_card
from mindspace_graph.models import ApiConfig
from mindspace_graph.product_database import ProductDatabase

DESTINY_SCHEMA_VERSION = "7.0.0"
JOURNEY_SCHEMA_VERSION = "3.0.0"
ARCHETYPE_COUNT = 8


DESTINY_SLOTS: tuple[dict[str, Any], ...] = (
    {
        "id": "emotional_baseline",
        "index": 1,
        "name": "心候",
        "axis": "情绪底色",
        "icon": "心",
        "x": 20,
        "y": 18,
    },
    {
        "id": "desire_profile",
        "index": 2,
        "name": "欲核",
        "axis": "欲望结构",
        "icon": "欲",
        "x": 42,
        "y": 11,
    },
    {
        "id": "interpretation_style",
        "index": 3,
        "name": "观法",
        "axis": "理解方式",
        "icon": "观",
        "x": 65,
        "y": 18,
    },
    {
        "id": "intimacy_style",
        "index": 4,
        "name": "亲疏",
        "axis": "亲密需求",
        "icon": "亲",
        "x": 82,
        "y": 31,
    },
    {
        "id": "relationship_positioning",
        "index": 5,
        "name": "势位",
        "axis": "关系主动性",
        "icon": "势",
        "x": 73,
        "y": 49,
    },
    {
        "id": "speaking_style",
        "index": 6,
        "name": "言声",
        "axis": "说话方式",
        "icon": "言",
        "x": 52,
        "y": 41,
    },
    {
        "id": "action_style",
        "index": 7,
        "name": "行止",
        "axis": "行动偏好",
        "icon": "行",
        "x": 32,
        "y": 50,
    },
    {
        "id": "boundary_conflict",
        "index": 8,
        "name": "界修",
        "axis": "冲突处理",
        "icon": "界",
        "x": 15,
        "y": 64,
    },
    {
        "id": "daily_rhythm",
        "index": 9,
        "name": "烟火",
        "axis": "日常节律",
        "icon": "烟",
        "x": 31,
        "y": 80,
    },
    {
        "id": "interest_style",
        "index": 10,
        "name": "兴味",
        "axis": "兴趣审美",
        "icon": "兴",
        "x": 54,
        "y": 69,
    },
    {
        "id": "adaptation_style",
        "index": 11,
        "name": "变易",
        "axis": "成长方式",
        "icon": "变",
        "x": 74,
        "y": 80,
    },
    {
        "id": "failure_repair_pattern",
        "index": 12,
        "name": "反象",
        "axis": "压力反应",
        "icon": "反",
        "x": 89,
        "y": 89,
    },
)

_SLOT_BY_ID = {str(slot["id"]): slot for slot in DESTINY_SLOTS}
_PERSON_IDS = tuple(f"p{index}" for index in range(1, ARCHETYPE_COUNT + 1))
_CARD_BATCHES = (
    ("first", DESTINY_SLOTS[:6]),
    ("second", DESTINY_SLOTS[6:]),
)
_WILLINGNESS = {"low", "neutral", "normal", "high"}
_INTERRUPTED_STAGE_STATUS = {
    "archetypes_generating": "archetypes",
    "cards_generating": "cards",
    "synthesizing": "synthesis",
    "committing": "commit",
}
_WILLINGNESS_ALIASES = {
    "low": "low",
    "降低": "low",
    "低": "low",
    "red": "low",
    "红": "low",
    "neutral": "neutral",
    "不影响": "neutral",
    "无影响": "neutral",
    "中性": "neutral",
    "green": "neutral",
    "绿": "neutral",
    "normal": "normal",
    "常规": "normal",
    "普通": "normal",
    "稳定": "normal",
    "blue": "normal",
    "蓝": "normal",
    "high": "high",
    "高": "high",
    "高亢": "high",
    "gold": "high",
    "金": "high",
}

_DEFAULT_ARCHETYPES: tuple[tuple[str, str], ...] = (
    ("温柔陪伴者", "情绪稳定，习惯先听完再回应，相处时让人放松。"),
    ("理性知己", "表达清楚，愿意一起分析问题，也会尊重不同意见。"),
    ("活力玩伴", "反应快，喜欢用轻松玩笑带动聊天，主动分享新鲜想法。"),
    ("慢热同伴", "起初克制谨慎，熟悉后会逐渐表达真实感受。"),
    ("细腻倾听者", "会留意语气和细节，回应温和，并认真接住情绪。"),
    ("独立搭档", "有自己的判断和节奏，愿意合作，但不会事事附和。"),
    ("可靠照顾者", "做事稳妥，重视承诺，常用具体行动表达关心。"),
    ("热情伙伴", "表达直接主动，乐于拉近距离，也愿意及时修复分歧。"),
)


def _empty_card_batches() -> dict[str, dict[str, Any]]:
    return {
        batch_id: {
            "status": "pending",
            "slot_ids": [str(slot["id"]) for slot in slots],
        }
        for batch_id, slots in _CARD_BATCHES
    }

_DEFAULT_SLOT_LABELS: dict[str, tuple[str, ...]] = {
    "emotional_baseline": (
        "温柔稳定",
        "冷静克制",
        "开朗直率",
        "慢热细腻",
        "敏感认真",
        "乐观松弛",
        "沉稳可靠",
        "热情外放",
    ),
    "desire_profile": (
        "重视陪伴",
        "追求默契",
        "需要回应",
        "珍惜自由",
        "偏爱亲近",
        "看重成长",
        "享受分享",
        "渴望确定",
    ),
    "interpretation_style": (
        "先听后想",
        "抓住重点",
        "关注语气",
        "直接理解",
        "耐心确认",
        "联想丰富",
        "务实判断",
        "共情优先",
    ),
    "intimacy_style": (
        "温柔靠近",
        "慢慢熟悉",
        "主动亲近",
        "保留空间",
        "重视陪伴",
        "喜欢分享",
        "需要确认",
        "自然依赖",
    ),
    "relationship_positioning": (
        "主动照顾",
        "平等商量",
        "轻松带动",
        "安静陪伴",
        "直接表达",
        "细心回应",
        "独立可靠",
        "热情投入",
    ),
    "speaking_style": (
        "温柔直白",
        "简洁清楚",
        "活泼俏皮",
        "沉稳耐心",
        "坦率认真",
        "细腻体贴",
        "幽默自然",
        "热情主动",
    ),
    "action_style": (
        "说到做到",
        "先想后做",
        "马上回应",
        "循序推进",
        "主动安排",
        "尊重选择",
        "灵活调整",
        "耐心坚持",
    ),
    "boundary_conflict": (
        "直接沟通",
        "冷静整理",
        "先听后说",
        "及时道歉",
        "明确表达",
        "给出空间",
        "主动修复",
        "共同商量",
    ),
    "daily_rhythm": (
        "作息规律",
        "随性自然",
        "精力充足",
        "安静从容",
        "喜欢计划",
        "顺势调整",
        "重视陪伴",
        "保留独处",
    ),
    "interest_style": (
        "好奇广泛",
        "偏爱经典",
        "重视质感",
        "喜欢新鲜",
        "审美简洁",
        "乐于分享",
        "专注深入",
        "轻松随性",
    ),
    "adaptation_style": (
        "愿意学习",
        "稳步调整",
        "快速尝试",
        "反思改进",
        "坚持原则",
        "接受建议",
        "共同成长",
        "保持自我",
    ),
    "failure_repair_pattern": (
        "先冷静",
        "需要安慰",
        "主动求助",
        "独自整理",
        "坦白压力",
        "暂时退后",
        "很快振作",
        "耐心恢复",
    ),
}

_DEFAULT_SLOT_BEHAVIORS: dict[str, str] = {
    "emotional_baseline": "回应时保持情绪连贯，不会突然改变语气。",
    "desire_profile": "会清楚表达自己在关系中真正重视的部分。",
    "interpretation_style": "会根据对方原话确认理解，再给出自己的判断。",
    "intimacy_style": "会用稳定的回应和具体表达调整彼此距离。",
    "relationship_positioning": "会在聊天中自然表现自己的主动程度和关系立场。",
    "speaking_style": "措辞和语气保持一致，让对方容易判断真实态度。",
    "action_style": "会把表达落实为聊天中可观察到的选择和回应。",
    "boundary_conflict": "出现分歧时会说明感受，并尝试把问题说清楚。",
    "daily_rhythm": "会按自己的日常节奏发起话题和回应消息。",
    "interest_style": "会分享真实兴趣，也愿意听对方谈喜欢的内容。",
    "adaptation_style": "遇到变化时会调整方法，但保留稳定的核心态度。",
    "failure_repair_pattern": "压力增加时会表现出明确反应，并逐步恢复交流。",
}

_DEFAULT_WILLINGNESS = ("normal", "neutral", "high", "normal", "low", "normal", "neutral", "high")

_OPPOSITE_ROLE_TERMS = {
    "女": ("男性", "男人", "男友", "丈夫", "哥哥", "弟弟", "叔叔", "少年", "公子"),
    "男": ("女性", "女人", "女友", "妻子", "姐姐", "妹妹", "阿姨", "少女", "姑娘"),
    "不指定": (
        "男性",
        "女性",
        "男人",
        "女人",
        "男友",
        "女友",
        "丈夫",
        "妻子",
        "哥哥",
        "姐姐",
        "弟弟",
        "妹妹",
    ),
}

_OPPOSITE_BODY_TERMS = {
    "女": ("阴茎", "阳具", "龟头", "睾丸", "勃起", "射精", "精液", "肉棒"),
    "男": ("阴道", "阴蒂", "子宫", "宫颈", "月经", "怀孕", "乳汁"),
    "不指定": (
        "阴茎",
        "阳具",
        "龟头",
        "睾丸",
        "勃起",
        "射精",
        "阴道",
        "阴蒂",
        "子宫",
        "宫颈",
        "月经",
        "怀孕",
    ),
}


def _gender_instruction(gender: Any) -> str:
    normalized = _clean(gender, 16) or "不指定"
    if normalized == "女":
        return (
            "角色固定为女性。角色标签、描述和角色自身的身体反应只能使用女性设定，"
            "禁止男性身份、男性器官、勃起或射精等男性反应。"
        )
    if normalized == "男":
        return (
            "角色固定为男性。角色标签、描述和角色自身的身体反应只能使用男性设定，"
            "禁止女性身份、女性器官、月经或怀孕等女性反应。"
        )
    return "角色性别不指定。不要自行补充男性或女性专属称谓、器官或生理反应。"


def _assert_gender_consistency(text: Any, gender: Any, *, strict_role_terms: bool) -> None:
    normalized = _clean(gender, 16) or "不指定"
    content = str(text or "")
    forbidden = list(_OPPOSITE_BODY_TERMS.get(normalized, ()))
    if strict_role_terms:
        forbidden.extend(_OPPOSITE_ROLE_TERMS.get(normalized, ()))
    if normalized == "女" and re.search(r"(?:我|她|自己|角色).{0,8}硬(?:的|得|了|着|起来)", content):
        raise ValueError("女性角色内容出现男性生理反应")
    conflicts = sorted({term for term in forbidden if term in content})
    if conflicts:
        raise ValueError(f"角色性别为 {normalized}，生成内容出现冲突词：{'、'.join(conflicts[:4])}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型返回中没有完整 JSON 对象")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型返回必须是 JSON 对象")
    return value


def _repair_json_shell(text: str) -> str:
    """Repair harmless transport syntax without inventing any card content."""

    normalized: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
                normalized.append(char)
                continue
            if char == "\\":
                escaped = True
                normalized.append(char)
                continue
            if char == '"':
                in_string = False
                normalized.append(char)
                continue
            normalized.append(" " if char in "\r\n\t" else char)
            continue
        if char == '"':
            in_string = True
            normalized.append(char)
        else:
            normalized.append({"，": ",", "：": ":"}.get(char, char))

    source = "".join(normalized)
    repaired: list[str] = []
    in_string = False
    escaped = False
    for index, char in enumerate(source):
        if in_string:
            repaired.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            repaired.append(char)
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(source) and source[lookahead].isspace():
                lookahead += 1
            if lookahead < len(source) and source[lookahead] in "]}":
                continue
        repaired.append(char)
    return "".join(repaired)


def _card_rows_from_value(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    rows = value.get("cards")
    if isinstance(rows, list):
        return rows
    data = value.get("data")
    if isinstance(data, dict) and isinstance(data.get("cards"), list):
        return data["cards"]
    if isinstance(data, list):
        return data
    return None


def _recover_card_rows(text: str, expected_count: int, slots_per_person: int) -> list[Any] | None:
    """Recover independently valid rows when only the surrounding JSON is broken."""

    decoder = json.JSONDecoder()
    explicit_rows: list[Any] = []
    compact_cells: list[list[Any]] = []
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and len(value) == 5:
            explicit_rows.append(value)
        elif isinstance(value, dict) and any(
            key in value for key in ("source_id", "person_id", "person", "人物ID", "slot_id", "category_id", "分类ID")
        ):
            explicit_rows.append(value)
        elif isinstance(value, list) and len(value) == 3:
            compact_cells.append(value)
    if len(explicit_rows) == expected_count:
        return explicit_rows
    if len(compact_cells) == expected_count:
        return [
            compact_cells[index : index + slots_per_person]
            for index in range(0, expected_count, slots_per_person)
        ]

    delimited: list[list[str]] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", raw_line.strip())
        separator = "\t" if line.count("\t") >= 4 else "|" if line.count("|") >= 4 else ""
        if not separator:
            continue
        parts = [part.strip().strip('"').strip("'") for part in line.split(separator)]
        if len(parts) == 5:
            delimited.append(parts)
    return delimited if len(delimited) == expected_count else None


def _load_card_rows(raw: str, expected_count: int, slots_per_person: int) -> list[Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    candidates = [text]
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start >= 0 and end >= start:
            candidates.append(text[start : end + 1])

    first_json_error: json.JSONDecodeError | None = None
    seen: set[str] = set()
    for candidate in candidates:
        for variant in (candidate, _repair_json_shell(candidate)):
            if not variant or variant in seen:
                continue
            seen.add(variant)
            try:
                rows = _card_rows_from_value(json.loads(variant))
            except json.JSONDecodeError as exc:
                first_json_error = first_json_error or exc
            else:
                if rows is not None:
                    return rows
            try:
                rows = _card_rows_from_value(ast.literal_eval(variant))
            except (SyntaxError, ValueError):
                continue
            if rows is not None:
                return rows

    recovered = _recover_card_rows(_repair_json_shell(text), expected_count, slots_per_person)
    if recovered is not None:
        return recovered
    if first_json_error is not None:
        raise first_json_error
    raise ValueError("模型返回中没有可区分的命签数据")


class DestinySeed(BaseModel):
    ai_name: str = Field(min_length=1, max_length=80)
    ai_gender: Literal["女", "男", "不指定"] = "不指定"
    user_name: str = Field(min_length=1, max_length=80)
    user_alias: str = Field(default="", max_length=80)
    relationship: str = Field(min_length=1, max_length=100)
    relationship_context: str = Field(default="", max_length=2400)
    character_expectation: str = Field(min_length=1, max_length=2400)
    appearance_expectation: str = Field(default="", max_length=1200)
    adult_character: bool = True
    avatar: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_seed(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data.setdefault("ai_name", data.get("name"))
        data.setdefault("ai_gender", data.get("gender", "不指定"))
        data.setdefault("character_expectation", data.get("expectation"))
        data.setdefault("appearance_expectation", data.get("appearance", ""))
        data.setdefault("user_name", "用户")
        data.setdefault("relationship", "陪伴者")
        return data

    @field_validator(
        "ai_name",
        "user_name",
        "user_alias",
        "relationship",
        "relationship_context",
        "character_expectation",
        "appearance_expectation",
        mode="before",
    )
    @classmethod
    def trim_text(cls, value: Any) -> str:
        return _clean(value, 2400)

    @field_validator("avatar", mode="before")
    @classmethod
    def keep_only_persistent_avatar(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        avatar = deepcopy(value)
        src = str(avatar.get("src") or "").strip()
        if src and not src.startswith(("/api/v1/avatar/files/", "/assets/")):
            return {}
        avatar["src"] = src
        return avatar


class DestinySelectionRequest(BaseModel):
    card_id: str = Field(min_length=1, max_length=160)
    expected_revision: int | None = Field(default=None, ge=1)


def public_destiny_definition() -> dict[str, Any]:
    return {
        "schema_version": DESTINY_SCHEMA_VERSION,
        "journey_schema_version": JOURNEY_SCHEMA_VERSION,
        "archetype_count": ARCHETYPE_COUNT,
        "slots": deepcopy(list(DESTINY_SLOTS)),
        "interaction_willingness": {
            "low": {"label": "互动意愿降低", "meaning": "该特征会降低主动聊天和回应的意愿。"},
            "neutral": {"label": "不影响互动意愿", "meaning": "该特征存在，但不直接改变互动意愿。"},
            "normal": {"label": "常规互动意愿", "meaning": "该特征对应稳定、普通的互动倾向。"},
            "high": {
                "label": "互动意愿高亢",
                "meaning": "该特征会让主动聊天和回应意愿变强，不代表好或坏。",
            },
        },
    }


class DestinyService:
    """Persisted V7 journey with three visible generation stages."""

    def __init__(
        self,
        database: ProductDatabase,
        *,
        characters: Any,
        profiles: Any,
        llm: Any,
        settings: Any,
    ) -> None:
        self.database = database
        self.characters = characters
        self.profiles = profiles
        self.llm = llm
        self.settings = settings

    @staticmethod
    def _key(journey_id: str) -> str:
        return f"destiny-journey:{journey_id}"

    def create(self, seed: DestinySeed) -> dict[str, Any]:
        journey_id = str(uuid4())
        now = _now()
        value = {
            "journey_id": journey_id,
            "schema_version": JOURNEY_SCHEMA_VERSION,
            "revision": 1,
            "status": "seed_ready",
            "seed": seed.model_dump(mode="json"),
            "archetypes": [],
            "cards_by_slot": {},
            "card_batches": _empty_card_batches(),
            "selections": {},
            "final_card": None,
            "character_id": "",
            "model_calls": {"archetypes": 0, "cards": 0, "synthesis": 0},
            "fallbacks": {"archetypes": False, "cards": False},
            "operation_id": "",
            "stage_started_at": "",
            "errors": [],
            "progress": {
                "stage": "seed",
                "current": 0,
                "total": 12,
                "percent": 0,
                "message": "角色种子已建立",
            },
            "created_at": now,
            "updated_at": now,
        }
        self.database.put_document(self._key(journey_id), value)
        return deepcopy(value)

    @staticmethod
    def _require_revision(journey: dict[str, Any], expected_revision: int | None) -> None:
        if expected_revision is not None and expected_revision != journey.get("revision"):
            raise ValueError("stale destiny journey revision")

    def recover_interrupted_journeys(self) -> int:
        """Make a prior Core process's in-flight work explicitly retryable at startup."""

        recovered = 0
        for _key, journey in self.database.list_documents("destiny-journey:"):
            if not isinstance(journey, dict):
                continue
            stage = _INTERRUPTED_STAGE_STATUS.get(str(journey.get("status") or ""))
            if not stage:
                continue
            errors = list(journey.get("errors") or [])
            errors.append(
                {
                    "stage": stage,
                    "message": "Core 在本阶段完成前中断，请明确点击重试。",
                    "created_at": _now(),
                }
            )
            extra_updates: dict[str, Any] = {}
            progress_current, progress_total = 0, 1
            if stage == "cards":
                batches = self._card_batch_states(journey)
                for batch in batches.values():
                    if batch.get("status") == "generating":
                        batch["status"] = "failed"
                extra_updates["card_batches"] = batches
                progress_current = self._complete_card_count(journey)
                progress_total = 96
            try:
                self._save(
                    journey,
                    status=f"{stage}_failed" if stage != "commit" else "review_ready",
                    operation_id="",
                    stage_started_at="",
                    errors=errors[-20:],
                    **extra_updates,
                    progress={
                        "stage": stage,
                        "current": progress_current,
                        "total": progress_total,
                        "percent": round(progress_current / progress_total * 100),
                        "message": "Core 中断，本阶段可手动重试",
                    },
                )
                recovered += 1
            except ValueError as exc:
                if "stale destiny journey revision" not in str(exc):
                    raise
        return recovered

    def get(self, journey_id: str) -> dict[str, Any]:
        value = self.database.get_document(self._key(journey_id))
        if not isinstance(value, dict):
            raise KeyError("destiny journey not found")
        result = deepcopy(value)
        if value.get("schema_version") != JOURNEY_SCHEMA_VERSION:
            result["read_state"] = {
                "state": "legacy_incomplete",
                "can_continue": False,
                "action": "restart",
                "message": "旧版未完成旅程不能继续，请重新开始创建角色",
            }
            return result
        if self._cards_are_invalid(value):
            result["read_state"] = {
                "state": "cards_invalid",
                "can_continue": False,
                "action": "regenerate_cards",
                "message": "现有命签的直接标签无效，请点击继续生成命签",
            }
            return result
        result["read_state"] = {
            "state": "ready",
            "can_continue": True,
            "action": "",
            "message": "旅程数据可继续使用",
        }
        return result

    def _get_mutable(self, journey_id: str) -> dict[str, Any]:
        value = self.database.get_document(self._key(journey_id))
        if not isinstance(value, dict):
            raise KeyError("destiny journey not found")
        if value.get("schema_version") != JOURNEY_SCHEMA_VERSION:
            raise ValueError("旧版未完成旅程不能继续，请重新开始创建角色")
        return deepcopy(value)

    def _cards_are_invalid(self, value: dict[str, Any]) -> bool:
        return value.get("status") in {
            "cards_ready",
            "selections_ready",
            "review_ready",
            "synthesis_failed",
        } and self._stored_cards_need_regeneration(value)

    def _save(self, journey: dict[str, Any], **updates: Any) -> dict[str, Any]:
        value = deepcopy(journey)
        value.update({key: deepcopy(item) for key, item in updates.items()})
        expected_revision = int(journey.get("revision", 0))
        value["revision"] = expected_revision + 1
        value["updated_at"] = _now()
        if not self.database.compare_and_swap_document(
            self._key(str(value["journey_id"])),
            expected_revision=expected_revision,
            value=value,
        ):
            raise ValueError("stale destiny journey revision")
        return deepcopy(value)

    def _fail(self, journey: dict[str, Any], stage: str, error: Exception) -> None:
        errors = list(journey.get("errors") or [])
        errors.append({"stage": stage, "message": _clean(error, 1000), "created_at": _now()})
        try:
            self._save(
                journey,
                status=f"{stage}_failed",
                operation_id="",
                stage_started_at="",
                errors=errors[-20:],
                progress={
                    "stage": stage,
                    "current": 0,
                    "total": 1,
                    "percent": 0,
                    "message": f"{stage} 失败，请明确点击重试",
                },
            )
        except ValueError as conflict:
            if "stale destiny journey revision" not in str(conflict):
                raise

    async def _generate(
        self,
        messages: list[dict[str, str]],
        *,
        request_kind: str,
        max_tokens: int,
        timeout_seconds: float,
        temperature: float,
    ) -> str:
        if self.settings.llm_mode != "openai" or not str(self.settings.llm_api_key or "").strip():
            raise ValueError("命格系统需要已配置且可用的模型 API")
        config = ApiConfig(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            model=self.settings.llm_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        structured = getattr(self.llm, "generate_structured", None)
        if callable(structured):
            call = asyncio.to_thread(
                structured,
                messages,
                config,
                request_kind=request_kind,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
        else:
            call = asyncio.to_thread(self.llm.generate, messages, config)
        return await asyncio.wait_for(call, timeout=timeout_seconds + 5)

    @staticmethod
    def _archetype_messages(seed: dict[str, Any]) -> list[dict[str, str]]:
        gender = _clean(seed.get("ai_gender"), 16) or "不指定"
        examples = (
            "邻家姐姐、年上御姐、淘气妹妹"
            if gender == "女"
            else "邻家哥哥、成熟前辈、淘气弟弟"
            if gender == "男"
            else "温柔陪伴者、理性知己、活力玩伴"
        )
        return [
            {
                "role": "system",
                "content": '你是聊天角色创作者。只返回 JSON：{"people":[{"id":"p1","label":"","summary":""}]}',
            },
            {
                "role": "user",
                "content": "\n".join(
                    (
                        "根据下面的输入，创作 8 位风格明显不同的聊天对象。",
                        f"label 使用“{examples}”这类直接标签，不另起人物姓名。",
                        "summary 用 1 到 2 句话概括性格、说话方式和相处感觉。",
                        _gender_instruction(gender),
                        "不要编造职业、创伤、前任或共同经历。",
                        "",
                        "输入：",
                        json.dumps(seed, ensure_ascii=False),
                    )
                ),
            },
        ]

    @staticmethod
    def _cards_messages(
        people: list[dict[str, Any]],
        gender: str,
        target_slots: tuple[dict[str, Any], ...] | None = None,
    ) -> list[dict[str, str]]:
        selected_slots = target_slots or DESTINY_SLOTS
        slots = [{"id": slot["id"], "name": slot["axis"]} for slot in selected_slots]
        card_count = ARCHETYPE_COUNT * len(selected_slots)
        return [
            {
                "role": "system",
                "content": "\n".join(
                    (
                        '你是聊天角色拆解器。只返回 JSON：{"cards":[[["慢热敏感","会先观察语气再回应","normal"]]]}',
                        "cards 外层按 8 位人物排序，每个人内层按给定分类排序。",
                        "每格依次是：[直接标签, 聊天中可见的表现, 互动意愿]。",
                        "互动意愿只能是 low、neutral、normal、high。",
                    )
                ),
            },
            {
                "role": "user",
                "content": "\n".join(
                    (
                        f"把下面 8 位聊天对象按顺序分别拆成 {len(selected_slots)} 个分类，共 {card_count} 张。",
                        "每个人、每个分类各一张。直接标签写 2 到 8 个字的人物特征，"
                        "例如“慢热敏感、温柔体贴、直率好奇”。",
                        "直接标签不能重复分类名、分类 ID 或人物方向标签，不要诗化别名。",
                        "表现只写用户在聊天中能观察到的行为和语气；每项只用一句短句，不超过 32 个汉字。",
                        "互动意愿表示该特征会让角色主动聊天和回应的意愿降低、不受影响、保持常规或变得高亢。",
                        _gender_instruction(gender),
                        "",
                        "人物：",
                        json.dumps(people, ensure_ascii=False, separators=(",", ":")),
                        "",
                        "分类（严格按此顺序）：",
                        json.dumps(slots, ensure_ascii=False, separators=(",", ":")),
                    )
                ),
            },
        ]

    @staticmethod
    def _synthesis_messages(journey: dict[str, Any]) -> list[dict[str, str]]:
        selected = [journey["selections"][str(slot["id"])] for slot in DESTINY_SLOTS]
        return [
            {
                "role": "system",
                "content": (
                    "你是聊天角色卡编写器。只返回 JSON："
                    '{"description":"","personality":"","scenario":"","first_mes":"",'
                    '"alternate_greetings":[],"mes_example":"","appearance":{'
                    '"height_cm":null,"body_shape":"","body_features":"","face":"",'
                    '"hair":"","eyes":"","skin":"","distinguishing_features":[],'
                    '"signature_outfit":"","intimate_features":""}}'
                ),
            },
            {
                "role": "user",
                "content": "\n".join(
                    (
                        "把用户选择的 12 项特征写成同一个连续、自然的聊天角色。",
                        "description 只写基础信息和必要背景，简短具体。",
                        "appearance 写稳定、可用于日常与亲密场景的具体外表；优先服从外表期待，未指定部分自然补全。",
                        "description 不重复 appearance，后端会自动加入可移植的外表摘要。",
                        "intimate_features 只在成年角色且外表期待明确涉及成人身体特征时填写，否则留空。",
                        "personality 写可观察的性格与相处反应，不堆形容词。",
                        "scenario 只写角色与用户的长期关系和常见互动背景，"
                        "不写具体日期、早晚、刚醒、当前地点或正在发生的动作。",
                        "first_mes 与 alternate_greetings 先回应对方，再自然延续；每条简短。",
                        "mes_example 写 1 到 2 轮短对话，体现称呼、关系和回应方式。",
                        "动作、旁白和镜头描写按角色与情境自然出现，不限制表达形式。",
                        "不得编造过去、时间、物品、职业、创伤、前任或共同经历。",
                        "不要写系统规则、禁令、边界条款或关系契约。",
                        "保持日常、具体，不把特征夸大成病态依赖、绝对服从、人格切换或神秘契约。",
                        "只有角色期待明确要求成人亲密时才可写成人内容；adult_character 只表示是否允许，不代表必须写。",
                        "若角色期待明确要求成人亲密，只写符合角色口吻的直接表达偏好；不写全局词表、强制升级或每轮推进规则。",
                        _gender_instruction(journey["seed"].get("ai_gender")),
                        "",
                        "角色种子：",
                        json.dumps(journey["seed"], ensure_ascii=False),
                        "",
                        "用户选择：",
                        json.dumps(selected, ensure_ascii=False),
                    )
                ),
            },
        ]

    @staticmethod
    def _normalize_archetypes(raw: str, gender: str) -> list[dict[str, Any]]:
        rows = _json_object(raw).get("people")
        if not isinstance(rows, list) or len(rows) != ARCHETYPE_COUNT:
            raise ValueError("模型必须完整返回 8 个角色方向")
        result: list[dict[str, Any]] = []
        labels: set[str] = set()
        ids: set[str] = set()
        for expected_id, row in zip(_PERSON_IDS, rows, strict=True):
            if not isinstance(row, dict):
                raise ValueError("角色方向格式无效")
            source_id = _clean(row.get("id"), 16)
            label = _clean(row.get("label"), 80)
            summary = _clean(row.get("summary"), 500)
            _assert_gender_consistency(f"{label}\n{summary}", gender, strict_role_terms=True)
            if source_id != expected_id or not label or not summary:
                raise ValueError("角色方向必须包含连续的 p1 至 p8、标签和概括")
            key = label.casefold()
            if source_id in ids or key in labels:
                raise ValueError("8 个角色方向的 ID 或标签重复")
            ids.add(source_id)
            labels.add(key)
            result.append({"id": source_id, "label": label, "summary": summary})
        return result

    @staticmethod
    def _normalize_willingness(value: Any) -> str:
        key = _clean(value, 40).casefold().replace(" ", "")
        normalized = _WILLINGNESS_ALIASES.get(key, key)
        if normalized not in _WILLINGNESS:
            raise ValueError("互动意愿只能是 low、neutral、normal 或 high")
        return normalized

    @staticmethod
    def _is_reserved_card_label(label: Any, slot_id: Any, slot: dict[str, Any], source_label: Any) -> bool:
        normalized = _clean(label, 100).casefold()
        return normalized in {
            _clean(slot_id, 100).casefold(),
            _clean(slot.get("axis"), 100).casefold(),
            _clean(slot.get("name"), 100).casefold(),
            _clean(source_label, 100).casefold(),
        }

    @classmethod
    def _label_from_summary(cls, summary: Any, slot_id: Any, slot: dict[str, Any], source_label: Any) -> str:
        first_phrase = re.split(r"[，,、；;。.!！?？\n]", str(summary or ""), maxsplit=1)[0]
        candidate = _clean(first_phrase, 100)
        if not 2 <= len(candidate) <= 8:
            return ""
        if cls._is_reserved_card_label(candidate, slot_id, slot, source_label):
            return ""
        if _clean(source_label, 100) and _clean(source_label, 100) in candidate:
            return ""
        if any(marker in candidate for marker in ("分类", "方向", "人物", "聊天", "用户", "表现", "角色", "说话")):
            return ""
        return candidate

    @classmethod
    def _stored_card_slots_valid(
        cls,
        journey: dict[str, Any],
        target_slots: tuple[dict[str, Any], ...],
    ) -> bool:
        people = {
            str(person.get("id")): str(person.get("label") or "")
            for person in journey.get("archetypes") or []
            if isinstance(person, dict)
        }
        cards_by_slot = journey.get("cards_by_slot")
        if len(people) != ARCHETYPE_COUNT or not isinstance(cards_by_slot, dict):
            return False
        for slot in target_slots:
            slot_id = str(slot["id"])
            cards = cards_by_slot.get(slot_id)
            if not isinstance(cards, list) or len(cards) != ARCHETYPE_COUNT:
                return False
            seen_sources: set[str] = set()
            for card in cards:
                if not isinstance(card, dict):
                    return False
                source_id = _clean(card.get("source_id"), 16)
                if source_id not in people or _clean(card.get("slot_id"), 80) != slot_id:
                    return False
                if source_id in seen_sources:
                    return False
                seen_sources.add(source_id)
                if not _clean(card.get("label"), 100) or not _clean(card.get("summary"), 120):
                    return False
                if _clean(card.get("interaction_willingness"), 40) not in _WILLINGNESS:
                    return False
                if cls._is_reserved_card_label(card.get("label"), slot_id, slot, people[source_id]):
                    return False
            if seen_sources != set(_PERSON_IDS):
                return False
        return True

    @classmethod
    def _stored_cards_need_regeneration(cls, journey: dict[str, Any]) -> bool:
        return not cls._stored_card_slots_valid(journey, DESTINY_SLOTS)

    @classmethod
    def _card_batch_states(cls, journey: dict[str, Any]) -> dict[str, dict[str, Any]]:
        stored = journey.get("card_batches")
        states = _empty_card_batches()
        for batch_id, slots in _CARD_BATCHES:
            if cls._stored_card_slots_valid(journey, slots):
                states[batch_id]["status"] = "ready"
                continue
            saved = stored.get(batch_id) if isinstance(stored, dict) else None
            saved_status = saved.get("status") if isinstance(saved, dict) else None
            if saved_status in {"failed", "generating"}:
                states[batch_id]["status"] = saved_status
        return states

    @classmethod
    def _preserved_card_batches(cls, journey: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        source = journey.get("cards_by_slot")
        preserved: dict[str, list[dict[str, Any]]] = {}
        if not isinstance(source, dict):
            return preserved
        for _batch_id, slots in _CARD_BATCHES:
            if cls._stored_card_slots_valid(journey, slots):
                for slot in slots:
                    slot_id = str(slot["id"])
                    preserved[slot_id] = deepcopy(source[slot_id])
        return preserved

    @classmethod
    def _complete_card_count(cls, journey: dict[str, Any]) -> int:
        return sum(
            ARCHETYPE_COUNT * len(slots)
            for _batch_id, slots in _CARD_BATCHES
            if cls._stored_card_slots_valid(journey, slots)
        )

    @classmethod
    def _normalize_cards(
        cls,
        raw: str,
        people: list[dict[str, Any]],
        gender: str,
        target_slots: tuple[dict[str, Any], ...] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        selected_slots = target_slots or DESTINY_SLOTS
        expected_count = ARCHETYPE_COUNT * len(selected_slots)
        try:
            rows = _load_card_rows(raw, expected_count, len(selected_slots))
        except (json.JSONDecodeError, ValueError) as exc:
            text = str(raw or "").strip()
            if text and not text.endswith("}"):
                raise ValueError("模型输出在 JSON 完成前中断；请点击继续生成命签") from exc
            if isinstance(exc, json.JSONDecodeError):
                raise ValueError(
                    f"模型返回的命签 JSON 格式无效（第 {exc.pos + 1} 字符附近）；请点击继续生成命签"
                ) from exc
            raise ValueError("模型返回的命签 JSON 格式无效；请点击继续生成命签") from exc
        if not isinstance(rows, list):
            raise ValueError("模型返回中缺少 cards 数组")

        # IDs are deterministic, so the preferred protocol does not repeat
        # person and slot IDs. Flat ordered rows and former five-column rows
        # remain accepted for compatible providers.
        if len(rows) == ARCHETYPE_COUNT and all(isinstance(group, list) for group in rows):
            matrix_shape = all(len(group) == len(selected_slots) for group in rows)
            if matrix_shape:
                expanded: list[list[Any]] = []
                for person_index, group in enumerate(rows, start=1):
                    for slot, cell in zip(selected_slots, group, strict=True):
                        if not isinstance(cell, list) or len(cell) != 3:
                            raise ValueError("命签矩阵中的每格必须包含标签、表现和互动意愿")
                        expanded.append([f"p{person_index}", slot["id"], *cell])
                rows = expanded

        if len(rows) == expected_count and all(isinstance(cell, list) and len(cell) == 3 for cell in rows):
            rows = [
                [
                    f"p{index // len(selected_slots) + 1}",
                    selected_slots[index % len(selected_slots)]["id"],
                    *cell,
                ]
                for index, cell in enumerate(rows)
            ]
        if len(rows) != expected_count:
            missing = max(0, expected_count - len(rows))
            raise ValueError(f"模型返回 {len(rows)} 张命签，要求 {expected_count} 张，缺少 {missing} 张")
        labels = {str(person["id"]): str(person["label"]) for person in people}
        expected = {(person_id, str(slot["id"])) for person_id in _PERSON_IDS for slot in selected_slots}
        seen: set[tuple[str, str]] = set()
        cards_by_slot: dict[str, list[dict[str, Any]]] = {str(slot["id"]): [] for slot in selected_slots}
        ordered_pairs = [
            (person_id, str(slot["id"]))
            for person_id in _PERSON_IDS
            for slot in selected_slots
        ]
        for row_index, row in enumerate(rows):
            if isinstance(row, list) and len(row) == 5:
                source_id, slot_id, label, summary, willingness = row
            elif isinstance(row, list) and len(row) == 4:
                slot_id, label, summary, willingness = row
                source_id = ordered_pairs[row_index][0]
            elif isinstance(row, dict):
                default_source, default_slot = ordered_pairs[row_index]
                source_id = (
                    row.get("source_id")
                    or row.get("person_id")
                    or row.get("person")
                    or row.get("人物ID")
                    or default_source
                )
                slot_id = (
                    row.get("slot_id")
                    or row.get("category_id")
                    or row.get("category")
                    or row.get("分类ID")
                    or default_slot
                )
                label = row.get("label") or row.get("feature") or row.get("tag") or row.get("标签")
                summary = row.get("summary") or row.get("behavior") or row.get("表现")
                willingness = row.get("interaction_willingness") or row.get("willingness") or row.get("互动意愿")
            else:
                raise ValueError("命签格式无法区分标签、表现和互动意愿")
            source_id, slot_id = _clean(source_id, 16), _clean(slot_id, 80)
            label, summary = _clean(label, 100), _clean(summary, 120)
            _assert_gender_consistency(f"{label}\n{summary}", gender, strict_role_terms=True)
            if not label or not summary or (source_id, slot_id) not in expected:
                raise ValueError("命签的人物、分类、标签或表现无效")
            slot = _SLOT_BY_ID[slot_id]
            if cls._is_reserved_card_label(label, slot_id, slot, labels[source_id]):
                label = cls._label_from_summary(summary, slot_id, slot, labels[source_id])
                if not label:
                    raise ValueError("命签直接标签不能重复分类名、分类 ID 或人物方向标签")
            pair = (source_id, slot_id)
            if pair in seen:
                raise ValueError(f"{expected_count} 张命签存在重复的人物与分类组合")
            seen.add(pair)
            cards_by_slot[slot_id].append(
                {
                    "card_id": f"{source_id}:{slot_id}",
                    "source_id": source_id,
                    "source_label": labels[source_id],
                    "slot_id": slot_id,
                    "slot_name": slot["axis"],
                    "label": label,
                    "summary": summary,
                    "interaction_willingness": cls._normalize_willingness(willingness),
                }
            )
        missing = expected - seen
        if missing:
            raise ValueError(f"模型缺少 {len(missing)} 张命签")
        return cards_by_slot

    async def generate_archetypes(self, journey_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        self._require_revision(journey, expected_revision)
        if journey.get("status") not in {"seed_ready", "archetypes_failed"}:
            raise ValueError("当前旅程不能重新生成角色方向")
        calls = deepcopy(journey.get("model_calls") or {})
        calls["archetypes"] = int(calls.get("archetypes", 0)) + 1
        journey = self._save(
            journey,
            status="archetypes_generating",
            archetypes=[],
            cards_by_slot={},
            card_batches=_empty_card_batches(),
            selections={},
            final_card=None,
            model_calls=calls,
            operation_id=str(uuid4()),
            stage_started_at=_now(),
            progress={
                "stage": "archetypes",
                "current": 0,
                "total": 8,
                "percent": 0,
                "message": "正在创作 8 个角色方向",
            },
        )
        try:
            archetypes = self._normalize_archetypes(
                await self._generate(
                    self._archetype_messages(journey["seed"]),
                    request_kind="destiny_archetypes",
                    max_tokens=1200,
                    timeout_seconds=120,
                    temperature=0.8,
                ),
                str(journey["seed"].get("ai_gender") or "不指定"),
            )
            return self._save(
                journey,
                status="archetypes_ready",
                archetypes=archetypes,
                operation_id="",
                stage_started_at="",
                progress={
                    "stage": "archetypes",
                    "current": 8,
                    "total": 8,
                    "percent": 100,
                    "message": "8 个角色方向已完成",
                },
            )
        except Exception as exc:
            self._fail(journey, "archetypes", exc)
            raise

    async def generate_cards(self, journey_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        self._require_revision(journey, expected_revision)
        invalid_stored_cards = self._cards_are_invalid(journey)
        can_retry = (journey.get("status") == "cards_failed" or invalid_stored_cards) and journey.get("archetypes")
        if journey.get("status") != "archetypes_ready" and not can_retry:
            raise ValueError("请先完成 8 个角色方向")
        people = list(journey.get("archetypes") or [])
        if len(people) != ARCHETYPE_COUNT:
            raise ValueError("角色方向不完整，不能拆分命签")
        cards_by_slot = {} if invalid_stored_cards else self._preserved_card_batches(journey)
        batch_states = _empty_card_batches() if invalid_stored_cards else self._card_batch_states(journey)
        pending_batches = [
            (batch_id, slots)
            for batch_id, slots in _CARD_BATCHES
            if batch_states[batch_id]["status"] != "ready"
        ]
        if not pending_batches:
            return self._save(
                journey,
                status="cards_ready",
                card_batches=batch_states,
                operation_id="",
                stage_started_at="",
            )
        operation_id = str(uuid4())
        journey = self._save(
            journey,
            status="cards_generating",
            cards_by_slot=cards_by_slot,
            card_batches=batch_states,
            selections={},
            final_card=None,
            operation_id=operation_id,
            stage_started_at=_now(),
            progress={
                "stage": "cards",
                "current": len(cards_by_slot) * ARCHETYPE_COUNT,
                "total": 96,
                "percent": round(len(cards_by_slot) * ARCHETYPE_COUNT / 96 * 100),
                "message": "正在拆分 96 张命签",
            },
        )
        failures: list[tuple[str, Exception]] = []
        gender = str(journey["seed"].get("ai_gender") or "不指定")
        for batch_id, slots in pending_batches:
            calls = deepcopy(journey.get("model_calls") or {})
            calls["cards"] = int(calls.get("cards", 0)) + 1
            batch_states[batch_id]["status"] = "generating"
            journey = self._save(
                journey,
                status="cards_generating",
                card_batches=batch_states,
                model_calls=calls,
                operation_id=operation_id,
            )
            try:
                generated = self._normalize_cards(
                    await self._generate(
                        self._cards_messages(people, gender, slots),
                        request_kind="destiny_cards",
                        max_tokens=8192,
                        timeout_seconds=180,
                        temperature=0.55,
                    ),
                    people,
                    gender,
                    slots,
                )
            except Exception as exc:  # noqa: BLE001 - the other half must still be attempted
                batch_states[batch_id]["status"] = "failed"
                errors = list(journey.get("errors") or [])
                batch_label = "前 6 类" if batch_id == "first" else "后 6 类"
                errors.append(
                    {
                        "stage": "cards",
                        "batch": batch_id,
                        "message": f"{batch_label}命签生成失败：{_clean(exc, 900)}",
                        "created_at": _now(),
                    }
                )
                journey = self._save(
                    journey,
                    card_batches=batch_states,
                    cards_by_slot=cards_by_slot,
                    errors=errors[-20:],
                )
                failures.append((batch_label, exc))
                continue

            cards_by_slot.update(generated)
            batch_states[batch_id]["status"] = "ready"
            completed = len(cards_by_slot) * ARCHETYPE_COUNT
            journey = self._save(
                journey,
                cards_by_slot=cards_by_slot,
                card_batches=batch_states,
                progress={
                    "stage": "cards",
                    "current": completed,
                    "total": 96,
                    "percent": round(completed / 96 * 100),
                    "message": "正在拆分 96 张命签" if completed < 96 else "96 张命签已完成",
                },
            )

        if all(self._stored_card_slots_valid(journey, slots) for _batch_id, slots in _CARD_BATCHES):
            return self._save(
                journey,
                status="cards_ready",
                cards_by_slot=cards_by_slot,
                card_batches=batch_states,
                operation_id="",
                stage_started_at="",
                progress={
                    "stage": "cards",
                    "current": 96,
                    "total": 96,
                    "percent": 100,
                    "message": "96 张命签已完成",
                },
            )

        completed = len(cards_by_slot) * ARCHETYPE_COUNT
        journey = self._save(
            journey,
            status="cards_failed",
            cards_by_slot=cards_by_slot,
            card_batches=batch_states,
            operation_id="",
            stage_started_at="",
            progress={
                "stage": "cards",
                "current": completed,
                "total": 96,
                "percent": round(completed / 96 * 100),
                "message": "部分命签生成失败，请点击继续生成命签",
            },
        )
        batch_label, first_error = failures[0]
        message = f"{batch_label}命签未通过，已保留另一批结果；请点击继续生成命签：{first_error}"
        if isinstance(first_error, TimeoutError):
            raise TimeoutError(message) from first_error
        if isinstance(first_error, ValueError):
            raise ValueError(message) from first_error
        raise RuntimeError(message) from first_error

    def select(self, journey_id: str, slot_id: str, payload: DestinySelectionRequest) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        if slot_id not in _SLOT_BY_ID:
            raise KeyError("unknown destiny slot")
        if journey.get("status") not in {
            "cards_ready",
            "selections_ready",
            "review_ready",
            "synthesis_failed",
        }:
            raise ValueError("命签尚未准备完成")
        self._require_revision(journey, payload.expected_revision)
        choices = list((journey.get("cards_by_slot") or {}).get(slot_id) or [])
        selected = next((card for card in choices if card.get("card_id") == payload.card_id), None)
        if not isinstance(selected, dict):
            raise ValueError("该命签不属于当前分类")
        selections = deepcopy(journey.get("selections") or {})
        selections[slot_id] = {**deepcopy(selected), "selected_at": _now()}
        complete = len(selections) == len(DESTINY_SLOTS)
        return self._save(
            journey,
            selections=selections,
            final_card=None,
            status="selections_ready" if complete else "cards_ready",
            progress={
                "stage": "selection",
                "current": len(selections),
                "total": len(DESTINY_SLOTS),
                "percent": round(len(selections) / len(DESTINY_SLOTS) * 100),
                "message": f"已定 {len(selections)}/12",
            },
        )

    def use_default_archetypes(self, journey_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        self._require_revision(journey, expected_revision)
        if journey.get("status") not in {"seed_ready", "archetypes_failed"}:
            raise ValueError("当前旅程不能使用默认角色方向")
        gender = str(journey["seed"].get("ai_gender") or "不指定")
        subject = "这位女性聊天对象" if gender == "女" else "这位男性聊天对象" if gender == "男" else "这位聊天对象"
        archetypes = [
            {"id": person_id, "label": label, "summary": f"{subject}{summary}"}
            for person_id, (label, summary) in zip(_PERSON_IDS, _DEFAULT_ARCHETYPES, strict=True)
        ]
        fallbacks = deepcopy(journey.get("fallbacks") or {})
        fallbacks["archetypes"] = True
        return self._save(
            journey,
            status="archetypes_ready",
            archetypes=archetypes,
            cards_by_slot={},
            card_batches=_empty_card_batches(),
            selections={},
            final_card=None,
            fallbacks=fallbacks,
            progress={
                "stage": "archetypes",
                "current": 8,
                "total": 8,
                "percent": 100,
                "message": "已使用默认角色方向",
            },
        )

    def use_default_cards(self, journey_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        self._require_revision(journey, expected_revision)
        can_retry = journey.get("status") == "cards_failed" and journey.get("archetypes")
        if journey.get("status") != "archetypes_ready" and not can_retry:
            raise ValueError("请先完成 8 个角色方向")
        people = list(journey.get("archetypes") or [])
        if len(people) != ARCHETYPE_COUNT:
            raise ValueError("角色方向不完整，不能使用默认命签")
        gender = str(journey["seed"].get("ai_gender") or "不指定")
        subject = "女性角色" if gender == "女" else "男性角色" if gender == "男" else "角色"
        cards_by_slot: dict[str, list[dict[str, Any]]] = {}
        for slot in DESTINY_SLOTS:
            slot_id = str(slot["id"])
            labels = _DEFAULT_SLOT_LABELS[slot_id]
            cards_by_slot[slot_id] = [
                {
                    "card_id": f"{person['id']}:{slot_id}",
                    "source_id": person["id"],
                    "source_label": person["label"],
                    "slot_id": slot_id,
                    "slot_name": slot["axis"],
                    "label": label,
                    "summary": f"{subject}会以{label}的方式表现：{_DEFAULT_SLOT_BEHAVIORS[slot_id]}",
                    "interaction_willingness": _DEFAULT_WILLINGNESS[index],
                }
                for index, (person, label) in enumerate(zip(people, labels, strict=True))
            ]
        fallbacks = deepcopy(journey.get("fallbacks") or {})
        fallbacks["cards"] = True
        return self._save(
            journey,
            status="cards_ready",
            cards_by_slot=cards_by_slot,
            card_batches={
                batch_id: {**state, "status": "ready"}
                for batch_id, state in _empty_card_batches().items()
            },
            selections={},
            final_card=None,
            fallbacks=fallbacks,
            progress={
                "stage": "cards",
                "current": 96,
                "total": 96,
                "percent": 100,
                "message": "已使用默认 96 张命签",
            },
        )

    def unselect(self, journey_id: str, slot_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        if journey.get("status") == "committed" or journey.get("character_id"):
            raise ValueError("已收入角色库的旅程不能回退")
        if journey.get("status") not in {
            "cards_ready",
            "selections_ready",
            "review_ready",
            "synthesis_failed",
        }:
            raise ValueError("命格正在生成，暂时不能回退")
        self._require_revision(journey, expected_revision)
        selections = deepcopy(journey.get("selections") or {})
        selected_slots = [slot for slot in DESTINY_SLOTS if str(slot["id"]) in selections]
        if not selected_slots:
            raise ValueError("当前没有可回退的选择")
        last_slot_id = str(selected_slots[-1]["id"])
        if slot_id != last_slot_id:
            raise ValueError("只能回退最后完成的一项")
        selections.pop(last_slot_id, None)
        return self._save(
            journey,
            selections=selections,
            final_card=None,
            status="cards_ready",
            progress={
                "stage": "selection",
                "current": len(selections),
                "total": len(DESTINY_SLOTS),
                "percent": round(len(selections) / len(DESTINY_SLOTS) * 100),
                "message": f"已回退至第 {len(selections) + 1} 项",
            },
        )

    def rewind_archetypes(self, journey_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        if journey.get("status") == "committed" or journey.get("character_id"):
            raise ValueError("已收入角色库的旅程不能回退")
        if journey.get("status") in _INTERRUPTED_STAGE_STATUS:
            raise ValueError("命格正在生成，暂时不能回退")
        self._require_revision(journey, expected_revision)
        if not journey.get("archetypes"):
            raise ValueError("当前没有可回退的角色方向")
        fallbacks = deepcopy(journey.get("fallbacks") or {})
        fallbacks.update({"archetypes": False, "cards": False})
        return self._save(
            journey,
            status="archetypes_failed",
            archetypes=[],
            cards_by_slot={},
            card_batches=_empty_card_batches(),
            selections={},
            final_card=None,
            fallbacks=fallbacks,
            progress={
                "stage": "archetypes",
                "current": 0,
                "total": 8,
                "percent": 0,
                "message": "已回退，请重新创建 8 个角色方向",
            },
        )

    @staticmethod
    def _normalize_synthesis_card(
        raw: str, seed: DestinySeed, journey_id: str, selections: list[dict[str, Any]]
    ) -> dict[str, Any]:
        generated = _json_object(raw)
        required = ("description", "personality", "scenario", "first_mes", "mes_example")
        if any(not _clean(generated.get(field), 5000) for field in required):
            raise ValueError("V2 角色卡缺少必要文本字段")
        greetings = generated.get("alternate_greetings")
        if (
            not isinstance(greetings, list)
            or not 2 <= len(greetings) <= 4
            or any(not _clean(item, 1000) for item in greetings)
        ):
            raise ValueError("V2 角色卡需要 2 到 4 条 alternate_greetings")
        _assert_gender_consistency(json.dumps(generated, ensure_ascii=False), seed.ai_gender, strict_role_terms=False)
        appearance = normalize_appearance(generated.get("appearance"))
        if not seed.adult_character:
            appearance["intimate_features"] = ""
        card = normalize_card(
            {
                "data": {
                    "name": seed.ai_name,
                    "description": _clean(generated["description"], 5000),
                    "personality": _clean(generated["personality"], 5000),
                    "scenario": _clean(generated["scenario"], 5000),
                    "first_mes": _clean(generated["first_mes"], 5000),
                    "alternate_greetings": [_clean(item, 1000) for item in greetings],
                    "mes_example": _clean(generated["mes_example"], 8000),
                    "creator": "Mindspace",
                    "character_version": "1.0",
                    "tags": ["Mindspace", "命格"],
                    "system_prompt": "",
                    "post_history_instructions": "",
                    "character_book": {},
                    "memory": {"preferences": [], "tasks": []},
                    "extensions": {
                        "mindspace": {
                            "gender": seed.ai_gender,
                            "relationship": seed.relationship,
                            "user_name": seed.user_name,
                            "relationship_context": seed.relationship_context,
                            "user_alias": seed.user_alias or seed.user_name,
                            "appearance": appearance,
                            "journey_id": journey_id,
                            "selected_card_ids": [card["card_id"] for card in selections],
                        }
                    },
                }
            }
        )
        return card

    async def synthesize(self, journey_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        self._require_revision(journey, expected_revision)
        if set(journey.get("selections") or {}) != set(_SLOT_BY_ID):
            raise ValueError("必须完成十二项选择后才能合成 V2 角色卡")
        if journey.get("status") not in {"selections_ready", "synthesis_failed", "review_ready"}:
            raise ValueError("当前旅程不能合成")
        calls = deepcopy(journey.get("model_calls") or {})
        calls["synthesis"] = int(calls.get("synthesis", 0)) + 1
        journey = self._save(
            journey,
            status="synthesizing",
            model_calls=calls,
            operation_id=str(uuid4()),
            stage_started_at=_now(),
            progress={
                "stage": "synthesis",
                "current": 0,
                "total": 1,
                "percent": 0,
                "message": "正在合成 V2 角色卡",
            },
        )
        try:
            seed = DestinySeed.model_validate(journey["seed"])
            selections = [journey["selections"][str(slot["id"])] for slot in DESTINY_SLOTS]
            card = self._normalize_synthesis_card(
                await self._generate(
                    self._synthesis_messages(journey),
                    request_kind="destiny_synthesis",
                    max_tokens=2600,
                    timeout_seconds=180,
                    temperature=0.35,
                ),
                seed,
                journey_id,
                selections,
            )
            return self._save(
                journey,
                status="review_ready",
                final_card=card,
                operation_id="",
                stage_started_at="",
                progress={
                    "stage": "synthesis",
                    "current": 1,
                    "total": 1,
                    "percent": 100,
                    "message": "V2 角色卡已经成型",
                },
            )
        except Exception as exc:
            self._fail(journey, "synthesis", exc)
            raise

    def clear_selections(self, journey_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        if journey.get("status") not in {
            "cards_ready",
            "selections_ready",
            "review_ready",
            "synthesis_failed",
        }:
            raise ValueError("命格正在生成，暂时不能解契")
        self._require_revision(journey, expected_revision)
        if not journey.get("cards_by_slot"):
            raise ValueError("命签尚未准备完成")
        return self._save(
            journey,
            selections={},
            final_card=None,
            status="cards_ready",
            progress={
                "stage": "selection",
                "current": 0,
                "total": 12,
                "percent": 0,
                "message": "十二项选择已清空",
            },
        )

    def commit(self, journey_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self._get_mutable(journey_id)
        self._require_revision(journey, expected_revision)
        if journey.get("character_id"):
            return self.characters.get(str(journey["character_id"]))
        card = journey.get("final_card")
        if journey.get("status") != "review_ready" or not isinstance(card, dict):
            raise ValueError("最终角色卡尚未通过合成与审阅")
        seed = DestinySeed.model_validate(journey["seed"])
        with self.database.transaction(operation="commit_destiny_journey", details={"journey_id": journey_id}):
            journey = self._save(
                journey,
                status="committing",
                operation_id=str(uuid4()),
                stage_started_at=_now(),
                progress={
                    "stage": "commit",
                    "current": 0,
                    "total": 1,
                    "percent": 0,
                    "message": "正在收入角色库",
                },
            )
            avatar = seed.avatar or {
                "src": "/assets/characters/placeholder-2.webp"
                if seed.ai_gender == "男"
                else "/assets/characters/placeholder-1.webp",
                "aspect": "2 / 3",
                "scale": 1.0,
                "x": 0,
                "y": 0,
            }
            record = self.characters.create(
                card=card,
                source="draw",
                avatar=avatar,
                user_alias=seed.user_alias or seed.user_name,
                relationship_label=seed.relationship,
            )
            self._save(
                journey,
                status="committed",
                character_id=record["character_id"],
                operation_id="",
                stage_started_at="",
                progress={
                    "stage": "committed",
                    "current": 1,
                    "total": 1,
                    "percent": 100,
                    "message": "已收入角色库",
                },
            )
        return record

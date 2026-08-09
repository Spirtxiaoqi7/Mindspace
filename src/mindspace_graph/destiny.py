"""The V7 Destiny Canvas authoring journey.

This module intentionally keeps generation small and deterministic: one call for
eight directions, one call for the 96 visible cards, and one call for the V2
character card.  A malformed provider response is saved as a failed stage and
is never "fixed" with a hidden follow-up request.
"""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from mindspace_graph.character_card import normalize_card
from mindspace_graph.models import ApiConfig
from mindspace_graph.product_database import ProductDatabase


DESTINY_SCHEMA_VERSION = "7.0.0"
JOURNEY_SCHEMA_VERSION = "3.0.0"
ARCHETYPE_COUNT = 8


DESTINY_SLOTS: tuple[dict[str, Any], ...] = (
    {"id": "emotional_baseline", "index": 1, "name": "心候", "axis": "情绪底色", "icon": "心", "x": 20, "y": 18},
    {"id": "desire_profile", "index": 2, "name": "欲核", "axis": "欲望结构", "icon": "欲", "x": 42, "y": 11},
    {"id": "interpretation_style", "index": 3, "name": "观法", "axis": "理解方式", "icon": "观", "x": 65, "y": 18},
    {"id": "intimacy_style", "index": 4, "name": "亲疏", "axis": "亲密需求", "icon": "亲", "x": 82, "y": 31},
    {"id": "relationship_positioning", "index": 5, "name": "势位", "axis": "关系主动性", "icon": "势", "x": 73, "y": 49},
    {"id": "speaking_style", "index": 6, "name": "言声", "axis": "说话方式", "icon": "言", "x": 52, "y": 41},
    {"id": "action_style", "index": 7, "name": "行止", "axis": "行动偏好", "icon": "行", "x": 32, "y": 50},
    {"id": "boundary_conflict", "index": 8, "name": "界修", "axis": "冲突处理", "icon": "界", "x": 15, "y": 64},
    {"id": "daily_rhythm", "index": 9, "name": "烟火", "axis": "日常节律", "icon": "烟", "x": 31, "y": 80},
    {"id": "interest_style", "index": 10, "name": "兴味", "axis": "兴趣审美", "icon": "兴", "x": 54, "y": 69},
    {"id": "adaptation_style", "index": 11, "name": "变易", "axis": "成长方式", "icon": "变", "x": 74, "y": 80},
    {"id": "failure_repair_pattern", "index": 12, "name": "反象", "axis": "压力反应", "icon": "反", "x": 89, "y": 89},
)

_SLOT_BY_ID = {str(slot["id"]): slot for slot in DESTINY_SLOTS}
_PERSON_IDS = tuple(f"p{index}" for index in range(1, ARCHETYPE_COUNT + 1))
_WILLINGNESS = {"low", "neutral", "normal", "high"}
_WILLINGNESS_ALIASES = {
    "low": "low", "降低": "low", "低": "low", "red": "low", "红": "low",
    "neutral": "neutral", "不影响": "neutral", "无影响": "neutral", "中性": "neutral", "green": "neutral", "绿": "neutral",
    "normal": "normal", "常规": "normal", "普通": "normal", "稳定": "normal", "blue": "normal", "蓝": "normal",
    "high": "high", "高": "high", "高亢": "high", "gold": "high", "金": "high",
}


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


class DestinySeed(BaseModel):
    ai_name: str = Field(min_length=1, max_length=80)
    ai_gender: Literal["女", "男", "不指定"] = "不指定"
    user_name: str = Field(min_length=1, max_length=80)
    user_alias: str = Field(default="", max_length=80)
    relationship: str = Field(min_length=1, max_length=100)
    relationship_context: str = Field(default="", max_length=2400)
    character_expectation: str = Field(min_length=1, max_length=2400)
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
        data.setdefault("user_name", "用户")
        data.setdefault("relationship", "陪伴者")
        return data

    @field_validator(
        "ai_name", "user_name", "user_alias", "relationship", "relationship_context", "character_expectation", mode="before"
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
            "high": {"label": "互动意愿高亢", "meaning": "该特征会让主动聊天和回应意愿变强，不代表好或坏。"},
        },
    }


class DestinyService:
    """Persisted V7 journey with exactly three model-generation stages."""

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
            "selections": {},
            "final_card": None,
            "character_id": "",
            "model_calls": {"archetypes": 0, "cards": 0, "synthesis": 0},
            "errors": [],
            "progress": {"stage": "seed", "current": 0, "total": 12, "percent": 0, "message": "角色种子已建立"},
            "created_at": now,
            "updated_at": now,
        }
        self.database.put_document(self._key(journey_id), value)
        return deepcopy(value)

    def get(self, journey_id: str) -> dict[str, Any]:
        value = self.database.get_document(self._key(journey_id))
        if not isinstance(value, dict):
            raise KeyError("destiny journey not found")
        if value.get("schema_version") != JOURNEY_SCHEMA_VERSION:
            raise ValueError("旧版未完成旅程不能继续，请重新开始创建角色")
        if value.get("status") in {"cards_ready", "selections_ready", "review_ready", "synthesis_failed"} and self._stored_cards_need_regeneration(value):
            errors = list(value.get("errors") or [])
            errors.append({
                "stage": "cards",
                "message": "现有命签的直接标签无效，请点击继续生成命签",
                "created_at": _now(),
            })
            return self._save(
                value,
                status="cards_failed",
                cards_by_slot={},
                selections={},
                final_card=None,
                errors=errors[-20:],
                progress={"stage": "cards", "current": 0, "total": 96, "percent": 0, "message": "命签标签无效，请明确点击继续生成"},
            )
        return deepcopy(value)

    def _save(self, journey: dict[str, Any], **updates: Any) -> dict[str, Any]:
        value = deepcopy(journey)
        value.update({key: deepcopy(item) for key, item in updates.items()})
        value["revision"] = int(journey.get("revision", 0)) + 1
        value["updated_at"] = _now()
        self.database.put_document(self._key(str(value["journey_id"])), value)
        return deepcopy(value)

    def _fail(self, journey: dict[str, Any], stage: str, error: Exception) -> None:
        errors = list(journey.get("errors") or [])
        errors.append({"stage": stage, "message": _clean(error, 1000), "created_at": _now()})
        self._save(
            journey,
            status=f"{stage}_failed",
            errors=errors[-20:],
            progress={"stage": stage, "current": 0, "total": 1, "percent": 0, "message": f"{stage} 失败，请明确点击重试"},
        )

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
                structured, messages, config, request_kind=request_kind,
                max_tokens=max_tokens, timeout_seconds=timeout_seconds,
            )
        else:
            call = asyncio.to_thread(self.llm.generate, messages, config)
        return await asyncio.wait_for(call, timeout=timeout_seconds + 5)

    @staticmethod
    def _archetype_messages(seed: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": '你是聊天角色创作者。只返回 JSON：{"people":[{"id":"p1","label":"","summary":""}]}'},
            {"role": "user", "content": "\n".join((
                "根据下面的输入，创作 8 位风格明显不同的聊天对象。",
                "label 使用“邻家姐姐、年上御姐、淘气弟弟”这类直接标签，不另起人物姓名。",
                "summary 用 1 到 2 句话概括性格、说话方式和相处感觉。",
                "不要编造职业、创伤、前任或共同经历。",
                "", "输入：", json.dumps(seed, ensure_ascii=False),
            ))},
        ]

    @staticmethod
    def _cards_messages(people: list[dict[str, Any]]) -> list[dict[str, str]]:
        slots = [{"id": slot["id"], "name": slot["axis"]} for slot in DESTINY_SLOTS]
        return [
            {"role": "system", "content": "\n".join((
                '你是聊天角色拆解器。只返回 JSON：{"cards":[["p1","emotional_baseline","","","normal"]]}',
                "每项依次是：[人物 ID, 分类 ID, 直接标签, 聊天中可见的表现, 互动意愿]。",
                "互动意愿只能是 low、neutral、normal、high。",
            ))},
            {"role": "user", "content": "\n".join((
                "把下面 8 位聊天对象按顺序分别拆成 12 个分类，共 96 张。",
                "每个人、每个分类各一张。直接标签写 2 到 8 个字的人物特征，例如“慢热敏感、温柔体贴、直率好奇”。",
                "直接标签绝不能重复分类名、分类 ID 或人物方向标签，不要诗化别名。",
                "表现只写用户在聊天中能观察到的行为和语气；每项只用一句短句，不超过 32 个汉字。",
                "互动意愿表示该特征会让角色主动聊天和回应的意愿降低、不受影响、保持常规或变得高亢。",
                "", "人物：", json.dumps(people, ensure_ascii=False, separators=(",", ":")),
                "", "分类（严格按此顺序）：", json.dumps(slots, ensure_ascii=False, separators=(",", ":")),
            ))},
        ]

    @staticmethod
    def _synthesis_messages(journey: dict[str, Any]) -> list[dict[str, str]]:
        selected = [journey["selections"][str(slot["id"])] for slot in DESTINY_SLOTS]
        return [
            {"role": "system", "content": '你是聊天角色卡编写器。只返回 JSON：{"description":"","personality":"","scenario":"","first_mes":"","alternate_greetings":[],"mes_example":""}'},
            {"role": "user", "content": "\n".join((
                "把用户选择的 12 项特征写成同一个连续、自然的聊天角色。",
                "description 只写基础信息和必要背景，简短具体。",
                "personality 写可观察的性格与相处反应，不堆形容词。",
                "scenario 写角色与用户的关系和一个日常聊天情境。",
                "first_mes 与 alternate_greetings 先回应对方，再自然延续；每条简短。",
                "mes_example 写 1 到 2 轮短对话，体现称呼、关系和回应方式。",
                "动作只在当前需要时写一句；不要用长段括号旁白或舞台说明。",
                "不得编造过去、时间、物品、职业、创伤、前任或共同经历。",
                "不要写系统规则、禁令、边界条款或关系契约。",
                "保持日常、具体，不把特征夸大成病态依赖、绝对服从、人格切换或神秘契约。",
                "只有角色期待明确要求成人亲密时才可写成人内容；adult_character 只表示是否允许，不代表必须写。",
                "", "角色种子：", json.dumps(journey["seed"], ensure_ascii=False), "", "用户选择：", json.dumps(selected, ensure_ascii=False),
            ))},
        ]

    @staticmethod
    def _normalize_archetypes(raw: str) -> list[dict[str, Any]]:
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
    def _stored_cards_need_regeneration(cls, journey: dict[str, Any]) -> bool:
        people = {str(person.get("id")): str(person.get("label") or "") for person in journey.get("archetypes") or [] if isinstance(person, dict)}
        cards_by_slot = journey.get("cards_by_slot")
        if len(people) != ARCHETYPE_COUNT or not isinstance(cards_by_slot, dict):
            return True
        for slot in DESTINY_SLOTS:
            slot_id = str(slot["id"])
            cards = cards_by_slot.get(slot_id)
            if not isinstance(cards, list) or len(cards) != ARCHETYPE_COUNT:
                return True
            for card in cards:
                if not isinstance(card, dict):
                    return True
                source_id = _clean(card.get("source_id"), 16)
                if source_id not in people or _clean(card.get("slot_id"), 80) != slot_id:
                    return True
                if cls._is_reserved_card_label(card.get("label"), slot_id, slot, people[source_id]):
                    return True
        return False

    @classmethod
    def _normalize_cards(cls, raw: str, people: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        try:
            rows = _json_object(raw).get("cards")
        except (json.JSONDecodeError, ValueError) as exc:
            text = str(raw or "").strip()
            if text and not text.endswith("}"):
                raise ValueError("模型输出在 JSON 完成前中断；请点击继续生成命签") from exc
            if isinstance(exc, json.JSONDecodeError):
                raise ValueError(f"模型返回的命签 JSON 格式无效（第 {exc.pos + 1} 字符附近）；请点击继续生成命签") from exc
            raise ValueError("模型返回的命签 JSON 格式无效；请点击继续生成命签") from exc
        if not isinstance(rows, list):
            raise ValueError("模型返回中缺少 cards 数组")

        # IDs are deterministic, so the preferred protocol does not repeat
        # person and slot IDs 96 times. The former five-column rows remain
        # accepted for existing journeys and compatible providers.
        if len(rows) == ARCHETYPE_COUNT and all(isinstance(group, list) for group in rows):
            matrix_shape = all(len(group) == len(DESTINY_SLOTS) for group in rows)
            if matrix_shape:
                expanded: list[list[Any]] = []
                for person_index, group in enumerate(rows, start=1):
                    for slot, cell in zip(DESTINY_SLOTS, group, strict=True):
                        if not isinstance(cell, list) or len(cell) != 3:
                            raise ValueError("96 签矩阵中的每格必须包含标签、表现和互动意愿")
                        expanded.append([f"p{person_index}", slot["id"], *cell])
                rows = expanded

        expected_count = ARCHETYPE_COUNT * len(DESTINY_SLOTS)
        if len(rows) != expected_count:
            missing = max(0, expected_count - len(rows))
            raise ValueError(f"模型返回 {len(rows)} 张命签，要求 {expected_count} 张，缺少 {missing} 张")
        labels = {str(person["id"]): str(person["label"]) for person in people}
        expected = {(person_id, str(slot["id"])) for person_id in _PERSON_IDS for slot in DESTINY_SLOTS}
        seen: set[tuple[str, str]] = set()
        cards_by_slot: dict[str, list[dict[str, Any]]] = {str(slot["id"]): [] for slot in DESTINY_SLOTS}
        for row in rows:
            if isinstance(row, list) and len(row) == 5:
                source_id, slot_id, label, summary, willingness = row
            elif isinstance(row, dict):
                source_id = row.get("source_id") or row.get("person_id")
                slot_id = row.get("slot_id")
                label, summary = row.get("label"), row.get("summary")
                willingness = row.get("interaction_willingness")
            else:
                raise ValueError("命签格式必须是五项紧凑数组")
            source_id, slot_id = _clean(source_id, 16), _clean(slot_id, 80)
            label, summary = _clean(label, 100), _clean(summary, 120)
            if not label or not summary or (source_id, slot_id) not in expected:
                raise ValueError("命签的人物、分类、标签或表现无效")
            slot = _SLOT_BY_ID[slot_id]
            if cls._is_reserved_card_label(label, slot_id, slot, labels[source_id]):
                label = cls._label_from_summary(summary, slot_id, slot, labels[source_id])
                if not label:
                    raise ValueError("命签直接标签不能重复分类名、分类 ID 或人物方向标签")
            pair = (source_id, slot_id)
            if pair in seen:
                raise ValueError("96 张命签存在重复的人物与分类组合")
            seen.add(pair)
            cards_by_slot[slot_id].append({
                "card_id": f"{source_id}:{slot_id}", "source_id": source_id,
                "source_label": labels[source_id], "slot_id": slot_id,
                "slot_name": slot["axis"], "label": label, "summary": summary,
                "interaction_willingness": cls._normalize_willingness(willingness),
            })
        missing = expected - seen
        if missing:
            raise ValueError(f"模型缺少 {len(missing)} 张命签")
        return cards_by_slot

    async def generate_archetypes(self, journey_id: str) -> dict[str, Any]:
        journey = self.get(journey_id)
        if journey.get("status") not in {"seed_ready", "archetypes_failed"}:
            raise ValueError("当前旅程不能重新生成角色方向")
        calls = deepcopy(journey.get("model_calls") or {})
        calls["archetypes"] = int(calls.get("archetypes", 0)) + 1
        journey = self._save(
            journey, status="archetypes_generating", archetypes=[], cards_by_slot={}, selections={}, final_card=None,
            model_calls=calls,
            progress={"stage": "archetypes", "current": 0, "total": 8, "percent": 0, "message": "正在创作 8 个角色方向"},
        )
        try:
            archetypes = self._normalize_archetypes(await self._generate(
                self._archetype_messages(journey["seed"]), request_kind="destiny_archetypes",
                max_tokens=1200, timeout_seconds=120, temperature=0.8,
            ))
            return self._save(
                journey, status="archetypes_ready", archetypes=archetypes,
                progress={"stage": "archetypes", "current": 8, "total": 8, "percent": 100, "message": "8 个角色方向已完成"},
            )
        except Exception as exc:
            self._fail(journey, "archetypes", exc)
            raise

    async def generate_cards(self, journey_id: str) -> dict[str, Any]:
        journey = self.get(journey_id)
        can_retry = journey.get("status") == "cards_failed" and journey.get("archetypes")
        if journey.get("status") != "archetypes_ready" and not can_retry:
            raise ValueError("请先完成 8 个角色方向")
        people = list(journey.get("archetypes") or [])
        if len(people) != ARCHETYPE_COUNT:
            raise ValueError("角色方向不完整，不能拆分命签")
        calls = deepcopy(journey.get("model_calls") or {})
        calls["cards"] = int(calls.get("cards", 0)) + 1
        journey = self._save(
            journey, status="cards_generating", cards_by_slot={}, selections={}, final_card=None, model_calls=calls,
            progress={"stage": "cards", "current": 0, "total": 96, "percent": 0, "message": "正在拆分 96 张命签"},
        )
        try:
            cards_by_slot = self._normalize_cards(await self._generate(
                self._cards_messages(people), request_kind="destiny_cards",
                max_tokens=8192, timeout_seconds=180, temperature=0.55,
            ), people)
            return self._save(
                journey, status="cards_ready", cards_by_slot=cards_by_slot,
                progress={"stage": "cards", "current": 96, "total": 96, "percent": 100, "message": "96 张命签已完成"},
            )
        except Exception as exc:
            self._fail(journey, "cards", exc)
            raise

    def select(self, journey_id: str, slot_id: str, payload: DestinySelectionRequest) -> dict[str, Any]:
        journey = self.get(journey_id)
        if slot_id not in _SLOT_BY_ID:
            raise KeyError("unknown destiny slot")
        if journey.get("status") not in {"cards_ready", "selections_ready", "review_ready", "synthesis_failed"}:
            raise ValueError("命签尚未准备完成")
        if payload.expected_revision is not None and payload.expected_revision != journey.get("revision"):
            raise ValueError("stale destiny journey revision")
        choices = list((journey.get("cards_by_slot") or {}).get(slot_id) or [])
        selected = next((card for card in choices if card.get("card_id") == payload.card_id), None)
        if not isinstance(selected, dict):
            raise ValueError("该命签不属于当前分类")
        selections = deepcopy(journey.get("selections") or {})
        selections[slot_id] = {**deepcopy(selected), "selected_at": _now()}
        complete = len(selections) == len(DESTINY_SLOTS)
        return self._save(
            journey, selections=selections, final_card=None,
            status="selections_ready" if complete else "cards_ready",
            progress={"stage": "selection", "current": len(selections), "total": len(DESTINY_SLOTS), "percent": round(len(selections) / len(DESTINY_SLOTS) * 100), "message": f"已定 {len(selections)}/12"},
        )

    @staticmethod
    def _normalize_synthesis_card(raw: str, seed: DestinySeed, journey_id: str, selections: list[dict[str, Any]]) -> dict[str, Any]:
        generated = _json_object(raw)
        required = ("description", "personality", "scenario", "first_mes", "mes_example")
        if any(not _clean(generated.get(field), 5000) for field in required):
            raise ValueError("V2 角色卡缺少必要文本字段")
        greetings = generated.get("alternate_greetings")
        if not isinstance(greetings, list) or not 2 <= len(greetings) <= 4 or any(not _clean(item, 1000) for item in greetings):
            raise ValueError("V2 角色卡需要 2 到 4 条 alternate_greetings")
        card = normalize_card({"data": {
            "name": seed.ai_name,
            "description": _clean(generated["description"], 5000),
            "personality": _clean(generated["personality"], 5000),
            "scenario": _clean(generated["scenario"], 5000),
            "first_mes": _clean(generated["first_mes"], 5000),
            "alternate_greetings": [_clean(item, 1000) for item in greetings],
            "mes_example": _clean(generated["mes_example"], 8000),
            "creator": "Mindspace", "character_version": "1.0", "tags": ["Mindspace", "命格"],
            "system_prompt": "", "post_history_instructions": "", "character_book": {},
            "memory": {"preferences": [], "tasks": []},
            "extensions": {"mindspace": {
                "gender": seed.ai_gender, "relationship": seed.relationship, "user_name": seed.user_name,
                "relationship_context": seed.relationship_context, "user_alias": seed.user_alias or seed.user_name,
                "journey_id": journey_id, "selected_card_ids": [card["card_id"] for card in selections],
            }},
        }})
        return card

    async def synthesize(self, journey_id: str) -> dict[str, Any]:
        journey = self.get(journey_id)
        if set(journey.get("selections") or {}) != set(_SLOT_BY_ID):
            raise ValueError("必须完成十二项选择后才能合成 V2 角色卡")
        if journey.get("status") not in {"selections_ready", "synthesis_failed", "review_ready"}:
            raise ValueError("当前旅程不能合成")
        calls = deepcopy(journey.get("model_calls") or {})
        calls["synthesis"] = int(calls.get("synthesis", 0)) + 1
        journey = self._save(
            journey, status="synthesizing", model_calls=calls,
            progress={"stage": "synthesis", "current": 0, "total": 1, "percent": 0, "message": "正在合成 V2 角色卡"},
        )
        try:
            seed = DestinySeed.model_validate(journey["seed"])
            selections = [journey["selections"][str(slot["id"])] for slot in DESTINY_SLOTS]
            card = self._normalize_synthesis_card(await self._generate(
                self._synthesis_messages(journey), request_kind="destiny_synthesis",
                max_tokens=2600, timeout_seconds=180, temperature=0.35,
            ), seed, journey_id, selections)
            return self._save(
                journey, status="review_ready", final_card=card,
                progress={"stage": "synthesis", "current": 1, "total": 1, "percent": 100, "message": "V2 角色卡已经成型"},
            )
        except Exception as exc:
            self._fail(journey, "synthesis", exc)
            raise

    def clear_selections(self, journey_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        journey = self.get(journey_id)
        if expected_revision is not None and expected_revision != journey.get("revision"):
            raise ValueError("stale destiny journey revision")
        if not journey.get("cards_by_slot"):
            raise ValueError("命签尚未准备完成")
        return self._save(
            journey, selections={}, final_card=None, status="cards_ready",
            progress={"stage": "selection", "current": 0, "total": 12, "percent": 0, "message": "十二项选择已清空"},
        )

    def commit(self, journey_id: str) -> dict[str, Any]:
        journey = self.get(journey_id)
        if journey.get("character_id"):
            return self.characters.get(str(journey["character_id"]))
        card = journey.get("final_card")
        if journey.get("status") != "review_ready" or not isinstance(card, dict):
            raise ValueError("最终角色卡尚未通过合成与审阅")
        seed = DestinySeed.model_validate(journey["seed"])
        with self.database.transaction(operation="commit_destiny_journey", details={"journey_id": journey_id}):
            user_profile = self.profiles.load_document("user_profile")
            if str(user_profile.get("identity", {}).get("preferred_name") or "") != seed.user_name:
                user_profile.setdefault("identity", {})["preferred_name"] = seed.user_name
                self.profiles.save_document("user_profile", user_profile)
            avatar = seed.avatar or {
                "src": "/assets/characters/placeholder-2.webp" if seed.ai_gender == "男" else "/assets/characters/placeholder-1.webp",
                "aspect": "2 / 3", "scale": 1.0, "x": 0, "y": 0,
            }
            record = self.characters.create(
                card=card, source="draw", avatar=avatar, user_alias=seed.user_alias or seed.user_name,
                relationship_label=seed.relationship,
            )
            self._save(
                journey, status="committed", character_id=record["character_id"],
                progress={"stage": "committed", "current": 1, "total": 1, "percent": 100, "message": "已收入角色库"},
            )
        return record

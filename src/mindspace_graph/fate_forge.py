"""Dynamic twelve-slot Fate System options and role-card projection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field, field_validator

from mindspace_graph.models import ApiConfig

FATE_SCHEMA_VERSION = "2.0.0"
RARITIES = {"red", "blue", "gold"}
ANSWER_KINDS = {"yes", "no", "custom"}

FATE_SLOTS: tuple[dict[str, Any], ...] = (
    {"id": "origin", "index": 1, "title": "本命·原火", "short_title": "原火", "icon": "火", "description": "情绪底色"},
    {"id": "desire", "index": 2, "title": "欲望·渴星", "short_title": "渴星", "icon": "星", "description": "真正追求"},
    {"id": "cognition", "index": 3, "title": "认知·心镜", "short_title": "心镜", "icon": "镜", "description": "理解方式"},
    {"id": "emotion", "index": 4, "title": "情绪·潮汐", "short_title": "潮汐", "icon": "潮", "description": "情绪变化"},
    {"id": "attachment", "index": 5, "title": "亲密·引力", "short_title": "引力", "icon": "环", "description": "亲密需求"},
    {"id": "conflict", "index": 6, "title": "冲突·裂痕", "short_title": "裂痕", "icon": "裂", "description": "争执与修复"},
    {"id": "boundary", "index": 7, "title": "关系·权柄", "short_title": "权柄", "icon": "契", "description": "顺从、协商或掌控"},
    {"id": "voice", "index": 8, "title": "表达·声纹", "short_title": "声纹", "icon": "声", "description": "语言与节奏"},
    {"id": "daily", "index": 9, "title": "日常·烟火", "short_title": "烟火", "icon": "灯", "description": "兴趣与习惯"},
    {"id": "agency", "index": 10, "title": "行动·远征", "short_title": "远征", "icon": "途", "description": "目标与行动"},
    {"id": "change", "index": 11, "title": "变化·蜕变", "short_title": "蜕变", "icon": "变", "description": "成长与反复"},
    {"id": "paradox", "index": 12, "title": "宿命·衔环", "short_title": "衔环", "icon": "衔", "description": "核心矛盾"},
)

_SLOT_BY_ID = {str(item["id"]): item for item in FATE_SLOTS}


class FateOptionsRequest(BaseModel):
    relationship: str = Field(min_length=1, max_length=200)
    user_content: str = Field(min_length=1, max_length=2400)
    modification: str = Field(default="", max_length=1200)
    slot_ids: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("relationship", "user_content", "modification", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @field_validator("slot_ids")
    @classmethod
    def valid_slots(cls, values: list[str]) -> list[str]:
        result = list(dict.fromkeys(str(item) for item in values))
        if any(item not in _SLOT_BY_ID for item in result):
            raise ValueError("命格槽位不存在")
        return result


def public_fate_catalog() -> dict[str, Any]:
    return {
        "schema_version": FATE_SCHEMA_VERSION,
        "rarities": {
            "red": {"label": "红色命格", "meaning": "负面与代价"},
            "blue": {"label": "蓝色命格", "meaning": "普通取向"},
            "gold": {"label": "金色命格", "meaning": "高定义方向"},
        },
        "slots": deepcopy(list(FATE_SLOTS)),
    }


def fate_options_messages(value: FateOptionsRequest) -> list[dict[str, str]]:
    slots = [_SLOT_BY_ID[item] for item in value.slot_ids] if value.slot_ids else list(FATE_SLOTS)
    return [
        {
            "role": "system",
            "content": (
                "这是命定系统选项输出。只输出JSON："
                '{"slots":[{"slot_id":"...","options":['
                '{"rarity":"red|blue|gold","title":"命格选项名","summary":"...",'
                '"question":"...","yes_direction":"...","no_direction":"..."}]}]}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "relationship": value.relationship,
                    "user_content": value.user_content,
                    "modification": value.modification,
                    "slots": [
                        {
                            "slot_id": item["id"],
                            "title": item["title"],
                            "description": item["description"],
                            "options": [
                                {"rarity": "red", "meaning": "负面命格与代价"},
                                {"rarity": "blue", "meaning": "普通命格取向"},
                                {"rarity": "gold", "meaning": "高度定义化命格"},
                            ],
                        }
                        for item in slots
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]


def _candidate_id(slot_id: str, rarity: str, title: str, summary: str) -> str:
    digest = hashlib.sha256(f"{slot_id}|{rarity}|{title}|{summary}".encode("utf-8")).hexdigest()[:16]
    return f"generated-{slot_id}-{digest}"


def parse_fate_options(raw: str, requested_slots: list[str]) -> dict[str, list[dict[str, str]]]:
    cleaned = str(raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    document = json.loads(cleaned)
    rows = document.get("slots") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError("AI 命格选项格式无效")
    expected = set(requested_slots)
    result: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        slot_id = str(row.get("slot_id") or "")
        if slot_id not in expected or slot_id in result:
            continue
        options = row.get("options")
        if not isinstance(options, list):
            continue
        normalized: list[dict[str, str]] = []
        seen_rarities: set[str] = set()
        for option in options:
            if not isinstance(option, dict):
                continue
            rarity = str(option.get("rarity") or "")
            title = re.sub(r"\s+", " ", str(option.get("title") or "")).strip()[:40]
            summary = re.sub(r"\s+", " ", str(option.get("summary") or "")).strip()[:240]
            if rarity not in RARITIES or rarity in seen_rarities or not title or not summary:
                continue
            question = re.sub(r"\s+", " ", str(option.get("question") or "")).strip()[:240]
            yes_direction = re.sub(r"\s+", " ", str(option.get("yes_direction") or "")).strip()[:300]
            no_direction = re.sub(r"\s+", " ", str(option.get("no_direction") or "")).strip()[:300]
            if rarity == "gold" and not all((question, yes_direction, no_direction)):
                continue
            normalized.append({
                "id": _candidate_id(slot_id, rarity, title, summary),
                "rarity": rarity,
                "title": title,
                "summary": summary,
                "question": question,
                "yes_direction": yes_direction,
                "no_direction": no_direction,
            })
            seen_rarities.add(rarity)
        if seen_rarities == RARITIES:
            normalized.sort(key=lambda item: ("red", "blue", "gold").index(item["rarity"]))
            result[slot_id] = normalized
    if set(result) != expected:
        raise ValueError("AI 没有完整生成所需命格选项")
    return result


async def generate_fate_options_once(value: FateOptionsRequest, *, llm: Any, settings: Any) -> dict[str, Any]:
    if settings.llm_mode != "openai" or not str(settings.llm_api_key or "").strip():
        raise ValueError("请先配置可用的模型 API，再实时生成命格选项")
    requested = value.slot_ids or [str(item["id"]) for item in FATE_SLOTS]
    config = ApiConfig(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=0.9,
        max_tokens=4200,
    )
    structured_generate = getattr(llm, "generate_structured", None)
    if callable(structured_generate):
        call = asyncio.to_thread(
            structured_generate,
            fate_options_messages(value),
            config,
            request_kind="character_generate",
            max_tokens=4200,
            timeout_seconds=45.0,
        )
    else:
        call = asyncio.to_thread(llm.generate, fate_options_messages(value), config)
    raw = await asyncio.wait_for(call, timeout=50.0)
    return {
        "schema_version": FATE_SCHEMA_VERSION,
        "options": parse_fate_options(raw, requested),
    }


def _normalize_selected_candidate(raw: dict[str, Any], slot_id: str) -> dict[str, str]:
    rarity = str(raw.get("rarity") or "")
    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()[:40]
    summary = re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()[:240]
    if rarity not in RARITIES or not title or not summary:
        raise ValueError("命格选项内容无效")
    return {
        "id": str(raw.get("fate_id") or raw.get("id") or _candidate_id(slot_id, rarity, title, summary))[:100],
        "rarity": rarity,
        "title": title,
        "summary": summary,
        "question": str(raw.get("question") or "").strip()[:240],
        "yes_direction": str(raw.get("yes_direction") or "").strip()[:300],
        "no_direction": str(raw.get("no_direction") or "").strip()[:300],
    }


def normalize_fate_forge(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("命定系统数据必须是对象")
    raw_selections = value.get("selections")
    if not isinstance(raw_selections, list) or len(raw_selections) != len(FATE_SLOTS):
        raise ValueError("必须完成十二个命格槽位")
    result: list[dict[str, Any]] = []
    seen_slots: set[str] = set()
    for raw in raw_selections:
        if not isinstance(raw, dict):
            raise ValueError("命格选择格式无效")
        slot_id = str(raw.get("slot_id") or "")
        if slot_id in seen_slots or slot_id not in _SLOT_BY_ID:
            raise ValueError("命格槽位重复或不存在")
        fate = _normalize_selected_candidate(raw, slot_id)
        answer = str(raw.get("answer") or "")
        custom = str(raw.get("custom") or "").strip()[:600]
        if fate["rarity"] == "gold":
            if answer not in ANSWER_KINDS:
                raise ValueError("金色命格必须回答是、否或自定义")
            if answer == "custom" and not custom:
                raise ValueError("自定义金色方向不能为空")
            direction = (
                str(raw.get("resolved_direction") or "").strip()[:600]
                or (fate["yes_direction"] if answer == "yes" else fate["no_direction"] if answer == "no" else custom)
            )
            if not direction:
                raise ValueError("金色命格方向无效")
        else:
            answer, custom, direction = "", "", fate["summary"]
        result.append({
            "slot_id": slot_id,
            "slot_title": _SLOT_BY_ID[slot_id]["title"],
            "fate_id": fate["id"],
            "rarity": fate["rarity"],
            "title": fate["title"],
            "summary": fate["summary"],
            "answer": answer,
            "custom": custom,
            "resolved_direction": direction,
        })
        seen_slots.add(slot_id)
    if seen_slots != set(_SLOT_BY_ID):
        raise ValueError("十二个命格槽位尚未完整")
    result.sort(key=lambda item: int(_SLOT_BY_ID[item["slot_id"]]["index"]))
    prior_source = value.get("source") if isinstance(value.get("source"), dict) else {}
    return {
        "schema_version": FATE_SCHEMA_VERSION,
        "seed": str(value.get("seed") or "").strip()[:100],
        "source": {
            "relationship": str(value.get("relationship") or prior_source.get("relationship") or "").strip()[:200],
            "user_content": str(value.get("user_content") or prior_source.get("user_content") or "").strip()[:2400],
        },
        "selections": result,
        "rarity_counts": {rarity: sum(1 for item in result if item["rarity"] == rarity) for rarity in ("red", "blue", "gold")},
    }


def fate_generation_packet(value: Any) -> dict[str, Any]:
    normalized = normalize_fate_forge(value)
    return {"system": "角色卡输出", **normalized}


def derive_legacy_traits(value: Any) -> tuple[list[str], str]:
    normalized = normalize_fate_forge(value)
    positive = [str(item["title"]) for item in normalized["selections"] if item["rarity"] in {"blue", "gold"}]
    negative = [str(item["title"]) for item in normalized["selections"] if item["rarity"] == "red"]
    return (positive + ["关系取向明确", "表达具体"])[:2], negative[0] if negative else str(normalized["selections"][-1]["title"])


def fate_block_additions(value: Any) -> dict[str, str]:
    normalized = normalize_fate_forge(value)
    selected = {item["slot_id"]: item for item in normalized["selections"]}

    def render(*slot_ids: str) -> str:
        return "命格：" + "；".join(f"{selected[item]['title']}：{selected[item]['resolved_direction']}" for item in slot_ids) + "。"

    return {
        "identity_story": render("origin", "desire", "agency", "change", "paradox"),
        "core_personality": render("origin", "cognition", "emotion"),
        "contradictions": render("emotion", "conflict", "change", "paradox"),
        "daily_life": render("daily", "agency"),
        "agency_goals": render("desire", "agency", "change"),
        "relationship_pattern": render("attachment", "conflict", "boundary"),
        "voice_style": render("emotion", "voice"),
        "scenario_boundaries": render("attachment", "boundary", "paradox"),
    }

"""Mindspace's compact SillyTavern Character Card V2 authority model."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

CARD_SPEC = "chara_card_v2"
CARD_VERSION = "2.0"
CARD_FIELDS = (
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "creator_notes",
    "system_prompt",
    "post_history_instructions",
    "alternate_greetings",
    "character_book",
    "memory",
    "tags",
    "creator",
    "character_version",
    "extensions",
)


def _text(value: Any, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _strings(value: Any, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def empty_memory() -> dict[str, list[str]]:
    return {"preferences": [], "tasks": []}


def normalize_memory(value: Any) -> dict[str, list[str]]:
    raw = value if isinstance(value, dict) else {}
    return {
        "preferences": _strings(raw.get("preferences"), limit=100, item_limit=500),
        "tasks": _strings(raw.get("tasks"), limit=100, item_limit=500),
    }


def normalize_tasks_v2(value: Any, legacy_titles: Any = None) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    if not raw_items and isinstance(legacy_titles, list):
        raw_items = [{"title": title, "status": "pending"} for title in legacy_titles]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items[:100]):
        item = raw if isinstance(raw, dict) else {"title": raw}
        title = _text(item.get("title"), 300)
        if not title:
            continue
        task_id = _text(item.get("id"), 64)
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", task_id):
            task_id = str(uuid5(NAMESPACE_URL, f"mindspace:task:{index}:{title}"))
        if task_id in seen:
            continue
        seen.add(task_id)
        status = "completed" if item.get("status") == "completed" else "pending"
        created_at = _text(item.get("created_at"), 80) or datetime.now(UTC).isoformat()
        completed_at = _text(item.get("completed_at"), 80) if status == "completed" else ""
        result.append(
            {
                "id": task_id,
                "title": title,
                "status": status,
                "due_at": _text(item.get("due_at"), 80) or None,
                "created_at": created_at,
                "updated_at": _text(item.get("updated_at"), 80) or created_at,
                "completed_at": completed_at or None,
            }
        )
    return result


def normalize_appearance(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    height_raw = raw.get("height_cm")
    try:
        height = int(float(str(height_raw).replace("cm", "").strip())) if height_raw not in (None, "") else None
    except (TypeError, ValueError):
        height = None
    if height is not None and not 100 <= height <= 250:
        height = None
    return {
        "height_cm": height,
        "body_shape": _text(raw.get("body_shape"), 300),
        "body_features": _text(raw.get("body_features"), 500),
        "face": _text(raw.get("face"), 300),
        "hair": _text(raw.get("hair"), 300),
        "eyes": _text(raw.get("eyes"), 300),
        "skin": _text(raw.get("skin"), 300),
        "distinguishing_features": _strings(raw.get("distinguishing_features"), limit=12, item_limit=200),
        "signature_outfit": _text(raw.get("signature_outfit"), 500),
        "intimate_features": _text(raw.get("intimate_features"), 500),
    }


def appearance_summary(value: Any) -> str:
    appearance = normalize_appearance(value)
    parts: list[str] = []
    if appearance["height_cm"]:
        parts.append(f"{appearance['height_cm']}cm")
    parts.extend(
        str(appearance[key])
        for key in ("body_shape", "body_features", "face", "hair", "eyes", "skin")
        if appearance[key]
    )
    parts.extend(appearance["distinguishing_features"])
    if appearance["signature_outfit"]:
        parts.append(f"标志穿着为{appearance['signature_outfit']}")
    if appearance["intimate_features"]:
        parts.append(str(appearance["intimate_features"]))
    return "，".join(dict.fromkeys(parts))


def description_with_appearance(description: Any, appearance: Any) -> str:
    base = re.sub(r"\s*外表：.*$", "", _text(description, 2400)).strip()
    summary = appearance_summary(appearance)
    return _text(f"{base} 外表：{summary}" if summary else base, 2400)


def normalize_card(value: Any) -> dict[str, Any]:
    raw = deepcopy(value) if isinstance(value, dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    name = _text(data.get("name"), 80)
    if not name:
        raise ValueError("V2 role card requires data.name")
    gender = _text((data.get("extensions") or {}).get("mindspace", {}).get("gender"), 10)
    if gender not in {"男", "女", "不指定"}:
        gender = "不指定"
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    mindspace = extensions.get("mindspace") if isinstance(extensions.get("mindspace"), dict) else {}
    appearance = normalize_appearance(mindspace.get("appearance"))
    memory = normalize_memory(data.get("memory"))
    tasks_v2 = normalize_tasks_v2(mindspace.get("tasks_v2"), memory.get("tasks"))
    memory["tasks"] = [item["title"] for item in tasks_v2]
    normalized = {
        "name": name,
        "description": description_with_appearance(data.get("description"), appearance),
        "personality": _text(data.get("personality"), 2400),
        "scenario": _text(data.get("scenario"), 1600),
        "first_mes": _text(data.get("first_mes"), 1600),
        "mes_example": _text(data.get("mes_example"), 3200),
        "creator_notes": _text(data.get("creator_notes"), 1000),
        # Imported prompt overrides are intentionally inert in Mindspace.
        "system_prompt": "",
        "post_history_instructions": "",
        "alternate_greetings": _strings(data.get("alternate_greetings"), limit=6, item_limit=1200),
        "character_book": data.get("character_book") if isinstance(data.get("character_book"), dict) else {},
        "memory": memory,
        "tags": _strings(data.get("tags"), limit=20, item_limit=80),
        "creator": _text(data.get("creator") or "Mindspace", 120),
        "character_version": _text(data.get("character_version") or "1.0", 80),
        "extensions": {
            "mindspace": {
                "gender": gender,
                "appearance": appearance,
                "tasks_v2": tasks_v2,
                **{
                    key: deepcopy(item)
                    for key, item in mindspace.items()
                    if key
                    in {
                        "destiny_version",
                        "journey_id",
                        "selected_card_ids",
                        "relationship",
                        "relationship_context",
                        "user_name",
                        "user_alias",
                    }
                },
            }
        },
    }
    return {"spec": CARD_SPEC, "spec_version": CARD_VERSION, "data": normalized}


def card_summary(card: dict[str, Any]) -> dict[str, Any]:
    data = normalize_card(card)["data"]
    mindspace = data["extensions"]["mindspace"]
    return {
        "display_name": data["name"],
        "gender": mindspace.get("gender", "不指定"),
        "relationship_label": _text(mindspace.get("relationship") or data["scenario"], 100),
    }


def legacy_profile_to_card(profile: dict[str, Any], *, creator: str = "Mindspace") -> dict[str, Any]:
    identity = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    personality = profile.get("personality") if isinstance(profile.get("personality"), dict) else {}
    relationship = profile.get("relationship_rules") if isinstance(profile.get("relationship_rules"), dict) else {}
    roleplay = profile.get("roleplay") if isinstance(profile.get("roleplay"), dict) else {}
    examples = roleplay.get("examples") if isinstance(roleplay.get("examples"), dict) else {}
    traits = _strings(personality.get("core_traits"), limit=6, item_limit=120)
    speech = _strings(personality.get("speech_style"), limit=4, item_limit=120)
    description = _text(identity.get("self_description"), 2400)
    scenario = _text(relationship.get("relationship_definition") or identity.get("relationship_to_user"), 1600)
    return normalize_card(
        {
            "spec": CARD_SPEC,
            "spec_version": CARD_VERSION,
            "data": {
                "name": identity.get("name") or "Mindspace",
                "description": description,
                "personality": "；".join([*traits, *speech]),
                "scenario": scenario,
                "first_mes": _strings(examples.get("casual"), limit=1, item_limit=1200)[0]
                if _strings(examples.get("casual"), limit=1, item_limit=1200)
                else "你好，我在。",
                "mes_example": "",
                "creator": creator,
                "extensions": {
                    "mindspace": {
                        "gender": identity.get("gender", "不指定"),
                        "appearance": roleplay.get("appearance", {}),
                    }
                },
            },
        }
    )


def prompt_profile_from_card(card: dict[str, Any]) -> dict[str, Any]:
    """Ephemeral compatibility view for the existing conversation planner.

    It is built per request and never persisted as an authoritative profile.
    """

    data = normalize_card(card)["data"]
    mindspace = data["extensions"]["mindspace"]
    gender = mindspace.get("gender", "不指定")
    return {
        "identity": {
            "name": data["name"],
            "gender": gender,
            "self_description": data["description"],
            "relationship_to_user": data["scenario"],
        },
        "personality": {"core_traits": [data["personality"]], "speech_style": []},
        "relationship_rules": {
            "relationship_definition": data["extensions"]["mindspace"].get("relationship") or data["scenario"],
            "preferred_interactions": [],
        },
        "behavior_rules": {},
        "continuity": {},
        "roleplay": {
            "examples": {"casual": [], "intimate": []},
            "scenario_baseline": data["scenario"],
            "appearance": mindspace.get("appearance", {}),
        },
        "v2_card": data,
    }

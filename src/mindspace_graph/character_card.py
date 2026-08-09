"""Mindspace's compact SillyTavern Character Card V2 authority model."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


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


def normalize_card(value: Any) -> dict[str, Any]:
    raw = deepcopy(value) if isinstance(value, dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    name = _text(data.get("name"), 80)
    if not name:
        raise ValueError("V2 role card requires data.name")
    gender = _text(
        (data.get("extensions") or {}).get("mindspace", {}).get("gender"), 10
    )
    if gender not in {"男", "女", "不指定"}:
        gender = "不指定"
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    mindspace = extensions.get("mindspace") if isinstance(extensions.get("mindspace"), dict) else {}
    normalized = {
        "name": name,
        "description": _text(data.get("description"), 2400),
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
        "memory": normalize_memory(data.get("memory")),
        "tags": _strings(data.get("tags"), limit=20, item_limit=80),
        "creator": _text(data.get("creator") or "Mindspace", 120),
        "character_version": _text(data.get("character_version") or "1.0", 80),
        "extensions": {
            "mindspace": {
                "gender": gender,
                **{
                    key: deepcopy(item)
                    for key, item in mindspace.items()
                    if key in {
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
    scenario = _text(
        relationship.get("relationship_definition") or identity.get("relationship_to_user"), 1600
    )
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
                "extensions": {"mindspace": {"gender": identity.get("gender", "不指定")}},
            },
        }
    )


def prompt_profile_from_card(card: dict[str, Any]) -> dict[str, Any]:
    """Ephemeral compatibility view for the existing conversation planner.

    It is built per request and never persisted as an authoritative profile.
    """

    data = normalize_card(card)["data"]
    gender = data["extensions"]["mindspace"].get("gender", "不指定")
    return {
        "identity": {
            "name": data["name"],
            "gender": gender,
            "self_description": data["description"],
            "relationship_to_user": data["scenario"],
        },
        "personality": {"core_traits": [data["personality"]], "speech_style": []},
        "relationship_rules": {"relationship_definition": data["extensions"]["mindspace"].get("relationship") or data["scenario"], "preferred_interactions": []},
        "behavior_rules": {},
        "continuity": {},
        "roleplay": {
            "examples": {"casual": [], "intimate": []},
            "scenario_baseline": data["scenario"],
        },
        "v2_card": data,
    }

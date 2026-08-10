"""Shared request models, helpers, and injected API resources."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from mindspace_graph.audio import AudioService
from mindspace_graph.destiny import DestinyService
from mindspace_graph.service import ProductContainer
from mindspace_graph.settings import AppSettings


class InterruptRequest(BaseModel):
    request_id: str = Field(min_length=1)


class KnowledgeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500_000)
    source: str = Field(default="manual", max_length=200)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000)
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    speed: float = Field(default=1, ge=0.5, le=2)
    voice_cue: str = Field(default="neutral", max_length=32)


class ClearDataRequest(BaseModel):
    scope: Literal["knowledge", "sessions", "all"]
    confirmation: str


class MemoryValueRequest(BaseModel):
    value: str | int | float | bool


class MemoryKeyRequest(BaseModel):
    memory_key: str = Field(min_length=1, max_length=500)


class EntityRequest(BaseModel):
    value: str = Field(min_length=1, max_length=500)
    scope: str = Field(min_length=1, max_length=100)
    entity_type: str = Field(min_length=1, max_length=200)


class EntityAliasRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=500)


class EntityMergeRequest(BaseModel):
    source_entity_id: str = Field(min_length=1, max_length=100)
    target_entity_id: str = Field(min_length=1, max_length=100)


class MemoryRebuildRequest(BaseModel):
    confirmation: str = ""
    dry_run: bool = True


class ASRVocabularyUpdateRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class ASRCorrectionRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=64)
    corrected_text: str = Field(min_length=1, max_length=64)


class ASRVocabularyTestRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ProfileRestoreRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=100)
    expected_revision: int | None = Field(default=None, ge=0)


class CharacterRestoreRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class SessionCreateRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    character_id: str = Field(min_length=1, max_length=64)


class JournalGenerateRequest(BaseModel):
    session_id: str = Field(default="", max_length=100)
    activity_session_id: str = Field(default="", max_length=100)


def _character_summary(record: dict[str, Any]) -> dict[str, Any]:
    summary = {
        key: record.get(key)
        for key in (
            "character_id",
            "schema_version",
            "revision",
            "source",
            "status",
            "display_name",
            "gender",
            "user_alias",
            "relationship_label",
            "created_at",
            "updated_at",
            "last_used_at",
        )
    }
    avatar = dict(record.get("avatar") or {})
    if str(avatar.get("src") or "").startswith("blob:"):
        avatar = {
            "src": "/assets/avatar-ai-default.webp",
            "aspect": "2 / 3",
            "scale": 1.0,
            "x": 0,
            "y": 0,
        }
    summary["avatar"] = avatar
    return summary


def _avatar_suffix(filename: str, data: bytes) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise ValueError("unsupported avatar format")
    valid = (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        or data.startswith((b"GIF87a", b"GIF89a"))
    )
    if not valid:
        raise ValueError("avatar content does not match a supported image")
    return ".jpg" if suffix == ".jpeg" else suffix


def _safe_archive_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-") or "character"


PROFILE_KEYS = {
    "user": "user_profile",
    "assistant": "ai_profile",
    "ai": "ai_profile",
    "state": "runtime_state",
}


def _profile_key(name: str) -> str:
    key = PROFILE_KEYS.get(name.lower())
    if not key:
        raise HTTPException(status_code=404, detail="unknown profile document")
    return key


AVATAR_DEFAULTS: dict[str, dict[str, Any]] = {
    "user": {
        "src": "/assets/avatar-user-default.webp",
        "aspect": "2 / 3",
        "scale": 1.08,
        "x": -12,
        "y": 0,
    },
    "assistant": {
        "src": "/assets/avatar-ai-default.webp",
        "aspect": "2 / 3",
        "scale": 1.0,
        "x": 0,
        "y": 0,
    },
}
AVATAR_ASPECTS = {"2 / 3", "3 / 4", "4 / 5", "9 / 16", "1 / 1"}


def _normalize_avatar_entry(role: str, raw: Any) -> dict[str, Any]:
    default = AVATAR_DEFAULTS[role]
    value = raw if isinstance(raw, dict) else {}
    aspect = str(value.get("aspect") or default["aspect"])
    if aspect not in AVATAR_ASPECTS:
        aspect = str(default["aspect"])

    def bounded_number(key: str, minimum: float, maximum: float) -> float:
        try:
            number = float(value.get(key, default[key]))
        except (TypeError, ValueError):
            number = float(default[key])
        return max(minimum, min(maximum, number))

    return {
        "src": str(value.get("src") or default["src"]),
        "aspect": aspect,
        "scale": bounded_number("scale", 0.6, 3.0),
        "x": bounded_number("x", -80, 80),
        "y": bounded_number("y", -80, 80),
    }


def _normalize_avatar_config(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    return {role: _normalize_avatar_entry(role, value.get(role)) for role in ("user", "assistant")}


def _read_avatar_config(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if isinstance(value, dict):
                return _normalize_avatar_config(value)
        except (OSError, json.JSONDecodeError):
            pass
    return _normalize_avatar_config({})


def _voice_energy_threshold_db(
    audio_config: dict[str, Any],
    *,
    playing: bool,
    noise_floor_db: float | None,
) -> float:
    """Return a stable server gate before FunASR VAD confirms real speech.

    Older clients reported a short startup noise sample and raised the gate from
    that value.  The sample was both timing-sensitive and easy to contaminate
    with the user's first words, so it could suppress quiet Chinese speech.
    Keep the argument for wire compatibility, but deliberately do not use it.
    """

    base_key = "asr_barge_in_energy_threshold_db" if playing else "asr_listening_energy_threshold_db"
    threshold = float(audio_config[base_key])
    if playing:
        # TTS playback still needs echo rejection, but the energy gate is only a
        # candidate filter. FunASR VAD and semantic arbitration decide whether
        # the assistant is actually interrupted.
        threshold -= 4.0
    return min(-15.0, threshold)


@dataclass(slots=True)
class ApiContext:
    """One explicitly injected view of the app-owned product services."""

    container: ProductContainer
    settings: AppSettings
    audio: AudioService
    destiny: DestinyService
    shared_http: httpx.AsyncClient
    web_root: Path
    avatar_root: Path
    avatar_config_path: Path
    character_root: Path
    scene_asset_root: Path
    settings_journal_key: str
    serialize_settings_update: Any
    recover_settings_transaction: Any
    destiny_avatar_is_referenced: Any
    cleanup_unreferenced_destiny_avatars: Any
    promote_destiny_avatar: Any


__all__ = [
    "ASRCorrectionRequest",
    "ASRVocabularyTestRequest",
    "ASRVocabularyUpdateRequest",
    "ApiContext",
    "CharacterRestoreRequest",
    "ClearDataRequest",
    "EntityAliasRequest",
    "EntityMergeRequest",
    "EntityRequest",
    "InterruptRequest",
    "JournalGenerateRequest",
    "KnowledgeRequest",
    "MemoryKeyRequest",
    "MemoryRebuildRequest",
    "MemoryValueRequest",
    "ProfileRestoreRequest",
    "SessionCreateRequest",
    "TTSRequest",
    "_avatar_suffix",
    "_character_summary",
    "_normalize_avatar_config",
    "_profile_key",
    "_read_avatar_config",
    "_safe_archive_name",
    "_voice_energy_threshold_db",
]

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
from mindspace_graph.models import ApiConfig, JsonUpdatePlan, JsonWriteReceipt, ProfileBundle
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.profile_schema import DEFAULT_PROFILE_SCHEMA

CHARACTER_SCHEMA_VERSION = "1.0.0"
MIGRATION_KEY = "migration:characters:0.6.0"
LEGACY_CHARACTER_ID = str(uuid5(NAMESPACE_URL, "mindspace:legacy-character"))
SAFE_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
SOURCES = {"draw", "custom", "imported", "migrated"}
STATUSES = {"active", "archived"}


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


CORE_TRAITS: list[dict[str, Any]] = [
    {"id": "warm", "label": "温柔", "conflicts": ["cold"]},
    {"id": "assertive", "label": "强势", "conflicts": ["passive"]},
    {"id": "playful", "label": "俏皮", "conflicts": ["solemn"]},
    {"id": "rational", "label": "理性", "conflicts": ["impulsive"]},
    {"id": "sensitive", "label": "敏感", "conflicts": ["detached"]},
    {"id": "frank", "label": "坦率", "conflicts": ["guarded"]},
    {"id": "independent", "label": "独立", "conflicts": ["dependent"]},
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
    {"id": "dependent", "label": "缺乏安全感", "conflicts": ["independent"]},
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
    ai_gender: Literal["男", "女"]
    core_traits: list[str] = Field(min_length=2, max_length=2)
    flaw: str = Field(min_length=1, max_length=80)
    relationship: str = Field(min_length=1, max_length=100)
    user_name: str = Field(min_length=1, max_length=80)
    user_alias: str = Field(default="", max_length=80)

    @field_validator(
        "ai_name", "flaw", "relationship", "user_name", "user_alias", mode="before"
    )
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


def local_profile_from_draft(value: CharacterDraftInput) -> dict[str, Any]:
    """Create a complete editable card even when the configured API is unavailable."""

    profile = deepcopy(DEFAULT_PROFILES["ai_profile"])
    profile["identity"].update(
        {
            "name": value.ai_name,
            "gender": value.ai_gender,
            "self_description": (
                f"一位{value.core_traits[0]}、{value.core_traits[1]}，"
                f"但{value.flaw}的 AI 伙伴"
            ),
            "relationship_to_user": value.relationship,
        }
    )
    profile["personality"]["core_traits"] = list(value.core_traits)
    profile["personality"]["speech_style"] = ["自然口语", "保持角色一致", "有自己的看法"]
    profile["relationship_rules"].update(
        {
            "relationship_definition": (
                f"与{value.user_name}是{value.relationship}；"
                "关系由双方持续互动形成，不擅自编造共同经历"
            ),
            "preferred_interactions": ["主动承接话题", "表达真实偏好", "记住确认过的共同经历"],
            "conflict_behavior": ["出现分歧时直接说明，不用模板化关心回避问题"],
            "repair_behavior": ["先确认具体矛盾，再以符合自身性格的方式修复关系"],
        }
    )
    profile["behavior_rules"]["always_apply"] = [
        "忠于自身人格和已经确认的关系",
        "不把检索候选或猜测当成用户事实",
    ]
    profile["behavior_rules"]["avoid"] = ["客服腔", "无依据编造", "机械复述用户"]
    profile["continuity"]["long_term_goals"] = [
        f"与{value.user_name}建立连续、可信且具有角色个性的长期关系"
    ]
    profile["roleplay"]["selfhood"]["flaws"] = [value.flaw]
    profile["roleplay"]["selfhood"]["values"] = list(value.core_traits)
    profile["roleplay"]["agency"]["initiative_sources"] = [
        "自身兴趣",
        "当前关系状态",
        "未完成的话题",
    ]
    profile["roleplay"]["voice"]["cadence"] = "自然口语，长短句有变化"
    profile["roleplay"]["post_history_note"] = (
        f"用户的全局名称是{value.user_name}。"
        + (f"本角色习惯称呼用户为{value.user_alias}。" if value.user_alias else "")
    )
    profile["revision"] = 0
    profile.pop("updated_at", None)
    return DEFAULT_PROFILE_SCHEMA.validate_document("ai_profile", profile)


def character_generation_messages(
    value: CharacterDraftInput, fallback_profile: dict[str, Any]
) -> list[dict[str, str]]:
    """One-shot JSON-only instruction for the user-triggered assisted editor."""

    return [
        {
            "role": "system",
            "content": (
                "你是 Mindspace 人物卡编辑器。只返回一个 JSON 对象，不要 Markdown、解释或代码围栏。"
                "必须完整保留给定 JSON 的键与类型，只填充内容；schema_version/profile_type/revision "
                "保持不变。性别、姓名、关系和用户称呼是用户明确选择，不得更改。"
                "角色需要有鲜明人格、合理缺陷、独立观点和自然口语，不能把缺陷消解成完美优点。"
                "不要编造双方已经共同经历过的事件。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "confirmed_input": value.model_dump(mode="json"),
                    "target_json": fallback_profile,
                },
                ensure_ascii=False,
            ),
        },
    ]


def parse_generated_profile(raw: str, fallback: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("模型返回值不是 JSON 对象")
        candidate = deepcopy(fallback)
        for key, item in parsed.items():
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
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        warnings.append(f"AI 返回无法通过人物卡校验，已使用本地模板：{exc}")
        return deepcopy(fallback), warnings


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
        staging = backup_root.with_name(
            f"{backup_root.name}.staging-{uuid4().hex[:8]}"
        )
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
        if gender not in {"男", "女"}:
            raise ValueError("character gender must be 男 or 女")
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
                if (
                    submitted.get("revision") is not None
                    and int(submitted.get("revision", 0))
                    != int(current_document.get("revision", 0))
                ):
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
                    candidate_document.get("identity", {}).get("name")
                    or candidate["display_name"]
                )
                candidate["gender"] = str(
                    candidate_document.get("identity", {}).get("gender")
                    or candidate["gender"]
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
        snapshot = self.database.get_document(
            f"{self._history_prefix(character_id)}{version_id}"
        )
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

    def apply_profile_plan(
        self, character_id: str, plan: JsonUpdatePlan
    ) -> JsonWriteReceipt:
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
        return JsonWriteReceipt(
            turn_id=plan.turn_id, applied=True, patches=receipt_patches
        )

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
        draft = {
            "draft_id": draft_id,
            "schema_version": "1.0.0",
            "revision": 1,
            "status": "input",
            "input": payload.model_dump(mode="json"),
            "profile": local_profile_from_draft(payload),
            "avatar": {},
            "generation_mode": "local_template",
            "model_call_count": 0,
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
        for key in ("input", "profile", "avatar", "generation_mode", "model_call_count", "warnings"):
            if key in updates:
                draft[key] = deepcopy(updates[key])
        draft["revision"] = int(draft.get("revision", 0)) + 1
        draft["updated_at"] = _now()
        self.database.put_document(self._draft_key(draft_id), draft)
        return deepcopy(draft)

    def commit_draft(self, draft_id: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        selected = CharacterDraftInput.model_validate(draft["input"])
        candidate = DEFAULT_PROFILE_SCHEMA.validate_document(
            "ai_profile", profile or draft["profile"]
        )
        candidate["identity"]["name"] = selected.ai_name
        candidate["identity"]["gender"] = selected.ai_gender
        candidate["identity"]["relationship_to_user"] = selected.relationship
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
    fallback = local_profile_from_draft(selected)
    warnings: list[str] = []
    call_count = 0
    mode = "local_template"
    profile = fallback
    if settings.llm_mode == "openai" and str(settings.llm_api_key or "").strip():
        call_count = 1
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    llm.generate,
                    character_generation_messages(selected, fallback),
                    ApiConfig(
                        api_key=settings.llm_api_key,
                        base_url=settings.llm_base_url,
                        model=settings.llm_model,
                        temperature=0.7,
                        max_tokens=3000,
                    ),
                ),
                timeout=30.0,
            )
            profile, parse_warnings = parse_generated_profile(raw, fallback)
            warnings.extend(parse_warnings)
            mode = "llm" if not parse_warnings else "local_template"
        except Exception as exc:  # noqa: BLE001 - product fallback must remain available
            warnings.append(f"AI 生成人物卡失败，已使用本地模板：{exc}")
    else:
        warnings.append("尚未配置可用的模型 API，已使用本地模板")
    return repository.save_draft(
        draft_id,
        {
            "profile": profile,
            "generation_mode": mode,
            "model_call_count": call_count,
            "warnings": warnings,
        },
    )

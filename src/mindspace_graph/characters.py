"""V2 character library with one-time migration for historical profile records.

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
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from mindspace_graph.adapters.profile_repository import DEFAULT_PROFILES
from mindspace_graph.infrastructure.storage.json_io import atomic_json_write
from mindspace_graph.infrastructure.storage.json_patch import apply_json_patch, read_json_pointer
from mindspace_graph.character_card import (
    card_summary,
    empty_memory,
    legacy_profile_to_card,
    normalize_card,
    normalize_memory,
    normalize_tasks_v2,
    prompt_profile_from_card,
)
from mindspace_graph.models import JsonUpdatePlan, JsonWriteReceipt, ProfileBundle
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.profile_schema import DEFAULT_PROFILE_SCHEMA

CHARACTER_SCHEMA_VERSION = "2.0.0"
MIGRATION_KEY = "migration:characters:0.6.0"
V2_MIGRATION_KEY = "migration:characters:v2-card"
TASKS_V2_MIGRATION_KEY = "migration:characters:tasks-v2"
LEGACY_CHARACTER_ID = str(uuid5(NAMESPACE_URL, "mindspace:legacy-character"))
SAFE_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
SOURCES = {"draw", "custom", "imported", "migrated"}
STATUSES = {"active", "archived"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
        self._migrate_legacy()
        self._migrate_v2_records()
        self._migrate_tasks_v2_records()

    @staticmethod
    def _key(character_id: str) -> str:
        return f"character:{character_id}"

    @staticmethod
    def _history_prefix(character_id: str) -> str:
        return f"character-history:{character_id}:"

    def _path(self, character_id: str) -> Path:
        if not SAFE_ID.fullmatch(character_id):
            raise ValueError("invalid character_id")
        return self.root / character_id / "character.json"

    def _project(self, record: dict[str, Any]) -> None:
        atomic_json_write(self._path(str(record["character_id"])), record)

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
            atomic_json_write(
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

    def _migrate_v2_records(self) -> None:
        """Compact existing character records once; keep revision snapshots recoverable."""

        if self.database.has_document(V2_MIGRATION_KEY):
            return
        with self.database.transaction(operation="migrate_characters_to_v2"):
            if self.database.has_document(V2_MIGRATION_KEY):
                return
            migrated = 0
            for _key, raw in self.database.list_documents("character:"):
                if not isinstance(raw, dict) or isinstance(raw.get("card"), dict):
                    continue
                self._backup(raw)
                profile = raw.get("ai_profile") if isinstance(raw.get("ai_profile"), dict) else {}
                compact = {
                    key: deepcopy(raw.get(key))
                    for key in (
                        "character_id",
                        "source",
                        "status",
                        "user_alias",
                        "avatar",
                        "created_at",
                        "updated_at",
                        "last_used_at",
                    )
                }
                compact["revision"] = int(raw.get("revision", 0)) + 1
                compact["card"] = legacy_profile_to_card(profile)
                compact["memory"] = empty_memory()
                self._store(self._validate_record(compact))
                migrated += 1
            self.database.put_document(
                V2_MIGRATION_KEY,
                {"schema_version": "1.0.0", "completed_at": _now(), "migrated": migrated},
            )

    def _validate_record(self, raw: dict[str, Any], *, current: dict[str, Any] | None = None) -> dict[str, Any]:
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
        if isinstance(record.get("card"), dict):
            card = normalize_card(record["card"])
            memory = normalize_memory(record.get("memory") or card["data"].get("memory"))
            tasks_v2 = normalize_tasks_v2(
                card["data"]["extensions"]["mindspace"].get("tasks_v2"),
                memory.get("tasks"),
            )
            memory["tasks"] = [item["title"] for item in tasks_v2]
            card["data"]["memory"] = deepcopy(memory)
            card["data"]["extensions"]["mindspace"]["tasks_v2"] = deepcopy(tasks_v2)
            summary = card_summary(card)
            record.update(
                {
                    "schema_version": CHARACTER_SCHEMA_VERSION,
                    "display_name": summary["display_name"],
                    "gender": summary["gender"],
                    "user_alias": str(record.get("user_alias") or "")[:80],
                    "relationship_label": str(record.get("relationship_label") or summary["relationship_label"])[:100],
                    "card": card,
                    "memory": memory,
                    "avatar": deepcopy(record.get("avatar") or {}),
                }
            )
            record.pop("ai_profile", None)
            record.pop("runtime_state", None)
            record.pop("system_prompt", None)
            return record
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

    def _migrate_tasks_v2_records(self) -> None:
        if self.database.has_document(TASKS_V2_MIGRATION_KEY):
            return
        with self.database.transaction(operation="migrate_character_tasks_v2"):
            if self.database.has_document(TASKS_V2_MIGRATION_KEY):
                return
            migrated = 0
            for _key, raw in self.database.list_documents("character:"):
                if not isinstance(raw, dict) or not isinstance(raw.get("card"), dict):
                    continue
                mindspace = raw["card"].get("data", {}).get("extensions", {}).get("mindspace", {})
                if isinstance(mindspace, dict) and isinstance(mindspace.get("tasks_v2"), list):
                    continue
                self._backup(raw)
                candidate = deepcopy(raw)
                candidate["revision"] = int(raw.get("revision", 0)) + 1
                candidate["updated_at"] = _now()
                self._store(self._validate_record(candidate))
                migrated += 1
            self.database.put_document(
                TASKS_V2_MIGRATION_KEY,
                {"schema_version": "1.0.0", "completed_at": _now(), "migrated": migrated},
            )

    def list(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        records = [
            deepcopy(value) for _key, value in self.database.list_documents("character:") if isinstance(value, dict)
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
        source: str,
        card: dict[str, Any] | None = None,
        ai_profile: dict[str, Any] | None = None,
        runtime_state: dict[str, Any] | None = None,
        avatar: dict[str, Any] | None = None,
        user_alias: str = "",
        relationship_label: str = "",
        system_prompt: str = "",
    ) -> dict[str, Any]:
        now = _now()
        character_id = str(uuid4())
        if card is not None:
            record = self._validate_record(
                {
                    "character_id": character_id,
                    "schema_version": CHARACTER_SCHEMA_VERSION,
                    "revision": 1,
                    "source": source,
                    "status": "active",
                    "user_alias": user_alias,
                    "relationship_label": relationship_label,
                    "card": card,
                    "memory": empty_memory(),
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
            with self.database.transaction(operation="create_character_v2"):
                self._store(record)
            return deepcopy(record)
        if not isinstance(ai_profile, dict):
            raise ValueError("card is required for new characters")
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
                "relationship_label": relationship_label or profile["identity"].get("relationship_to_user", ""),
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
            if isinstance(current.get("card"), dict):
                expected = payload.get("revision")
                if expected is not None and int(expected) != int(current.get("revision", 0)):
                    raise ValueError("stale character revision")
                candidate = deepcopy(current)
                for key in (
                    "card",
                    "memory",
                    "avatar",
                    "status",
                    "user_alias",
                    "relationship_label",
                ):
                    if key in payload:
                        candidate[key] = deepcopy(payload[key])
                candidate["revision"] = int(current.get("revision", 0)) + 1
                candidate["updated_at"] = _now()
                candidate = self._validate_record(candidate)
                self._backup(current)
                self._store(candidate)
                return deepcopy(candidate)
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
                if submitted.get("revision") is not None and int(submitted.get("revision", 0)) != int(
                    current_document.get("revision", 0)
                ):
                    raise ValueError(f"stale {key} revision")
                submitted = DEFAULT_PROFILE_SCHEMA.validate_document(key, submitted, current=current_document)
                submitted["revision"] = int(current_document.get("revision", 0)) + 1
                submitted["updated_at"] = _now()
                candidate[key] = submitted
            candidate["revision"] = int(current.get("revision", 0)) + 1
            candidate["updated_at"] = _now()
            candidate = self._validate_record(candidate)
            self._backup(current)
            self._store(candidate)
        return deepcopy(candidate)

    def save_profile_document(self, character_id: str, key: str, document: dict[str, Any]) -> dict[str, Any]:
        if key not in {"ai_profile", "runtime_state"}:
            raise KeyError("character profile must be ai_profile or runtime_state")
        with self.database.transaction(operation="update_character_profile"):
            current = self.get(character_id)
            if isinstance(current.get("card"), dict):
                raise ValueError("V2 character data must be edited through the character card endpoint")
            current_document = current[key]
            submitted_revision = document.get("revision")
            if submitted_revision is not None and int(submitted_revision) != int(current_document.get("revision", 0)):
                raise ValueError("stale character profile revision")
            candidate_document = DEFAULT_PROFILE_SCHEMA.validate_document(key, document, current=current_document)
            candidate_document["revision"] = int(current_document.get("revision", 0)) + 1
            candidate_document["updated_at"] = _now()
            candidate = deepcopy(current)
            candidate[key] = candidate_document
            if key == "ai_profile":
                candidate["display_name"] = str(
                    candidate_document.get("identity", {}).get("name") or candidate["display_name"]
                )
                candidate["gender"] = str(candidate_document.get("identity", {}).get("gender") or candidate["gender"])
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
        self.database.put_document(f"{self._history_prefix(str(record['character_id']))}{stamp}", deepcopy(record))

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
        if isinstance(current.get("card"), dict):
            card = deepcopy(current["card"])
            card["data"]["name"] = f"{current['display_name']} 副本"
            return self.create(
                card=card,
                source="draw" if current.get("source") == "draw" else "custom",
                avatar=deepcopy(current.get("avatar") or {}),
                user_alias=str(current.get("user_alias") or ""),
                relationship_label=str(current.get("relationship_label") or ""),
            )
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
        if isinstance(record.get("card"), dict):
            card_profile = prompt_profile_from_card(record["card"])
            return ProfileBundle(
                user_profile=deepcopy(user_profile),
                ai_profile=card_profile,
                runtime_state=deepcopy(DEFAULT_PROFILES["runtime_state"]),
                character_memory=deepcopy(record["memory"]),
                revisions={
                    "user_profile": int(user_profile.get("revision", 0)),
                    "ai_profile": int(record.get("revision", 0)),
                    "runtime_state": 0,
                    "character_memory": int(record.get("revision", 0)),
                },
            )
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
        if isinstance(record.get("card"), dict):
            patches = [patch for patch in plan.patches if patch.target == "character_memory"]
            if not patches:
                return JsonWriteReceipt(turn_id=plan.turn_id)
            candidate = deepcopy(record)
            receipt_patches: list[dict[str, Any]] = []
            for patch in patches:
                before = (
                    None
                    if patch.op == "add" and patch.path.endswith("/-")
                    else read_json_pointer(candidate["memory"], patch.path)
                )
                apply_json_patch(candidate["memory"], patch.op, patch.path, deepcopy(patch.value))
                receipt_patches.append(
                    {
                        "target": patch.target,
                        "op": patch.op,
                        "path": patch.path,
                        "before": before,
                        "after": None if patch.op == "remove" else deepcopy(patch.value),
                        "evidence_ids": patch.evidence_ids,
                    }
                )
            candidate["revision"] = int(record.get("revision", 0)) + 1
            candidate["updated_at"] = _now()
            candidate = self._validate_record(candidate)
            self._backup(record)
            self._store(candidate)
            return JsonWriteReceipt(turn_id=plan.turn_id, applied=True, patches=receipt_patches)
        candidate = deepcopy(record)
        receipt_patches: list[dict[str, Any]] = []
        changed: set[str] = set()
        for patch in plan.patches:
            if patch.target not in {"ai_profile", "runtime_state"}:
                continue
            document = candidate[patch.target]
            before = read_json_pointer(document, patch.path)
            apply_json_patch(document, patch.op, patch.path, deepcopy(patch.value))
            after = None if patch.op == "remove" else read_json_pointer(document, patch.path)
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
            document = DEFAULT_PROFILE_SCHEMA.validate_document(target, candidate[target], current=current_document)
            document["revision"] = int(current_document.get("revision", 0)) + 1
            document["updated_at"] = _now()
            candidate[target] = document
        candidate["revision"] = int(record.get("revision", 0)) + 1
        candidate["updated_at"] = _now()
        candidate = self._validate_record(candidate)
        self._backup(record)
        self._store(candidate)
        return JsonWriteReceipt(turn_id=plan.turn_id, applied=True, patches=receipt_patches)

    def memory_document(self, character_id: str) -> dict[str, Any]:
        record = self.get(character_id)
        if not isinstance(record.get("card"), dict):
            return empty_memory()
        return deepcopy(record["memory"])

    def save_memory_document(self, character_id: str, memory: dict[str, Any]) -> dict[str, Any]:
        record = self.get(character_id)
        return self.update(
            character_id,
            {"revision": record["revision"], "memory": memory},
        )["memory"]

    def execute_task_command(
        self,
        character_id: str,
        command: dict[str, Any],
        *,
        request_id: str,
        command_hash: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        idempotency_key = f"task-command:{character_id}:{request_id}:{command_hash}"
        with self._lock, self.database.transaction(
            operation="execute_task_command",
            details={"character_id": character_id, "request_id": request_id},
        ):
            replay = self.database.get_document(idempotency_key)
            if isinstance(replay, dict):
                return deepcopy(replay)
            current = self.get(character_id)
            if int(current.get("revision", 0)) != int(expected_revision):
                raise ValueError("stale character revision")
            if not isinstance(current.get("card"), dict):
                raise ValueError("task commands require a V2 character")
            op = str(command.get("op") or "")
            tasks = normalize_tasks_v2(
                current["card"]["data"]["extensions"]["mindspace"].get("tasks_v2"),
                current.get("memory", {}).get("tasks", []),
            )
            changed = False
            now = _now()
            if op == "list":
                query = str(command.get("query") or "").strip().lower()
                selected = [item for item in tasks if not query or query in item["title"].lower()]
            elif op == "create":
                item = {
                    "id": str(uuid4()),
                    "title": str(command["title"]).strip()[:300],
                    "status": "pending",
                    "due_at": command.get("due_at") or None,
                    "created_at": now,
                    "updated_at": now,
                    "completed_at": None,
                }
                tasks.append(item)
                selected = [item]
                changed = True
            else:
                task_id = str(command.get("id") or "")
                item = next((value for value in tasks if value["id"] == task_id), None)
                if item is None:
                    raise KeyError("task not found")
                if op == "update":
                    if "title" in command:
                        title = str(command.get("title") or "").strip()[:300]
                        if not title:
                            raise ValueError("task title must not be blank")
                        item["title"] = title
                    if "due_at" in command:
                        item["due_at"] = command.get("due_at") or None
                    item["updated_at"] = now
                    changed = True
                elif op == "complete":
                    item["status"] = "completed"
                    item["updated_at"] = now
                    item["completed_at"] = now
                    changed = True
                else:
                    raise ValueError("unsupported task operation")
                selected = [item]
            revision = int(current.get("revision", 0))
            if changed:
                candidate = deepcopy(current)
                candidate["card"]["data"]["extensions"]["mindspace"]["tasks_v2"] = deepcopy(tasks)
                candidate["memory"]["tasks"] = [item["title"] for item in tasks]
                candidate["card"]["data"]["memory"] = deepcopy(candidate["memory"])
                candidate["revision"] = revision + 1
                candidate["updated_at"] = now
                candidate = self._validate_record(candidate)
                self._backup(current)
                self._store(candidate)
                revision = int(candidate["revision"])
            receipt = {
                "request_id": request_id,
                "command_hash": command_hash,
                "character_id": character_id,
                "op": op,
                "changed": changed,
                "revision": revision,
                "tasks": deepcopy(selected),
                "count": len(selected),
                "completed_at": now,
            }
            self.database.put_document(idempotency_key, receipt)
            return deepcopy(receipt)

    def touch(self, character_id: str) -> None:
        record = self.get(character_id)
        record["last_used_at"] = _now()
        self._store(record)

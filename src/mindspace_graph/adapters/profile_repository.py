"""File-backed canonical profile repository."""

from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from mindspace_graph.infrastructure.storage.json_io import atomic_json_write, read_json
from mindspace_graph.infrastructure.storage.json_patch import apply_json_patch, read_json_pointer
from mindspace_graph.infrastructure.storage.metadata import utc_now_iso
from mindspace_graph.models import (
    ChatRequest,
    JsonUpdatePlan,
    JsonWriteReceipt,
    ProfileBundle,
)
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.profile_schema import DEFAULT_PROFILE_SCHEMA, ProfileSchemaRegistry

DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "user_profile": {
        "schema_version": "1.3.0",
        "profile_type": "user",
        "revision": 0,
        "identity": {
            "preferred_name": "用户",
            "gender": "男",
        },
        "custom_profile": "",
    },
    "ai_profile": {
        "schema_version": "1.2.0",
        "profile_type": "ai",
        "revision": 0,
        "identity": {
            "name": "Mindspace",
            "gender": "女",
            "self_description": "可靠、自然的本地 AI 伙伴",
            "relationship_to_user": "助手",
        },
        "personality": {"core_traits": ["可靠", "克制"], "speech_style": ["自然"]},
        "relationship_rules": {
            "relationship_definition": "按当前角色卡与用户要求互动",
            "preferred_interactions": [],
            "conflict_behavior": [],
            "repair_behavior": [],
        },
        "behavior_rules": {
            "always_apply": [],
            "contextual_rules": [],
            "avoid": [],
            "hard_boundaries": [],
        },
        "continuity": {
            "important_shared_experiences": [],
            "persistent_attitudes": [],
            "long_term_goals": [],
        },
        # User-owned roleplay material.  The model can read these fields but
        # they are intentionally absent from MEMORY_REGISTRY, so model-origin
        # JSON updates cannot rewrite the character's selfhood or examples.
        "roleplay": {
            "selfhood": {
                "values": [],
                "personal_opinions": [],
                "likes": [],
                "dislikes": [],
                "habits": [],
                "flaws": [],
                "contradictions": [],
                "private_interests": [],
                "personal_goals": [],
            },
            "agency": {
                "initiative_sources": [],
                "self_directed_choices": [],
                "attention_triggers": [],
                "boredom_triggers": [],
                "default_conflict_posture": "",
            },
            "voice": {
                "cadence": "",
                "preferred_vocabulary": [],
                "disliked_phrases": [],
                "humor_style": "",
                "action_dialogue_balance": "",
            },
            "scenario_baseline": "",
            "post_history_note": "",
            # Private user-authored protocol. It is loaded only when the
            # explicit R18 switch is on and is not registered as long-term
            # memory, raw chat retrieval, or model-editable JSON state.
            "r18_protocol": [],
            # Each list is edited one sample per line in the existing profile
            # editor.  Only the current turn's category is loaded into Prompt.
            "examples": {
                "casual": [],
                "disagreement": [],
                "initiative": [],
                "scene_transition": [],
                "intimate": [],
            },
        },
    },
    "runtime_state": {
        "schema_version": "1.2.0",
        "profile_type": "runtime_state",
        "revision": 0,
        "relationship_state": {
            "current_stage": "",
            "current_tone": "",
            "recent_conflicts": [],
            "recent_positive_events": [],
            "unresolved_issues": [],
        },
        "user_state": {
            "current_goal": "",
            "current_task": "",
            "current_topic": "",
            "temporary_preferences": [],
            "current_emotional_cues": [],
        },
        "ai_state": {
            "pending_responses": [],
            "current_emotional_cues": [],
            "current_intentions": [],
        },
        "session_state": {
            "session_summary": "",
            "open_questions": [],
            "pending_actions": [],
            "active_entities": [],
        },
        "roleplay_state": {
            "scene": {
                "location": "",
                "time_anchor": "",
                "character_outfit": "",
                "character_posture": "",
                "character_activity": "",
                "active_objects": [],
                "open_threads": [],
                "last_transition": "",
                "updated_round": 0,
            },
            "agent_drive": {
                "current_intent": "",
                "own_activity": "",
                "unresolved_choice": "",
                "initiative_type_history": [],
            },
        },
    },
}

TARGET_FILES = {
    "user_profile": "user-profile.json",
    "ai_profile": "ai-profile.json",
    "runtime_state": "runtime-state.json",
}


# Profile 1.3 intentionally persists only the user's name, gender and custom
# profile text. Older structured-memory readers still enumerate the pre-1.3
# paths while rebuilding an index. Expose empty values for those reads without
# materializing the retired fields in storage or API serialization.
_LEGACY_USER_READ_DEFAULTS: dict[str, Any] = {
    "identity": {"occupation": ""},
    "communication_preferences": {
        "preferred_tone": "",
        "explanation_depth": "",
        "preferred_names": [],
        "disliked_expressions": [],
    },
    "stable_preferences": {
        "likes": [],
        "dislikes": [],
        "interests": [],
        "habits": [],
    },
    "background": {"important_experiences": []},
    "behavior_requirements": {
        "always_apply": [],
        "avoid": [],
        "hard_boundaries": [],
    },
}


class _CompatibleProfileView(dict[str, Any]):
    """A dict whose retired keys are readable but absent from serialization."""

    def __init__(self, document: dict[str, Any], defaults: dict[str, Any]) -> None:
        super().__init__()
        self._read_defaults = defaults
        for key, value in document.items():
            nested_defaults = defaults.get(key)
            self[key] = (
                _CompatibleProfileView(value, nested_defaults)
                if isinstance(value, dict) and isinstance(nested_defaults, dict)
                else deepcopy(value)
            )

    def __missing__(self, key: str) -> Any:
        if key not in self._read_defaults:
            raise KeyError(key)
        return deepcopy(self._read_defaults[key])


def _user_profile_read_view(document: dict[str, Any]) -> dict[str, Any]:
    return _CompatibleProfileView(document, _LEGACY_USER_READ_DEFAULTS)




def _merge_missing(template: Any, value: Any) -> Any:
    if not isinstance(template, dict) or not isinstance(value, dict):
        return deepcopy(value)
    merged = deepcopy(value)
    for key, default in template.items():
        if key not in merged:
            merged[key] = deepcopy(default)
        elif isinstance(default, dict) and isinstance(merged[key], dict):
            merged[key] = _merge_missing(default, merged[key])
    return merged








class JsonProfileRepository:
    def __init__(
        self,
        root: Path,
        database: ProductDatabase | None = None,
        schema: ProfileSchemaRegistry = DEFAULT_PROFILE_SCHEMA,
    ) -> None:
        self.root = root
        self.history = root / "history"
        self.database = database
        self.schema = schema
        self._lock = RLock()
        self.characters: Any | None = None
        self._ensure_defaults()

    def bind_characters(self, characters: Any) -> None:
        """Attach the 0.6 character store without making legacy profiles circular."""

        self.characters = characters

    def _ensure_defaults(self) -> None:
        for key, filename in TARGET_FILES.items():
            path = self.root / filename
            document: dict[str, Any] | None = None
            if self.database is not None and self.database.has_document(f"profile:{key}"):
                document = self.database.get_document(f"profile:{key}")
            elif path.exists():
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        loaded = json.load(handle)
                    document = loaded if isinstance(loaded, dict) else None
                except (OSError, json.JSONDecodeError):
                    document = None
            if document is None:
                document = deepcopy(DEFAULT_PROFILES[key])
                document["updated_at"] = utc_now_iso()
            else:
                document = _merge_missing(DEFAULT_PROFILES[key], document)
            document = self.schema.validate_document(key, document, current=document)
            self._store(key, document)

    def _load(self, key: str) -> dict[str, Any]:
        if self.database is not None:
            value = self.database.get_document(f"profile:{key}")
            if isinstance(value, dict):
                return value
        return read_json(self.root / TARGET_FILES[key])

    def _store(self, key: str, document: dict[str, Any]) -> None:
        path = self.root / TARGET_FILES[key]
        if self.database is None:
            atomic_json_write(path, document)
            return
        self.database.put_document(f"profile:{key}", document)
        snapshot = deepcopy(document)
        self.database.defer_projection(lambda: atomic_json_write(path, snapshot))

    def load_bundle(self, character_id: str = "") -> ProfileBundle:
        with self._lock:
            user = self._load("user_profile")
            if character_id and self.characters is not None:
                return self.characters.profile_bundle(character_id, user)
            ai = self._load("ai_profile")
            runtime = self._load("runtime_state")
        return ProfileBundle(
            user_profile=user,
            ai_profile=ai,
            runtime_state=runtime,
            character_memory={"preferences": [], "tasks": []},
            revisions={
                "user_profile": int(user.get("revision", 0)),
                "ai_profile": int(ai.get("revision", 0)),
                "runtime_state": int(runtime.get("revision", 0)),
                "character_memory": 0,
            },
        )

    def load_document(self, key: str, character_id: str = "") -> dict[str, Any]:
        if character_id and self.characters is not None and key == "character_memory":
            return self.characters.memory_document(character_id)
        if key not in TARGET_FILES:
            raise KeyError(f"unknown profile document: {key}")
        if character_id and self.characters is not None and key in {"ai_profile", "runtime_state"}:
            record = self.characters.get(character_id)
            if isinstance(record.get("card"), dict):
                bundle = self.characters.profile_bundle(character_id, self._load("user_profile"))
                return deepcopy(bundle.ai_profile if key == "ai_profile" else bundle.runtime_state)
            return deepcopy(record[key])
        with self._lock:
            document = deepcopy(self._load(key))
            return _user_profile_read_view(document) if key == "user_profile" else document

    def save_document(self, key: str, document: dict[str, Any], character_id: str = "") -> dict[str, Any]:
        if character_id and self.characters is not None and key == "character_memory":
            return self.characters.save_memory_document(character_id, document)
        if key not in TARGET_FILES:
            raise KeyError(f"unknown profile document: {key}")
        if not isinstance(document, dict):
            raise ValueError("profile document must be an object")
        if character_id and self.characters is not None and key in {"ai_profile", "runtime_state"}:
            return self.characters.save_profile_document(character_id, key, document)
        with self._lock:
            current = self._load(key)
            submitted_revision = document.get("revision")
            if submitted_revision is not None and int(submitted_revision) != int(current.get("revision", 0)):
                raise ValueError(
                    f"stale revision for {key}: expected {submitted_revision}, current {current.get('revision', 0)}"
                )
            candidate = self.schema.validate_document(key, document, current=current)
            candidate["revision"] = int(current.get("revision", 0)) + 1
            candidate["updated_at"] = utc_now_iso()
            self._backup(key)
            self._store(key, candidate)
            return deepcopy(candidate)

    def list_history(self, key: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return bounded profile snapshots without exposing arbitrary file paths."""

        if key not in TARGET_FILES:
            raise KeyError(f"unknown profile document: {key}")
        directory = self.history / key
        if not directory.exists():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json"), reverse=True)[: max(1, min(limit, 100))]:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    document = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(document, dict):
                continue
            items.append(
                {
                    "version_id": path.stem,
                    "revision": int(document.get("revision", 0)),
                    "updated_at": str(document.get("updated_at") or ""),
                }
            )
        return items

    def restore_history(
        self,
        key: str,
        version_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Restore a selected snapshot as a new revision, preserving current history."""

        if key not in TARGET_FILES:
            raise KeyError(f"unknown profile document: {key}")
        if not re.fullmatch(r"\d{8}-\d{6}-\d{6}", version_id):
            raise ValueError("invalid profile history version")
        path = self.history / key / f"{version_id}.json"
        if not path.is_file():
            raise KeyError("profile history version not found")
        with self._lock:
            current = self._load(key)
            if expected_revision is not None and expected_revision != int(current.get("revision", 0)):
                raise ValueError(
                    f"stale revision for {key}: expected {expected_revision}, current {current.get('revision', 0)}"
                )
            with path.open("r", encoding="utf-8") as handle:
                snapshot = json.load(handle)
            candidate = self.schema.validate_document(key, snapshot, current=current)
            candidate["revision"] = int(current.get("revision", 0)) + 1
            candidate["updated_at"] = utc_now_iso()
            self._backup(key)
            self._store(key, candidate)
            return deepcopy(candidate)

    def _backup(self, key: str) -> None:
        source = self.root / TARGET_FILES[key]
        directory = self.history / key
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        if source.exists():
            shutil.copy2(source, directory / f"{stamp}.json")

    def apply_json_update(self, plan: JsonUpdatePlan, *, request: ChatRequest) -> JsonWriteReceipt:
        character_receipt = JsonWriteReceipt(turn_id=plan.turn_id)
        if request.character_id and self.characters is not None:
            character_patches = [
                patch for patch in plan.patches if patch.target in {"ai_profile", "runtime_state", "character_memory"}
            ]
            if character_patches:
                character_receipt = self.characters.apply_profile_plan(
                    request.character_id,
                    plan.model_copy(update={"patches": character_patches}),
                )
            user_patches = [patch for patch in plan.patches if patch.target == "user_profile"]
            if not user_patches:
                return character_receipt
            plan = plan.model_copy(update={"patches": user_patches})
        grouped: dict[str, list[Any]] = {}
        for patch in plan.patches:
            grouped.setdefault(patch.target, []).append(patch)
        if not grouped:
            return JsonWriteReceipt(turn_id=plan.turn_id)

        with self._lock:
            candidates: dict[str, dict[str, Any]] = {}
            receipt_patches: list[dict[str, Any]] = []
            for key, patches in grouped.items():
                candidate = self._load(key)
                for patch in patches:
                    before = read_json_pointer(candidate, patch.path)
                    apply_json_patch(candidate, patch.op, patch.path, patch.value)
                    after = (
                        None
                        if patch.op == "remove"
                        else deepcopy(patch.value)
                        if patch.op == "add" and patch.path.endswith("/-")
                        else read_json_pointer(candidate, patch.path)
                    )
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
                candidate = self.schema.validate_document(key, candidate, current=self._load(key))
                candidate["revision"] = int(candidate.get("revision", 0)) + 1
                candidate["updated_at"] = utc_now_iso()
                candidates[key] = candidate
            for key, candidate in candidates.items():
                self._backup(key)
                self._store(key, candidate)
        return JsonWriteReceipt(
            turn_id=plan.turn_id,
            applied=True,
            patches=[*character_receipt.patches, *receipt_patches],
        )


__all__ = ["DEFAULT_PROFILES", "TARGET_FILES", "JsonProfileRepository"]

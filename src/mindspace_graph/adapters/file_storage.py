"""Atomic JSON repositories isolated inside this project's runtime directory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from mindspace_graph.models import (
    ChatRequest,
    DeletionEvent,
    JsonUpdatePlan,
    JsonWriteReceipt,
    ProfileBundle,
)
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.profile_schema import DEFAULT_PROFILE_SCHEMA, ProfileSchemaRegistry
from mindspace_graph.roleplay import (
    chat_message_retrieval_eligible,
    companion_lane,
    evaluate_roleplay_quality,
    resolve_presentation_mode,
)

DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "user_profile": {
        "schema_version": "1.2.0",
        "profile_type": "user",
        "revision": 0,
        "identity": {
            "preferred_name": "用户",
            "gender": "男",
            "occupation": "",
            "language": "zh-CN",
        },
        "communication_preferences": {
            "preferred_tone": "自然",
            "response_length": "",
            "explanation_depth": "清晰",
            "preferred_names": [],
            "disliked_expressions": [],
        },
        "stable_preferences": {"likes": [], "dislikes": [], "interests": [], "habits": []},
        "background": {"important_experiences": []},
        "behavior_requirements": {"always_apply": [], "avoid": [], "hard_boundaries": []},
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


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


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


def _pointer_tokens(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValueError("JSON pointer must start with /")
    return [token.replace("~1", "/").replace("~0", "~") for token in path[1:].split("/")]


def _read_pointer(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for token in _pointer_tokens(path):
        if isinstance(current, list):
            if token == "-":
                return None
            current = current[int(token)]
        else:
            current = current.get(token)
    return deepcopy(current)


def _apply_patch(document: dict[str, Any], op: str, path: str, value: Any = None) -> None:
    tokens = _pointer_tokens(path)
    current: Any = document
    for token in tokens[:-1]:
        if isinstance(current, list):
            current = current[int(token)]
        else:
            current = current.setdefault(token, {})
    leaf = tokens[-1]
    if isinstance(current, list):
        if op == "add" and leaf == "-":
            current.append(value)
        elif op == "add":
            current.insert(int(leaf), value)
        elif op == "remove":
            current.pop(int(leaf))
        else:
            current[int(leaf)] = value
    elif op == "remove":
        current.pop(leaf, None)
    else:
        current[leaf] = value


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
                document["updated_at"] = _now()
            else:
                document = _merge_missing(DEFAULT_PROFILES[key], document)
            document = self.schema.validate_document(key, document, current=document)
            self._store(key, document)

    def _load(self, key: str) -> dict[str, Any]:
        if self.database is not None:
            value = self.database.get_document(f"profile:{key}")
            if isinstance(value, dict):
                return value
        with (self.root / TARGET_FILES[key]).open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _store(self, key: str, document: dict[str, Any]) -> None:
        path = self.root / TARGET_FILES[key]
        if self.database is None:
            _atomic_json(path, document)
            return
        self.database.put_document(f"profile:{key}", document)
        snapshot = deepcopy(document)
        self.database.defer_projection(lambda: _atomic_json(path, snapshot))

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
            return deepcopy(self._load(key))

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
            candidate["updated_at"] = _now()
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
            candidate["updated_at"] = _now()
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
                    before = _read_pointer(candidate, patch.path)
                    _apply_patch(candidate, patch.op, patch.path, patch.value)
                    after = (
                        None
                        if patch.op == "remove"
                        else deepcopy(patch.value)
                        if patch.op == "add" and patch.path.endswith("/-")
                        else _read_pointer(candidate, patch.path)
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
                candidate["updated_at"] = _now()
                candidates[key] = candidate
            for key, candidate in candidates.items():
                self._backup(key)
                self._store(key, candidate)
        return JsonWriteReceipt(
            turn_id=plan.turn_id,
            applied=True,
            patches=[*character_receipt.patches, *receipt_patches],
        )


class JsonSessionRepository:
    SAFE_ID = re.compile(r"[^a-zA-Z0-9_.-]+")

    def __init__(self, root: Path, database: ProductDatabase | None = None) -> None:
        self.root = root
        self.database = database
        self.root.mkdir(parents=True, exist_ok=True)
        self.receipts_path = root.parent / "memory-write-receipts.json"
        self.events_path = root.parent / "memory-deletion-events.json"
        self._lock = RLock()
        if self.database is not None:
            self._import_legacy_documents()
        else:
            if not self.receipts_path.exists():
                _atomic_json(self.receipts_path, {})
            if not self.events_path.exists():
                _atomic_json(self.events_path, [])
        self._migrate_legacy_analysis()

    def _import_legacy_documents(self) -> None:
        assert self.database is not None
        if not self.database.has_document("session_receipts"):
            value: Any = {}
            if self.receipts_path.exists():
                try:
                    value = json.loads(self.receipts_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    value = {}
            self._store_receipts(value if isinstance(value, dict) else {})
        if not self.database.has_document("session_deletion_events"):
            value = []
            if self.events_path.exists():
                try:
                    value = json.loads(self.events_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    value = []
            self._store_events(value if isinstance(value, list) else [])
        for path in self.root.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            session_id = str(value.get("session_id") or path.stem)
            key = self._session_key(session_id)
            if not self.database.has_document(key):
                self._store_session(session_id, value)
        # Recreate every readable JSON projection from canonical state. This is
        # also the repair path after a previous projection I/O failure.
        self._store_receipts(self.database.get_document("session_receipts", {}))
        self._store_events(self.database.get_document("session_deletion_events", []))
        for key, value in self.database.list_documents("session:"):
            if isinstance(value, dict):
                self._store_session(str(value.get("session_id") or key.removeprefix("session:")), value)

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"session:{session_id}"

    def _store_session(self, session_id: str, value: dict[str, Any]) -> None:
        path = self._path(session_id)
        if self.database is None:
            _atomic_json(path, value)
            return
        self.database.put_document(self._session_key(session_id), value)
        snapshot = deepcopy(value)
        self.database.defer_projection(lambda: _atomic_json(path, snapshot))

    def _store_receipts(self, value: dict[str, Any]) -> None:
        if self.database is None:
            _atomic_json(self.receipts_path, value)
            return
        self.database.put_document("session_receipts", value)
        snapshot = deepcopy(value)
        self.database.defer_projection(lambda: _atomic_json(self.receipts_path, snapshot))

    def _store_events(self, value: list[dict[str, Any]]) -> None:
        if self.database is None:
            _atomic_json(self.events_path, value)
            return
        self.database.put_document("session_deletion_events", value)
        snapshot = deepcopy(value)
        self.database.defer_projection(lambda: _atomic_json(self.events_path, snapshot))

    def _path(self, session_id: str) -> Path:
        safe = (self.SAFE_ID.sub("-", session_id).strip(".-") or "session")[:48]
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return self.root / f"{safe}-{digest}.json"

    def _legacy_path(self, session_id: str) -> Path:
        safe = self.SAFE_ID.sub("-", session_id).strip(".-") or "session"
        return self.root / f"{safe}.json"

    def _owned_legacy_path(self, session_id: str) -> Path | None:
        path = self._legacy_path(session_id)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return path if str(value.get("session_id") or path.stem) == session_id else None

    def load_session(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.exists():
            path = self._owned_legacy_path(session_id) or path
        stored = self.database.get_document(self._session_key(session_id)) if self.database is not None else None
        if not isinstance(stored, dict) and not path.exists():
            return {
                "session_id": session_id,
                "title": "新对话",
                "character_id": "",
                "mode": "custom",
                "created_at": _now(),
                "updated_at": _now(),
                "messages": [],
            }
        if isinstance(stored, dict):
            session = stored
        else:
            with self._lock, path.open("r", encoding="utf-8") as handle:
                session = json.load(handle)
        timestamp_dirty = False
        fallback_timestamp = str(session.get("created_at") or session.get("updated_at") or _now())
        for message in session.get("messages", []):
            message.pop("analysis", None)
            if not str(message.get("timestamp") or "").strip():
                timing = message.get("timing") if isinstance(message.get("timing"), dict) else {}
                message["timestamp"] = str(
                    timing.get("assistant_completed_at_utc")
                    or timing.get("server_received_at_utc")
                    or timing.get("request_received_at_utc")
                    or fallback_timestamp
                )
                message["timestamp_source"] = "legacy_backfill"
                timestamp_dirty = True
        if timestamp_dirty:
            self._store_session(session_id, session)
        return session

    def session_exists(self, session_id: str) -> bool:
        if self.database is not None:
            return self.database.has_document(self._session_key(session_id))
        return self._path(session_id).exists() or self._owned_legacy_path(session_id) is not None

    def ensure_session(
        self,
        session_id: str,
        *,
        character_id: str,
        mode: str,
        role_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or authoritatively bind an empty session to one character."""

        if mode not in {"draw", "custom"}:
            raise ValueError("session mode must be draw or custom")
        if not character_id:
            raise ValueError("character_id is required")
        with self._lock:
            exists = self.session_exists(session_id)
            session = self.load_session(session_id)
            bound = str(session.get("character_id") or "")
            if bound and bound != character_id:
                raise ValueError("session is already bound to another character")
            if exists and session.get("messages") and not bound:
                raise ValueError("legacy session must be migrated before it can be used")
            should_snapshot = bool(role_state) and (
                not isinstance(session.get("role_state"), dict) or not session.get("messages")
            )
            changed = not exists or not bound or str(session.get("mode") or "") != mode or should_snapshot
            if changed:
                now = _now()
                session["session_id"] = session_id
                session["character_id"] = character_id
                session["mode"] = mode
                session.setdefault("title", "新对话")
                session.setdefault("created_at", now)
                session["updated_at"] = str(session.get("updated_at") or now)
                session.setdefault("messages", [])
                if should_snapshot:
                    session["role_state"] = deepcopy(role_state)
                self._store_session(session_id, session)
            return deepcopy(session)

    def bind_unbound(self, character_id: str, *, mode: str = "custom") -> int:
        """Idempotently bind every legacy session that has no character."""

        changed = 0
        sources = (
            [
                (
                    str(value.get("session_id") or key.removeprefix("session:")),
                    value,
                )
                for key, value in self.database.list_documents("session:")
            ]
            if self.database is not None
            else []
        )
        if self.database is None:
            for path in self.root.glob("*.json"):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                sources.append((str(value.get("session_id") or path.stem), value))
        with self._lock:
            for session_id, value in sources:
                if str(value.get("character_id") or ""):
                    continue
                value["character_id"] = character_id
                value["mode"] = mode
                self._store_session(session_id, value)
                changed += 1
        return changed

    def _migrate_legacy_analysis(self) -> None:
        changed: list[tuple[Path, dict[str, Any]]] = []
        for path in self.root.glob("*.json"):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    session = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            dirty = False
            for message in session.get("messages", []):
                if "analysis" in message:
                    message.pop("analysis", None)
                    dirty = True
            if dirty:
                changed.append((path, session))
        if not changed:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_root = self.root.parent / "backups" / "analysis-migration" / stamp
        backup_root.mkdir(parents=True, exist_ok=True)
        for path, session in changed:
            shutil.copy2(path, backup_root / path.name)
            self._store_session(str(session.get("session_id") or path.stem), session)

    def _read_receipts(self) -> dict[str, Any]:
        if self.database is not None:
            value = self.database.get_document("session_receipts", {})
            return value if isinstance(value, dict) else {}
        try:
            with self.receipts_path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _read_events(self) -> list[dict[str, Any]]:
        if self.database is not None:
            value = self.database.get_document("session_deletion_events", [])
            return value if isinstance(value, list) else []
        try:
            with self.events_path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def load_recent(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        visible = [item for item in self.load_session(session_id).get("messages", []) if not item.get("hidden")]
        return visible[-limit:]

    def load_all(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.load_session(session_id).get("messages", []))

    def load_pending_deletions(self, session_id: str) -> list[DeletionEvent]:
        with self._lock:
            return [
                DeletionEvent.model_validate(item)
                for item in self._read_events()
                if item.get("session_id") == session_id and item.get("status") == "pending"
            ]

    def resolve_deletions(self, event_ids: list[str]) -> None:
        if not event_ids:
            return
        with self._lock:
            ids = set(event_ids)
            events = self._read_events()
            changed = False
            for event in events:
                if event.get("event_id") in ids and event.get("status") == "pending":
                    event["status"] = "resolved"
                    event["resolved_at"] = _now()
                    changed = True
            if changed:
                self._store_events(events)

    def persist_turn(
        self,
        request: ChatRequest,
        reply: str,
        *,
        replace_round: bool,
        write_receipt: JsonWriteReceipt,
        tool_execution: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        with self._lock:
            session = self.load_session(request.session_id)
            bound_character = str(session.get("character_id") or "")
            if request.character_id and bound_character != request.character_id:
                raise ValueError("session character binding changed during the turn")
            messages = session.setdefault("messages", [])
            replaced_ids: set[str | None] = set()
            if replace_round:
                replaced_ids = {item.get("message_id") for item in messages if item.get("round") == request.round}
                messages[:] = [item for item in messages if item.get("round") != request.round]
            user_timestamp = request.server_received_at.isoformat()
            assistant_timestamp = _now()
            user_message_id = uuid4().hex
            assistant_message_id = uuid4().hex
            role_quality = evaluate_roleplay_quality(reply, request, messages)
            presentation_mode = resolve_presentation_mode(request, messages)
            messages.extend(
                [
                    {
                        "message_id": user_message_id,
                        "role": "user",
                        "content": request.message,
                        "round": request.round,
                        "status": "complete",
                        "timestamp": user_timestamp,
                        "timestamp_source": "server_received_at",
                        "timing": {
                            "client_sent_at": (
                                request.client_sent_at.isoformat() if request.client_sent_at is not None else None
                            ),
                            "server_received_at_utc": user_timestamp,
                        },
                        "hidden": request.initiative,
                        "kind": "initiative_signal" if request.initiative else "message",
                        "initiative_trigger": request.initiative_trigger,
                        "retrieval_class": ("initiative_signal" if request.initiative else "user_dialogue"),
                        "adult_mode": request.adult_mode,
                        "companion_lane": companion_lane(request),
                        "presentation_mode": presentation_mode,
                        "reply_to_message_id": request.reply_to_message_id,
                        "interactions": [item.model_dump(mode="json") for item in request.interactions],
                        "attachments": [
                            item.model_dump(mode="json", exclude={"content"})
                            for item in request.attachments
                        ],
                    },
                    {
                        "message_id": assistant_message_id,
                        "role": "assistant",
                        "content": reply,
                        "round": request.round,
                        "status": "complete",
                        "timestamp": assistant_timestamp,
                        "timestamp_source": "server_completed_at",
                        "timing": {
                            "request_received_at_utc": user_timestamp,
                            "assistant_completed_at_utc": assistant_timestamp,
                        },
                        "kind": "initiative_response" if request.initiative else "message",
                        "initiative_trigger": request.initiative_trigger,
                        "retrieval_class": ("raw_initiative" if request.initiative else "raw_assistant"),
                        "adult_mode": request.adult_mode,
                        "companion_lane": companion_lane(request),
                        "presentation_mode": presentation_mode,
                        "role_quality": role_quality["quality"],
                        "role_quality_reasons": role_quality["reasons"],
                        "role_quality_correction": role_quality["correction"],
                        "tool_execution": deepcopy(tool_execution) if tool_execution else None,
                    },
                ]
            )
            if session.get("title") == "新对话":
                interaction_title = "、".join(
                    item.action + (f"-{item.target}" if item.target else "")
                    for item in request.interactions[:2]
                )
                attachment_title = request.attachments[0].name if request.attachments else ""
                session["title"] = (
                    f"{request.character_name}的主动问候"
                    if request.initiative
                    else (request.message[:28] or interaction_title or attachment_title or "新互动")
                )
            session["updated_at"] = assistant_timestamp
            self._store_session(request.session_id, session)
            receipts = self._read_receipts()
            receipts = {key: value for key, value in receipts.items() if key not in replaced_ids}
            receipts[assistant_message_id] = write_receipt.model_dump(mode="json")
            self._store_receipts(receipts)
            return {
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
            }

    def list_sessions(self) -> list[dict[str, Any]]:
        items = []
        sources = (
            [
                (self._path(str(value.get("session_id") or key.removeprefix("session:"))), value)
                for key, value in self.database.list_documents("session:")
            ]
            if self.database is not None
            else [(path, None) for path in self.root.glob("*.json")]
        )
        for path, stored in sources:
            try:
                if isinstance(stored, dict):
                    value = stored
                else:
                    with path.open("r", encoding="utf-8") as handle:
                        value = json.load(handle)
                session_id = str(value.get("session_id") or path.stem)
                if path != self._path(session_id) and self._path(session_id).exists():
                    continue
                items.append(
                    {
                        "session_id": session_id,
                        "title": value.get("title", "未命名对话"),
                        "character_id": value.get("character_id", ""),
                        "mode": value.get("mode", "custom"),
                        "updated_at": value.get("updated_at", ""),
                        "message_count": sum(1 for item in value.get("messages", []) if not item.get("hidden")),
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def delete_session(self, session_id: str) -> bool:
        path = self._path(session_id)
        legacy_path = self._owned_legacy_path(session_id)
        exists = (
            self.database.has_document(self._session_key(session_id))
            if self.database
            else path.exists() or legacy_path is not None
        )
        if not exists:
            return False
        with self._lock:
            session = self.load_session(session_id)
            original_messages = list(session.get("messages", []))
            message_ids = {item.get("message_id") for item in original_messages}
            if self.database is not None:
                self.database.delete_document(self._session_key(session_id))
                self.database.defer_projection(lambda: path.unlink(missing_ok=True))
                if legacy_path is not None:
                    self.database.defer_projection(lambda: legacy_path.unlink(missing_ok=True))
            else:
                path.unlink(missing_ok=True)
                if legacy_path is not None:
                    legacy_path.unlink(missing_ok=True)
            receipts = self._read_receipts()
            receipts = {key: value for key, value in receipts.items() if key not in message_ids}
            self._store_receipts(receipts)
            events = [item for item in self._read_events() if item.get("session_id") != session_id]
            self._store_events(events)
        return True

    def delete_message(self, session_id: str, message_id: str) -> DeletionEvent | None:
        with self._lock:
            session = self.load_session(session_id)
            messages = session.get("messages", [])
            target = next(
                (item for item in messages if item.get("message_id") == message_id and item.get("role") == "assistant"),
                None,
            )
            if target is None:
                return None
            initiative = target.get("kind") == "initiative_response"
            target_round = int(target.get("round", 0))
            session["messages"] = [
                item
                for item in messages
                if item.get("message_id") != message_id
                and not (
                    initiative
                    and item.get("hidden")
                    and item.get("kind") == "initiative_signal"
                    and int(item.get("round", 0)) == target_round
                )
            ]
            session["updated_at"] = _now()
            receipts = self._read_receipts()
            receipt = receipts.pop(message_id, {})
            event = DeletionEvent(
                session_id=session_id,
                turn_id=str(receipt.get("turn_id") or f"round_{target.get('round', 0)}"),
                round=int(target.get("round", 0)),
                message_id=message_id,
                deleted_content=str(target.get("content") or ""),
                associated_write_receipt=receipt,
                status="resolved" if initiative else "pending",
            )
            events = self._read_events()
            if not initiative:
                events.append(event.model_dump(mode="json"))
            self._store_session(session_id, session)
            self._store_receipts(receipts)
            self._store_events(events)
            return event

    def delete_round(self, session_id: str, round_num: int) -> bool:
        with self._lock:
            session = self.load_session(session_id)
            messages = session.get("messages", [])
            removed_ids = {item.get("message_id") for item in messages if int(item.get("round", 0)) == round_num}
            retained = [item for item in messages if int(item.get("round", 0)) != round_num]
            if len(retained) == len(messages):
                return False
            session["messages"] = retained
            session["updated_at"] = _now()
            receipts = self._read_receipts()
            events = self._read_events()
            for item in messages:
                if int(item.get("round", 0)) != round_num or item.get("role") != "assistant":
                    continue
                message_id = str(item.get("message_id") or "")
                receipt = receipts.get(message_id, {})
                events.append(
                    DeletionEvent(
                        session_id=session_id,
                        turn_id=str(receipt.get("turn_id") or f"round_{round_num}"),
                        round=round_num,
                        message_id=message_id,
                        deleted_content=str(item.get("content") or ""),
                        associated_write_receipt=receipt,
                    ).model_dump(mode="json")
                )
            receipts = {key: value for key, value in receipts.items() if key not in removed_ids}
            self._store_session(session_id, session)
            self._store_receipts(receipts)
            self._store_events(events)
            return True

    def clear_session(self, session_id: str) -> bool:
        path = self._path(session_id)
        exists = (
            self.database.has_document(self._session_key(session_id))
            if self.database
            else path.exists() or self._owned_legacy_path(session_id) is not None
        )
        if not exists:
            return False
        with self._lock:
            session = self.load_session(session_id)
            original_messages = list(session.get("messages", []))
            message_ids = {item.get("message_id") for item in original_messages}
            session["messages"] = []
            session["updated_at"] = _now()
            receipts = self._read_receipts()
            events = self._read_events()
            for item in original_messages:
                if item.get("role") != "assistant":
                    continue
                message_id = str(item.get("message_id") or "")
                receipt = receipts.get(message_id, {})
                events.append(
                    DeletionEvent(
                        session_id=session_id,
                        turn_id=str(receipt.get("turn_id") or f"round_{item.get('round', 0)}"),
                        round=int(item.get("round", 0)),
                        message_id=message_id,
                        deleted_content=str(item.get("content") or ""),
                        associated_write_receipt=receipt,
                    ).model_dump(mode="json")
                )
            receipts = {key: value for key, value in receipts.items() if key not in message_ids}
            self._store_session(session_id, session)
            self._store_receipts(receipts)
            self._store_events(events)
        return True

    def clear_all(self) -> int:
        removed = 0
        with self._lock:
            if self.database is not None:
                removed = self.database.delete_prefix("session:")
                paths = list(self.root.glob("*.json"))
                self.database.defer_projection(lambda: [path.unlink(missing_ok=True) for path in paths])
            else:
                for path in self.root.glob("*.json"):
                    path.unlink()
                    removed += 1
            self._store_receipts({})
            self._store_events([])
        return removed

    def list_chunks(self, session_id: str | None = None) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        if self.database is not None:
            sources = (
                [(self._path(session_id), self.load_session(session_id))]
                if session_id
                else [
                    (
                        self._path(str(value.get("session_id") or key.removeprefix("session:"))),
                        value,
                    )
                    for key, value in self.database.list_documents("session:")
                ]
            )
        else:
            sources = [
                (path, None) for path in ([self._path(session_id)] if session_id else list(self.root.glob("*.json")))
            ]
        for path, stored in sources:
            if isinstance(stored, dict):
                session = stored
            else:
                if not path.exists():
                    continue
                with path.open("r", encoding="utf-8") as handle:
                    session = json.load(handle)
            sid = str(session.get("session_id", path.stem))
            if path != self._path(sid) and self._path(sid).exists():
                continue
            for index, message in enumerate(session.get("messages", [])):
                if not chat_message_retrieval_eligible(message):
                    continue
                chunks.append(
                    {
                        "chunk_id": f"{sid}:{index}",
                        "session_id": sid,
                        "character_id": str(session.get("character_id") or ""),
                        "round": message.get("round", 0),
                        "role": message.get("role", "unknown"),
                        "text": message.get("content", ""),
                        "created_at": message.get("timestamp", ""),
                        "adult_mode": bool(message.get("adult_mode")),
                        "companion_lane": str(message.get("companion_lane") or "DAILY"),
                    }
                )
        return chunks

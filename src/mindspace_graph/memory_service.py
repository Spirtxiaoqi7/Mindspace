"""Application service for user-visible structured memory operations."""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from copy import deepcopy
from threading import RLock
from typing import Any
from uuid import uuid4

from mindspace_graph.adapters.profile_repository import JsonProfileRepository
from mindspace_graph.adapters.structured_memory import StructuredMemoryStore
from mindspace_graph.entity_registry import EntityRegistry
from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY, MemoryField, MemoryRegistry
from mindspace_graph.models import ChatRequest, JsonWriteReceipt
from mindspace_graph.product_database import ProductDatabase


def _normalize(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value.strip().casefold())
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for token in path.strip("/").split("/"):
        current = current[token]
    return current


def _write(document: dict[str, Any], path: str, value: Any) -> None:
    tokens = path.strip("/").split("/")
    current: Any = document
    for token in tokens[:-1]:
        current = current[token]
    current[tokens[-1]] = value


class StructuredMemoryService:
    def __init__(
        self,
        profiles: JsonProfileRepository,
        store: StructuredMemoryStore,
        registry: MemoryRegistry = DEFAULT_MEMORY_REGISTRY,
        database: ProductDatabase | None = None,
        entity_registry: EntityRegistry | None = None,
    ) -> None:
        self.profiles = profiles
        self.store = store
        self.registry = registry
        self.database = database
        self.entity_registry = entity_registry
        self._lock = RLock()

    def list_items(self, *, include_history: bool = False, character_id: str = "") -> list[dict[str, Any]]:
        return self.store.list_items(include_history=include_history, character_id=character_id)

    def rebuild(self, *, dry_run: bool = False, character_id: str = "") -> dict[str, int | bool]:
        """Recreate structured bindings from the current authoritative profiles."""

        all_character_ids = (
            [str(item["character_id"]) for item in self.profiles.characters.list()]
            if self.profiles.characters is not None
            else []
        )
        if character_id and character_id not in all_character_ids:
            raise KeyError("character not found")
        character_ids = [character_id] if character_id else all_character_ids
        groups: dict[str, list[dict[str, Any]]] = {character_id: []} if character_id else {"": []}
        for field in self.registry.fields:
            if field.target == "user_profile":
                owners = [] if character_id else [""]
            elif field.target == "character_memory":
                owners = character_ids
            else:
                # V2 characters no longer project personality, boundary, or runtime
                # fields into structured memory.
                continue
            for owner in owners:
                document = self.profiles.load_document(field.target, owner)
                value = deepcopy(_read(document, field.path))
                values = value if field.value_kind == "list" else [value]
                for item in values:
                    if item in (None, "", [], {}):
                        continue
                    groups.setdefault(owner, []).append(
                        self._patch(
                            field,
                            "add" if field.value_kind == "list" else "replace",
                            f"{field.path}/-" if field.value_kind == "list" else field.path,
                            None,
                            item,
                        )
                    )
        patches = [item for values in groups.values() for item in values]
        if dry_run:
            return {
                "dry_run": True,
                "bindings": len(patches),
                "fields": len({p["path"].rsplit("/", 1)[0] for p in patches}),
            }
        identifier = uuid4().hex
        transaction = (
            self.database.transaction(operation="rebuild_structured_memory")
            if self.database is not None
            else nullcontext()
        )
        with transaction:
            if character_id:
                self.store.reset_character(character_id)
            else:
                self.store.reset()
            for owner_index, (owner, owner_patches) in enumerate(groups.items()):
                if not owner_patches:
                    continue
                owner_identifier = f"{identifier}-{owner_index}"
                profile = (
                    self.profiles.load_document("ai_profile", owner) if owner else {"identity": {"name": "Mindspace"}}
                )
                self.store.record_turn(
                    ChatRequest(
                        message="依据当前权威档案重建结构化记忆索引。",
                        session_id="memory-rebuild",
                        character_id=owner,
                        round=1,
                        character_name=str(profile.get("identity", {}).get("name") or "Mindspace"),
                    ),
                    "结构化记忆索引已完成确定性重建。",
                    persisted={
                        "user_message_id": f"memory-rebuild-user-{owner_identifier}",
                        "assistant_message_id": f"memory-rebuild-{owner_identifier}",
                    },
                    write_receipt=JsonWriteReceipt(
                        turn_id=f"memory_rebuild_{owner_identifier}",
                        applied=True,
                        patches=owner_patches,
                    ),
                )
        return {
            "dry_run": False,
            "bindings": len(patches),
            "active": len(self.store.snapshot()["active"]),
        }

    def update(self, memory_key: str, value: Any) -> dict[str, Any]:
        if self.database is not None:
            with self.database.transaction(operation="update_memory", details={"memory_key": memory_key}):
                return self._update(memory_key, value)
        return self._update(memory_key, value)

    def _update(self, memory_key: str, value: Any) -> dict[str, Any]:
        active = self._active(memory_key)
        field = self._field(active)
        normalized = self._validate_value(field, value)
        owner = str(active.get("character_id") or "")
        patches = self._mutate_profile(field, normalized, previous=active.get("value"), character_id=owner)
        self._record_manual(
            field,
            patches,
            f"将“{field.display_name}”修改为：{normalized}",
            character_id=owner,
        )
        return self._find_current(field, normalized, character_id=owner)

    def delete(self, memory_key: str) -> bool:
        if self.database is not None:
            with self.database.transaction(operation="delete_memory", details={"memory_key": memory_key}):
                return self._delete(memory_key)
        return self._delete(memory_key)

    def _delete(self, memory_key: str) -> bool:
        active = self._active(memory_key)
        field = self._field(active)
        owner = str(active.get("character_id") or "")
        with self._lock:
            document = self.profiles.load_document(field.target, owner)
            if field.value_kind == "scalar":
                _write(document, field.path, "")
            else:
                values = list(_read(document, field.path))
                values = [item for item in values if not self._equivalent(field, item, active.get("value"))]
                _write(document, field.path, values)
            self.profiles.save_document(field.target, document, owner)
            return self.store.invalidate_key(memory_key, reason="user_deleted")

    def restore(self, memory_key: str) -> dict[str, Any]:
        if self.database is not None:
            with self.database.transaction(operation="restore_memory", details={"memory_key": memory_key}):
                return self._restore(memory_key)
        return self._restore(memory_key)

    def _restore(self, memory_key: str) -> dict[str, Any]:
        tombstone = self.store.latest_invalidated(memory_key)
        if tombstone is None:
            raise KeyError("memory history not found")
        record = tombstone.get("removed_record", {})
        field = self._field(record)
        owner = str(record.get("character_id") or "")
        value = self._validate_value(field, record.get("value"))
        patches = self._mutate_profile(field, value, previous=None, character_id=owner)
        self._record_manual(
            field,
            patches,
            f"恢复“{field.display_name}”：{value}",
            character_id=owner,
        )
        return self._find_current(field, value, character_id=owner)

    def _mutate_profile(
        self,
        field: MemoryField,
        value: Any,
        *,
        previous: Any,
        character_id: str = "",
    ) -> list[dict[str, Any]]:
        with self._lock:
            document = self.profiles.load_document(field.target, character_id)
            patches: list[dict[str, Any]] = []
            if field.value_kind == "scalar":
                before = deepcopy(_read(document, field.path))
                _write(document, field.path, deepcopy(value))
                patches.append(self._patch(field, "replace", field.path, before, value))
            else:
                if previous is not None:
                    self._remove_value(document, field, previous, patches)
                if field.conflict_group:
                    for peer in self.registry.fields:
                        if peer.target == field.target and peer.conflict_group == field.conflict_group:
                            self._remove_value(document, peer, value, patches)
                values = list(_read(document, field.path))
                if not any(self._equivalent(field, item, value) for item in values):
                    values.append(deepcopy(value))
                    if len(values) > field.max_items:
                        values = values[-field.max_items :]
                    _write(document, field.path, values)
                    patches.append(self._patch(field, "add", f"{field.path}/-", None, value))
            self.profiles.save_document(field.target, document, character_id)
            return patches

    def _remove_value(
        self,
        document: dict[str, Any],
        field: MemoryField,
        value: Any,
        patches: list[dict[str, Any]],
    ) -> None:
        if field.value_kind != "list":
            return
        values = list(_read(document, field.path))
        for index in range(len(values) - 1, -1, -1):
            if not self._equivalent(field, values[index], value):
                continue
            before = values.pop(index)
            patches.append(StructuredMemoryService._patch(field, "remove", f"{field.path}/{index}", before, None))
        _write(document, field.path, values)

    def _equivalent(self, field: MemoryField, left: Any, right: Any) -> bool:
        if _normalize(left) == _normalize(right):
            return True
        if self.entity_registry is None:
            return False
        entity_type = field.conflict_group or field.field_code
        left_id = self.entity_registry.resolve(left, scope=field.scope, entity_type=entity_type, create=False)
        right_id = self.entity_registry.resolve(right, scope=field.scope, entity_type=entity_type, create=False)
        return bool(left_id and right_id and left_id == right_id)

    @staticmethod
    def _patch(
        field: MemoryField,
        op: str,
        path: str,
        before: Any,
        after: Any,
    ) -> dict[str, Any]:
        return {
            "target": field.target,
            "op": op,
            "path": path,
            "before": deepcopy(before),
            "after": deepcopy(after),
            "evidence_ids": ["memory_center"],
        }

    def _record_manual(
        self,
        field: MemoryField,
        patches: list[dict[str, Any]],
        message: str,
        *,
        character_id: str = "",
    ) -> None:
        if not patches:
            return
        identifier = uuid4().hex
        revision = int(self.profiles.load_document(field.target, character_id).get("revision", 1))
        self.store.record_turn(
            ChatRequest(
                message=message,
                session_id="memory-center",
                character_id=character_id,
                round=max(1, revision),
                character_name="Mindspace",
            ),
            "用户已在记忆中心确认这项修改。",
            persisted={
                "user_message_id": f"memory-center-user-{identifier}",
                "assistant_message_id": f"memory-center-{identifier}",
            },
            write_receipt=JsonWriteReceipt(turn_id=f"memory_center_{identifier}", applied=True, patches=patches),
        )

    @staticmethod
    def _validate_value(field: MemoryField, value: Any) -> Any:
        if field.value_kind == "list" and isinstance(value, (dict, list)):
            raise ValueError("list memory items must be a scalar value")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("memory value must be a string, number, or boolean")
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("memory value must not be blank")
            if len(value) > 1000:
                raise ValueError("memory value exceeds 1000 characters")
        return value

    def _active(self, memory_key: str) -> dict[str, Any]:
        record = self.store.snapshot()["active"].get(memory_key)
        if not isinstance(record, dict):
            raise KeyError("active memory not found")
        return record

    def _field(self, record: dict[str, Any]) -> MemoryField:
        field = self.registry.by_code(str(record.get("field_code") or ""))
        if field is None:
            raise KeyError("memory field is not registered")
        return field

    def _find_current(self, field: MemoryField, value: Any, *, character_id: str = "") -> dict[str, Any]:
        candidates = [
            item
            for item in self.store.list_items(character_id=character_id)
            if item.get("field_code") == field.field_code and self._equivalent(field, item.get("value"), value)
        ]
        if not candidates:
            raise RuntimeError("memory update was persisted but active binding is missing")
        return candidates[0]

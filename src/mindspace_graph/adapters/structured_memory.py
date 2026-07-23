"""Deterministic JSON-tagged memory without model-authored classifications.

The store deliberately separates source text from its JSON field bindings:

* one episode owns the original text;
* one active record represents one committed JSON leaf;
* untagged text is quarantined in a bounded candidate pool and is never indexed;
* recall statistics affect selection fairness only and can never promote a record.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from mindspace_graph.adapters.file_storage import _atomic_json
from mindspace_graph.entity_registry import EntityRegistry
from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY, MemoryRegistry
from mindspace_graph.models import ChatRequest, JsonWriteReceipt, RetrievedChunk
from mindspace_graph.product_database import ProductDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_value(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value.strip().casefold())
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


class StructuredMemoryStore:
    """Persistent, bounded memory classified only by committed JSON paths."""

    def __init__(
        self,
        path: Path,
        *,
        max_untagged: int = 128,
        max_untagged_per_session: int = 24,
        untagged_ttl_days: int = 14,
        registry: MemoryRegistry = DEFAULT_MEMORY_REGISTRY,
        database: ProductDatabase | None = None,
        entity_registry: EntityRegistry | None = None,
    ) -> None:
        self.path = path
        self.database = database
        self.entity_registry = entity_registry
        self.max_untagged = max(1, max_untagged)
        self.max_untagged_per_session = max(1, max_untagged_per_session)
        self.untagged_ttl_days = max(1, untagged_ttl_days)
        self.registry = registry
        self._lock = RLock()
        if self.database is not None:
            if not self.database.has_document("structured_memory"):
                imported = None
                if path.exists():
                    try:
                        imported = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        imported = None
                self._save(imported if isinstance(imported, dict) else self._empty())
            else:
                self._save(self.database.get_document("structured_memory", self._empty()))
        elif not path.exists():
            _atomic_json(path, self._empty())

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "schema_version": "2.0.0",
            "episodes": {},
            "active": {},
            "untagged": [],
            "tombstones": [],
        }

    def _load(self) -> dict[str, Any]:
        if self.database is not None:
            value = self.database.get_document("structured_memory", self._empty())
        else:
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    value = json.load(handle)
            except (OSError, json.JSONDecodeError):
                value = self._empty()
        if not isinstance(value, dict):
            value = self._empty()
        baseline = self._empty()
        for key, default in baseline.items():
            if not isinstance(value.get(key), type(default)):
                value[key] = deepcopy(default)
        return value

    def _save(self, value: dict[str, Any]) -> None:
        if self.database is None:
            _atomic_json(self.path, value)
            return
        self.database.put_document("structured_memory", value)
        snapshot = deepcopy(value)
        self.database.defer_projection(lambda: _atomic_json(self.path, snapshot))

    @staticmethod
    def _episode(
        request: ChatRequest,
        reply: str,
        persisted: dict[str, str],
        timestamp: str,
        patches: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        episode_id = f"episode:{persisted['assistant_message_id']}"
        evidence = {str(item) for patch in patches for item in patch.get("evidence_ids", [])}
        source_lines: list[str] = []
        if "user_setup" in evidence and request.user_persona.strip():
            source_lines.append(f"用户设定：{request.user_persona.strip()}")
        if "character_setup" in evidence and request.system_prompt.strip():
            source_lines.append(f"角色设定：{request.system_prompt.strip()}")
        source_lines.extend([f"用户：{request.message}", f"{request.character_name}：{reply}"])
        text = "\n".join(source_lines)
        return episode_id, {
            "episode_id": episode_id,
            "session_id": request.session_id,
            "round": request.round,
            "user_message_id": persisted["user_message_id"],
            "assistant_message_id": persisted["assistant_message_id"],
            "text": text,
            "created_at": timestamp,
        }

    def _binding(self, patch: dict[str, Any], episode_id: str, timestamp: str) -> dict[str, Any]:
        target = str(patch.get("target") or "")
        raw_path = str(patch.get("path") or "")
        field = self.registry.resolve(target, raw_path)
        if field is None:
            raise ValueError(f"unregistered memory field: {target}:{raw_path}")
        path = field.path
        op = str(patch.get("op") or "replace")
        value = patch.get("after", patch.get("value"))
        if op == "remove":
            value = patch.get("before")
        normalized = _normalize_value(value)
        family_key = f"{field.scope}:{field.conflict_group or field.field_code}"
        has_item_identity = field.value_kind == "list"
        entity_id = (
            self.entity_registry.resolve(
                value,
                scope=field.scope,
                entity_type=field.conflict_group or field.field_code,
            )
            if has_item_identity and self.entity_registry is not None
            else None
        )
        suffix = f":{entity_id or _digest(normalized)}" if has_item_identity else ""
        memory_key = f"{family_key}{suffix}"
        return {
            "memory_key": memory_key,
            "family_key": family_key,
            "episode_id": episode_id,
            "json_tags": [
                {
                    "tag_id": f"json:{target}:{path}",
                    "field_code": field.field_code,
                    "target": target,
                    "path": path,
                    "display_name": field.display_name,
                    "category": field.category,
                    "polarity": field.polarity,
                }
            ],
            "field_code": field.field_code,
            "display_name": field.display_name,
            "category": field.category,
            "scope": field.scope,
            "lifecycle": field.lifecycle,
            "reducer": field.reducer,
            "max_items": field.max_items,
            "operation": op,
            "value": deepcopy(value),
            "value_fingerprint": _digest(normalized),
            "entity_id": entity_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "recall_count": 0,
            "selection_count": 0,
            "eligible_misses": 0,
            "last_selected_round": 0,
        }

    def record_turn(
        self,
        request: ChatRequest,
        reply: str,
        *,
        persisted: dict[str, str],
        write_receipt: JsonWriteReceipt,
    ) -> dict[str, int]:
        """Record text once and bind only server-committed JSON changes."""

        timestamp = _now()
        patches = write_receipt.patches if write_receipt.applied else []
        episode_id, episode = self._episode(request, reply, persisted, timestamp, patches)
        with self._lock:
            data = self._load()
            if patches:
                data["episodes"][episode_id] = episode
                bindings = [
                    self._binding(raw_patch, episode_id, timestamp) for raw_patch in patches
                ]
                # When one turn removes and adds opposite values for the same slot,
                # the positive final binding wins regardless of patch ordering.
                resolved: dict[str, dict[str, Any]] = {}
                for binding in bindings:
                    current = resolved.get(binding["memory_key"])
                    if current is None or binding["operation"] != "remove":
                        resolved[binding["memory_key"]] = binding
                for binding in resolved.values():
                    memory_key = binding["memory_key"]
                    if binding["operation"] == "remove":
                        self._invalidate(data, memory_key, episode_id, timestamp, "removed")
                    else:
                        previous = data["active"].get(memory_key)
                        if previous:
                            binding["created_at"] = previous.get("created_at", timestamp)
                            binding["recall_count"] = int(previous.get("recall_count", 0))
                            binding["selection_count"] = int(previous.get("selection_count", 0))
                            binding["eligible_misses"] = int(previous.get("eligible_misses", 0))
                            binding["last_selected_round"] = int(
                                previous.get("last_selected_round", 0)
                            )
                            self._invalidate(data, memory_key, episode_id, timestamp, "superseded")
                        data["active"][memory_key] = binding
                self._enforce_active_limits(data, timestamp)
            else:
                self._record_untagged(data, episode_id, episode, timestamp)
            self._prune(data, now=datetime.fromisoformat(timestamp))
            self._save(data)
            return {
                "active": len(data["active"]),
                "untagged": len(data["untagged"]),
                "episodes": len(data["episodes"]),
            }

    @staticmethod
    def _invalidate(
        data: dict[str, Any],
        memory_key: str,
        episode_id: str,
        timestamp: str,
        reason: str,
    ) -> None:
        removed = data["active"].pop(memory_key, None)
        if removed is None:
            return
        source_episode = deepcopy(data["episodes"].get(removed.get("episode_id"), {}))
        data["tombstones"].append(
            {
                "memory_key": memory_key,
                "episode_id": episode_id,
                "invalidated_at": timestamp,
                "reason": reason,
                "removed_record": removed,
                "source_episode": source_episode,
            }
        )

    def _enforce_active_limits(self, data: dict[str, Any], timestamp: str) -> None:
        by_field: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for key, record in data["active"].items():
            by_field.setdefault(str(record.get("field_code") or ""), []).append((key, record))
        for field_code, records in by_field.items():
            field = self.registry.by_code(field_code)
            if field is None or len(records) <= field.max_items:
                continue
            records.sort(key=lambda item: str(item[1].get("updated_at") or ""), reverse=True)
            for key, _record in records[field.max_items :]:
                self._invalidate(data, key, "", timestamp, "field_limit")

    def _record_untagged(
        self,
        data: dict[str, Any],
        episode_id: str,
        episode: dict[str, Any],
        timestamp: str,
    ) -> None:
        content_hash = _digest(str(episode["text"]))
        existing = next(
            (item for item in data["untagged"] if item.get("content_hash") == content_hash),
            None,
        )
        if existing:
            existing["last_seen_at"] = timestamp
            existing["repeat_count"] = int(existing.get("repeat_count", 1)) + 1
            return
        data["episodes"][episode_id] = episode
        data["untagged"].append(
            {
                "episode_id": episode_id,
                "session_id": episode["session_id"],
                "content_hash": content_hash,
                "first_seen_at": timestamp,
                "last_seen_at": timestamp,
                "repeat_count": 1,
                "expires_at": (
                    datetime.fromisoformat(timestamp) + timedelta(days=self.untagged_ttl_days)
                ).isoformat(),
            }
        )

    def _prune(self, data: dict[str, Any], *, now: datetime) -> None:
        untagged = [
            item for item in data["untagged"] if self._parse_time(item.get("expires_at")) > now
        ]
        untagged.sort(key=lambda item: str(item.get("last_seen_at", "")), reverse=True)
        per_session: dict[str, int] = {}
        retained = []
        for item in untagged:
            session_id = str(item.get("session_id") or "")
            if per_session.get(session_id, 0) >= self.max_untagged_per_session:
                continue
            if len(retained) >= self.max_untagged:
                continue
            per_session[session_id] = per_session.get(session_id, 0) + 1
            retained.append(item)
        data["untagged"] = retained
        data["tombstones"] = data["tombstones"][-128:]
        referenced = {str(item.get("episode_id")) for item in data["active"].values()} | {
            str(item.get("episode_id")) for item in retained
        }
        data["episodes"] = {
            key: value for key, value in data["episodes"].items() if key in referenced
        }

    @staticmethod
    def _parse_time(raw: Any) -> datetime:
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self._load()
        result = []
        for record in data["active"].values():
            episode = data["episodes"].get(record.get("episode_id"), {})
            if episode.get("text"):
                result.append({**deepcopy(record), "episode": deepcopy(episode)})
        return result

    def list_items(self, *, include_history: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            data = self._load()
        items: list[dict[str, Any]] = []
        for record in data["active"].values():
            episode = data["episodes"].get(record.get("episode_id"), {})
            items.append(self._public_item(record, episode, "active"))
        if include_history:
            for tombstone in reversed(data["tombstones"]):
                record = tombstone.get("removed_record")
                if not isinstance(record, dict):
                    continue
                item = self._public_item(
                    record,
                    tombstone.get("source_episode", {}),
                    "invalidated",
                )
                item.update(
                    {
                        "invalidated_at": tombstone.get("invalidated_at", ""),
                        "reason": tombstone.get("reason", ""),
                    }
                )
                items.append(item)
        return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    @staticmethod
    def _public_item(
        record: dict[str, Any], episode: dict[str, Any], status: str
    ) -> dict[str, Any]:
        text = str(episode.get("text") or "")
        return {
            "memory_key": record.get("memory_key", ""),
            "field_code": record.get("field_code", ""),
            "display_name": record.get("display_name", ""),
            "category": record.get("category", ""),
            "value": deepcopy(record.get("value")),
            "scope": record.get("scope", ""),
            "lifecycle": record.get("lifecycle", ""),
            "status": status,
            "created_at": record.get("created_at", ""),
            "updated_at": record.get("updated_at", ""),
            "session_id": episode.get("session_id", ""),
            "assistant_message_id": episode.get("assistant_message_id", ""),
            "source_text": text[:600],
        }

    def invalidate_key(self, memory_key: str, *, reason: str = "user_deleted") -> bool:
        with self._lock:
            data = self._load()
            if memory_key not in data["active"]:
                return False
            self._invalidate(data, memory_key, "", _now(), reason)
            self._prune(data, now=datetime.now(UTC))
            self._save(data)
            return True

    def latest_invalidated(self, memory_key: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._load()
        return next(
            (
                deepcopy(item)
                for item in reversed(data["tombstones"])
                if item.get("memory_key") == memory_key
                and isinstance(item.get("removed_record"), dict)
            ),
            None,
        )

    def set_episode_embedding(self, episode_id: str, embedding: list[float]) -> None:
        """Cache one vector on the shared episode instead of duplicating it per tag."""

        self.set_episode_embeddings({episode_id: embedding})

    def set_episode_embeddings(self, embeddings: dict[str, list[float]]) -> None:
        """Persist a retrieval batch with one load and one atomic projection write."""

        with self._lock:
            data = self._load()
            changed = False
            for episode_id, embedding in embeddings.items():
                episode = data["episodes"].get(episode_id)
                if episode is None or episode.get("embedding") == embedding:
                    continue
                episode["embedding"] = embedding
                changed = True
            if changed:
                self._save(data)

    def record_retrieval(
        self,
        candidates: list[RetrievedChunk],
        selected: list[RetrievedChunk],
        current_round: int,
    ) -> None:
        candidate_keys = {
            str(item.metadata.get("memory_key"))
            for item in candidates
            if item.source == "memory" and item.metadata.get("memory_key")
        }
        selected_keys = {
            str(item.metadata.get("memory_key"))
            for item in selected
            if item.source == "memory" and item.metadata.get("memory_key")
        }
        if not candidate_keys:
            return
        with self._lock:
            data = self._load()
            for key in candidate_keys:
                record = data["active"].get(key)
                if not record:
                    continue
                record["recall_count"] = int(record.get("recall_count", 0)) + 1
                if key in selected_keys:
                    record["selection_count"] = int(record.get("selection_count", 0)) + 1
                    record["eligible_misses"] = 0
                    record["last_selected_round"] = current_round
                else:
                    record["eligible_misses"] = min(100, int(record.get("eligible_misses", 0)) + 1)
            self._save(data)

    def forget_message(self, message_id: str) -> int:
        return self._forget(lambda episode: episode.get("assistant_message_id") == message_id)

    def forget_session(self, session_id: str, round_num: int | None = None) -> int:
        return self._forget(
            lambda episode: (
                episode.get("session_id") == session_id
                and (round_num is None or int(episode.get("round", 0)) == round_num)
            )
        )

    def _forget(self, predicate: Any) -> int:
        with self._lock:
            data = self._load()
            forgotten = {
                episode_id for episode_id, episode in data["episodes"].items() if predicate(episode)
            }
            if not forgotten:
                return 0
            data["active"] = {
                key: value
                for key, value in data["active"].items()
                if value.get("episode_id") not in forgotten
            }
            data["untagged"] = [
                item for item in data["untagged"] if item.get("episode_id") not in forgotten
            ]
            for episode_id in forgotten:
                data["episodes"].pop(episode_id, None)
            self._save(data)
            return len(forgotten)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._load())

    def reset(self) -> None:
        with self._lock:
            self._save(self._empty())

    def migrate_entity_identities(self) -> dict[str, int]:
        """Upgrade legacy value-hash keys without changing remembered content."""

        if self.entity_registry is None:
            return {"migrated": 0, "collisions": 0}
        migrated = 0
        collisions = 0
        with self._lock:
            data = self._load()
            rebuilt: dict[str, dict[str, Any]] = {}
            for old_key, record in list(data["active"].items()):
                field = self.registry.by_code(str(record.get("field_code") or ""))
                if field is None or field.value_kind != "list":
                    rebuilt[old_key] = record
                    continue
                entity_id = self.entity_registry.resolve(
                    record.get("value"),
                    scope=field.scope,
                    entity_type=field.conflict_group or field.field_code,
                )
                if entity_id is None:
                    rebuilt[old_key] = record
                    continue
                new_key = f"{record['family_key']}:{entity_id}"
                updated = deepcopy(record)
                updated["entity_id"] = entity_id
                updated["memory_key"] = new_key
                previous = rebuilt.get(new_key)
                if previous is not None:
                    collisions += 1
                    if str(previous.get("updated_at") or "") > str(updated.get("updated_at") or ""):
                        continue
                rebuilt[new_key] = updated
                migrated += int(new_key != old_key or not record.get("entity_id"))
            data["active"] = rebuilt
            data["schema_version"] = "2.0.0"
            if migrated or str(self._load().get("schema_version")) != "2.0.0":
                self._save(data)
        return {"migrated": migrated, "collisions": collisions}

    def stats(self) -> dict[str, int]:
        data = self.snapshot()
        return {
            "active": len(data["active"]),
            "untagged": len(data["untagged"]),
            "episodes": len(data["episodes"]),
            "tombstones": len(data["tombstones"]),
        }

"""File-backed chat session repository."""

from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from mindspace_graph.infrastructure.storage.json_io import atomic_json_write
from mindspace_graph.infrastructure.storage.metadata import utc_now_iso
from mindspace_graph.infrastructure.storage.paths import hashed_json_document_path, legacy_json_document_path
from mindspace_graph.models import ChatRequest, DeletionEvent, JsonWriteReceipt
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.roleplay import (
    chat_message_retrieval_eligible,
    companion_lane,
    evaluate_roleplay_quality,
    resolve_presentation_mode,
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
                atomic_json_write(self.receipts_path, {})
            if not self.events_path.exists():
                atomic_json_write(self.events_path, [])
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
            atomic_json_write(path, value)
            return
        self.database.put_document(self._session_key(session_id), value)
        snapshot = deepcopy(value)
        self.database.defer_projection(lambda: atomic_json_write(path, snapshot))

    def _store_receipts(self, value: dict[str, Any]) -> None:
        if self.database is None:
            atomic_json_write(self.receipts_path, value)
            return
        self.database.put_document("session_receipts", value)
        snapshot = deepcopy(value)
        self.database.defer_projection(lambda: atomic_json_write(self.receipts_path, snapshot))

    def _store_events(self, value: list[dict[str, Any]]) -> None:
        if self.database is None:
            atomic_json_write(self.events_path, value)
            return
        self.database.put_document("session_deletion_events", value)
        snapshot = deepcopy(value)
        self.database.defer_projection(lambda: atomic_json_write(self.events_path, snapshot))

    def _path(self, session_id: str) -> Path:
        return hashed_json_document_path(
            self.root,
            session_id,
            unsafe=self.SAFE_ID,
            fallback="session",
            stem_limit=48,
        )

    def _legacy_path(self, session_id: str) -> Path:
        return legacy_json_document_path(
            self.root,
            session_id,
            unsafe=self.SAFE_ID,
            fallback="session",
        )

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
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "messages": [],
            }
        if isinstance(stored, dict):
            session = stored
        else:
            with self._lock, path.open("r", encoding="utf-8") as handle:
                session = json.load(handle)
        timestamp_dirty = False
        fallback_timestamp = str(session.get("created_at") or session.get("updated_at") or utc_now_iso())
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
                now = utc_now_iso()
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
                    event["resolved_at"] = utc_now_iso()
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
            assistant_timestamp = utc_now_iso()
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
            session["updated_at"] = utc_now_iso()
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
            session["updated_at"] = utc_now_iso()
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
            session["updated_at"] = utc_now_iso()
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


__all__ = ["JsonSessionRepository"]

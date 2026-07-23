"""Short-lived, read-only inspection of the exact messages sent to the main model."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from typing import Any

from mindspace_graph.product_database import ProductDatabase


class PromptInspectionStore:
    """Keep full prompts in memory while persisting only hashes and layer sizes."""

    def __init__(
        self,
        database: ProductDatabase | None = None,
        *,
        max_runs: int = 10,
        ttl_seconds: int = 1800,
    ) -> None:
        self.database = database
        self.max_runs = max_runs
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = RLock()

    @staticmethod
    def _layer_name(index: int, base_count: int, pending: list[dict[str, Any]]) -> str:
        if index == 0:
            return "persona_system"
        if index == 1:
            return "contract_system"
        if index == 2:
            return "authoritative_profile"
        pending_index = index - base_count
        if 0 <= pending_index < len(pending):
            return str(pending[pending_index].get("kind") or "turn_context")
        return "conversation_history"

    def _purge(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        for key in [
            key for key, item in self._items.items() if item["stored_at"] < cutoff
        ]:
            self._items.pop(key, None)
        while len(self._items) > self.max_runs:
            self._items.popitem(last=False)

    def record(
        self,
        *,
        run_id: str,
        session_id: str,
        messages: list[dict[str, str]],
        pending_events: list[dict[str, Any]],
    ) -> None:
        if not run_id:
            return
        base_count = max(0, len(messages) - len(pending_events))
        layers = []
        for index, message in enumerate(messages):
            content = str(message.get("content") or "")
            layers.append(
                {
                    "index": index,
                    "layer": self._layer_name(index, base_count, pending_events),
                    "role": str(message.get("role") or ""),
                    "chars": len(content),
                    "estimated_tokens": max(1, len(content) // 2) if content else 0,
                    "content": content,
                }
            )
        encoded = json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        metadata = {
            "run_id": run_id,
            "session_id": session_id,
            "message_count": len(messages),
            "total_chars": sum(item["chars"] for item in layers),
            "estimated_tokens": sum(item["estimated_tokens"] for item in layers),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "layers": layers,
            "stored_at": time.monotonic(),
        }
        with self._lock:
            self._purge()
            self._items[run_id] = metadata
            self._items.move_to_end(run_id)
            self._purge()
        if self.database is not None:
            self.database.record_prompt_inspection_metadata(
                run_id=run_id,
                session_id=session_id,
                sha256=metadata["sha256"],
                layers=[
                    {key: value for key, value in item.items() if key != "content"}
                    for item in layers
                ],
            )

    def get(self, run_id: str, *, reveal: bool = False) -> dict[str, Any] | None:
        with self._lock:
            self._purge()
            item = self._items.get(run_id)
            if item is None:
                return None
            self._items.move_to_end(run_id)
            value = deepcopy(item)
        value.pop("stored_at", None)
        if not reveal:
            for layer in value["layers"]:
                layer["content"] = f"[已脱敏：{layer['chars']} 字符]"
        value["revealed"] = reveal
        return value

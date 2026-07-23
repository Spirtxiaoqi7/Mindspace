"""Append-only JSONL audit adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any


class JsonlAudit:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def record(self, event: str, payload: dict[str, Any]) -> None:
        safe_payload = self._redact(payload)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "payload": safe_payload,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "***"
                if key.lower() in {"api_key", "authorization", "token"}
                else cls._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value

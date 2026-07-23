"""Versioned, forward-compatible validation for authoritative profile JSON."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any

from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY

PROFILE_TYPES = {
    "user_profile": "user",
    "ai_profile": "ai",
    "runtime_state": "runtime_state",
}

REQUIRED_SECTIONS = {
    "user_profile": {
        "identity": dict,
        "communication_preferences": dict,
        "stable_preferences": dict,
        "background": dict,
        "behavior_requirements": dict,
    },
    "ai_profile": {
        "identity": dict,
        "personality": dict,
        "relationship_rules": dict,
        "behavior_rules": dict,
        "continuity": dict,
    },
    "runtime_state": {
        "relationship_state": dict,
        "user_state": dict,
        "ai_state": dict,
        "session_state": dict,
    },
}


def _read(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for token in path.strip("/").split("/"):
        current = current[token]
    return current


def _validate_safe_json(value: Any, *, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("profile nesting exceeds 12 levels")
    if isinstance(value, dict):
        if len(value) > 500:
            raise ValueError("profile object has too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 200:
                raise ValueError("profile field names must be 1-200 character strings")
            _validate_safe_json(item, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 1000:
            raise ValueError("profile list has too many items")
        for item in value:
            _validate_safe_json(item, depth=depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("profile numbers must be finite")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"unsupported profile value type: {type(value).__name__}")


class ProfileSchemaRegistry:
    current_version = "1.0.0"

    def validate_document(
        self,
        key: str,
        document: dict[str, Any],
        *,
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if key not in PROFILE_TYPES:
            raise KeyError(f"unknown profile document: {key}")
        if not isinstance(document, dict):
            raise ValueError("profile document must be an object")
        if len(json.dumps(document, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
            raise ValueError("profile document exceeds 512 KiB")
        candidate = deepcopy(document)
        version = str(candidate.get("schema_version") or self.current_version)
        try:
            major = int(version.split(".", 1)[0])
        except ValueError as exc:
            raise ValueError("invalid profile schema_version") from exc
        if major > 1:
            raise ValueError(f"unsupported future profile schema: {version}")
        candidate["schema_version"] = self.current_version
        candidate["profile_type"] = PROFILE_TYPES[key]
        if current is not None:
            candidate["revision"] = int(current.get("revision", 0))
            if "updated_at" in current:
                candidate["updated_at"] = current["updated_at"]
        for section, expected_type in REQUIRED_SECTIONS[key].items():
            if not isinstance(candidate.get(section), expected_type):
                raise ValueError(f"{key}.{section} must be an object")
        for field in DEFAULT_MEMORY_REGISTRY.fields:
            if field.target != key:
                continue
            try:
                value = _read(candidate, field.path)
            except (KeyError, TypeError):
                raise ValueError(f"missing registered field: {key}:{field.path}") from None
            if field.value_kind == "list" and not isinstance(value, list):
                raise ValueError(f"{key}:{field.path} must be a list")
            if field.value_kind == "scalar" and isinstance(value, (dict, list)):
                raise ValueError(f"{key}:{field.path} must be a scalar")
        _validate_safe_json(candidate)
        return candidate


DEFAULT_PROFILE_SCHEMA = ProfileSchemaRegistry()

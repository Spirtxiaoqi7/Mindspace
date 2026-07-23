"""Deterministic ASR vocabulary compiled from manual entries and profile JSON.

The vocabulary is intentionally kept outside the LLM prompt.  It is used only
for decoder hints, final-text correction, diagnostics, and user editing.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from mindspace_graph.adapters.file_storage import _atomic_json
from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY

PRIORITIES = {"low": 30, "medium": 65, "high": 90, "critical": 100}
MANAGED_FIELDS = {
    "user.identity.name",
    "user.communication.names",
    "agent.identity.name",
    "runtime.session.entities",
}
SKIP_KEYS = {"schema_version", "profile_type", "revision", "updated_at"}
TERM_SPLIT = re.compile(r"[\s，。！？；：、,.!?;:（）()\[\]{}<>《》\"“”'‘’/\\|]+")
LATIN_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+_.-]{1,31}")

SYSTEM_ENTRIES = (
    ("Mindspace", ["mind space", "曼德斯佩斯"], "high", "产品名称"),
    ("LangGraph", ["lang graph", "兰格拉夫"], "high", "技术名词"),
    ("RAG", ["拉格"], "high", "技术名词"),
    ("MCP", [], "high", "技术名词"),
    ("CosyVoice", ["cosy voice", "扣子voice"], "high", "技术名词"),
    ("GPT-SoVITS", ["GPT SoVITS", "搜维茨", "SoVITS"], "high", "技术名词"),
    ("FunASR", ["fun asr"], "high", "技术名词"),
    ("Paraformer", ["para former"], "high", "技术名词"),
    ("AudioWorklet", ["audio worklet"], "medium", "技术名词"),
    ("应该", ["因该"], "low", "日常易错词"),
    ("再见", ["在见"], "low", "日常易错词"),
    ("待会儿", ["带会儿"], "low", "日常易错词"),
    ("没关系", [], "low", "日常表达"),
    ("等一下", [], "low", "日常表达"),
    ("然后呢", [], "low", "日常表达"),
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_pointer(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for token in path.strip("/").split("/"):
        if not isinstance(current, dict):
            return None
        current = current.get(token)
    return current


def _text_terms(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    found: list[str] = []
    for item in values:
        if isinstance(item, dict):
            for key, child in item.items():
                if key not in SKIP_KEYS:
                    found.extend(_text_terms(child))
            continue
        if item is None or isinstance(item, bool):
            continue
        text = str(item).strip()
        if not text:
            continue
        if 2 <= len(text) <= 32:
            found.append(text)
        else:
            found.extend(part for part in TERM_SPLIT.split(text) if 2 <= len(part) <= 16)
            found.extend(LATIN_TERM.findall(text))
    return list(dict.fromkeys(found))


def _walk_leaves(value: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        leaves: list[tuple[str, Any]] = []
        for key, child in value.items():
            if key in SKIP_KEYS:
                continue
            leaves.extend(_walk_leaves(child, f"{path}/{key}"))
        return leaves
    if isinstance(value, list):
        return [(path, value)]
    return [(path, value)]


def _normalize_entry(raw: dict[str, Any], *, source: str = "manual") -> dict[str, Any]:
    term = str(raw.get("term") or "").strip()
    if not term or len(term) > 64:
        raise ValueError("词条必须为 1-64 个字符")
    aliases = []
    for alias in raw.get("aliases") or []:
        value = str(alias).strip()
        if value and value != term and value not in aliases and len(value) <= 64:
            aliases.append(value)
    priority = str(raw.get("priority") or "medium").lower()
    if priority not in PRIORITIES:
        priority = "medium"
    return {
        "id": str(raw.get("id") or uuid4().hex),
        "term": term,
        "aliases": aliases[:20],
        "priority": priority,
        "weight": PRIORITIES[priority],
        "scope": str(raw.get("scope") or "global")[:32],
        "category": str(raw.get("category") or "个人词表")[:64],
        "source": source,
        "source_field": str(raw.get("source_field") or "")[:160],
        "enabled": bool(raw.get("enabled", True)),
        "hit_count": max(0, int(raw.get("hit_count") or 0)),
        "updated_at": str(raw.get("updated_at") or _now()),
        "read_only": source != "manual",
    }


class ASRVocabularyStore:
    """Atomic manual vocabulary plus a live, revision-based profile projection."""

    def __init__(self, path: Path, profiles: Any) -> None:
        self.path = path
        self.history_path = path.with_name("correction-history.jsonl")
        self.profiles = profiles
        self._lock = RLock()
        self._manual = {"schema_version": "1.0.0", "revision": 0, "entries": []}
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    entries = [
                        _normalize_entry(item)
                        for item in value.get("entries", [])
                        if isinstance(item, dict)
                    ]
                    self._manual = {
                        "schema_version": "1.0.0",
                        "revision": max(0, int(value.get("revision") or 0)),
                        "entries": entries,
                    }
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        else:
            _atomic_json(path, self._manual)

    def _system_entries(self) -> list[dict[str, Any]]:
        return [
            _normalize_entry(
                {
                    "id": f"system-{index}",
                    "term": term,
                    "aliases": aliases,
                    "priority": priority,
                    "category": category,
                    "scope": "global",
                },
                source="system",
            )
            for index, (term, aliases, priority, category) in enumerate(SYSTEM_ENTRIES)
        ]

    def _profile_entries(self) -> tuple[list[dict[str, Any]], dict[str, int]]:
        bundle = self.profiles.load_bundle()
        documents = {
            "user_profile": bundle.user_profile,
            "ai_profile": bundle.ai_profile,
            "runtime_state": bundle.runtime_state,
        }
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        registered_paths: set[tuple[str, str]] = set()
        for field in DEFAULT_MEMORY_REGISTRY.fields:
            registered_paths.add((field.target, field.path))
            value = _read_pointer(documents[field.target], field.path)
            for term in _text_terms(value):
                key = (term.casefold(), field.field_code)
                if key in seen:
                    continue
                seen.add(key)
                priority = "high" if field.field_code in MANAGED_FIELDS else "low"
                entries.append(
                    _normalize_entry(
                        {
                            "id": f"profile-{field.field_code}-{len(entries)}",
                            "term": term,
                            "priority": priority,
                            "category": "人物与专名" if priority == "high" else field.category,
                            "scope": field.scope,
                            "source_field": f"{field.target}:{field.path}",
                        },
                        source="profile",
                    )
                )
        for target, document in documents.items():
            for path, value in _walk_leaves(document):
                if (target, path) in registered_paths:
                    continue
                for term in _text_terms(value):
                    key = (term.casefold(), f"{target}:{path}")
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append(
                        _normalize_entry(
                            {
                                "id": f"profile-unregistered-{len(entries)}",
                                "term": term,
                                "priority": "low",
                                "category": "JSON 其他字段",
                                "scope": "session" if target == "runtime_state" else "global",
                                "source_field": f"{target}:{path}",
                            },
                            source="profile",
                        )
                    )
        return entries, dict(bundle.revisions)

    def snapshot(self, *, include_entries: bool = True) -> dict[str, Any]:
        with self._lock:
            manual = deepcopy(self._manual)
        profile_entries, revisions = self._profile_entries()
        entries = [*self._system_entries(), *profile_entries, *manual["entries"]]
        enabled = [item for item in entries if item["enabled"]]
        enabled.sort(key=lambda item: (-int(item["weight"]), str(item["term"]).casefold()))

        # Only high-value terms are sent to the streaming decoder.  The complete
        # list remains available to the deterministic final-text corrector.
        decoder_hotwords = list(
            dict.fromkeys(item["term"] for item in enabled if int(item["weight"]) >= 65)
        )[:96]
        explicit: dict[str, str] = {}
        fuzzy_targets: list[dict[str, Any]] = []
        for item in reversed(enabled):
            for alias in item["aliases"]:
                explicit[alias] = item["term"]
        for item in enabled:
            if len(item["term"]) >= 2:
                fuzzy_targets.append(
                    {
                        "term": item["term"],
                        "priority": item["priority"],
                        "threshold": {
                            "critical": 0.80,
                            "high": 0.84,
                            "medium": 0.90,
                            "low": 0.96,
                        }[item["priority"]],
                        "source": item["source"],
                        "source_field": item["source_field"],
                    }
                )
        fingerprint_source = {
            "manual_revision": manual["revision"],
            "profile_revisions": revisions,
            "terms": [(item["term"], item["aliases"], item["weight"]) for item in enabled],
        }
        revision = hashlib.sha256(
            json.dumps(fingerprint_source, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        result: dict[str, Any] = {
            "schema_version": "1.0.0",
            "revision": revision,
            "manual_revision": manual["revision"],
            "profile_revisions": revisions,
            "decoder_hotwords": decoder_hotwords,
            "explicit": explicit,
            "fuzzy_targets": fuzzy_targets[:500],
            "counts": {
                "manual": len(manual["entries"]),
                "profile": len(profile_entries),
                "system": len(SYSTEM_ENTRIES),
                "enabled": len(enabled),
            },
        }
        if include_entries:
            result["entries"] = entries
        return result

    def replace_manual(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        if len(entries) > 500:
            raise ValueError("个人词表最多 500 条")
        normalized = [_normalize_entry(item) for item in entries]
        terms = [item["term"].casefold() for item in normalized]
        if len(terms) != len(set(terms)):
            raise ValueError("个人词表中存在重复标准词")
        with self._lock:
            self._manual = {
                "schema_version": "1.0.0",
                "revision": int(self._manual["revision"]) + 1,
                "entries": normalized,
            }
            _atomic_json(self.path, self._manual)
        return self.snapshot()

    def record_correction(self, raw_text: str, corrected_text: str) -> dict[str, Any]:
        wrong = raw_text.strip()
        right = corrected_text.strip()
        if not wrong or not right or wrong == right:
            raise ValueError("原始识别和正确写法必须不同")
        if len(wrong) > 64 or len(right) > 64:
            raise ValueError("快捷纠偏仅支持 64 字符以内的词或短语")
        with self._lock:
            entries = deepcopy(self._manual["entries"])
            target = next(
                (item for item in entries if item["term"].casefold() == right.casefold()),
                None,
            )
            if target is None:
                target = _normalize_entry(
                    {
                        "term": right,
                        "aliases": [wrong],
                        "priority": "critical",
                        "category": "纠偏记录",
                    }
                )
                entries.append(target)
            elif wrong not in target["aliases"]:
                target["aliases"].append(wrong)
                target["priority"] = "critical"
                target["weight"] = PRIORITIES["critical"]
                target["updated_at"] = _now()
            self._manual = {
                "schema_version": "1.0.0",
                "revision": int(self._manual["revision"]) + 1,
                "entries": entries,
            }
            _atomic_json(self.path, self._manual)
        return self.snapshot()

    def test_text(self, text: str) -> dict[str, Any]:
        snapshot = self.snapshot(include_entries=False)
        updated = text
        matches: list[dict[str, Any]] = []
        for wrong in sorted(snapshot["explicit"], key=len, reverse=True):
            right = snapshot["explicit"][wrong]
            if wrong not in updated:
                continue
            matches.append(
                {"from": wrong, "to": right, "score": 1.0, "source": "explicit"}
            )
            updated = updated.replace(wrong, right)
        return {
            "raw_text": text,
            "corrected_text": updated,
            "matches": matches,
            "vocabulary_revision": snapshot["revision"],
        }

    def record_observation(self, data: dict[str, Any], *, event: str) -> None:
        """Persist ASR correction metadata without exposing it to the LLM prompt."""

        raw_text = str(data.get("raw_text") or data.get("text") or "").strip()
        corrected_text = str(data.get("text") or "").strip()
        if not raw_text and not corrected_text:
            return
        matches = data.get("correction_matches")
        record = {
            "recorded_at": _now(),
            "event": event,
            "raw_text": raw_text[:4000],
            "corrected_text": corrected_text[:4000],
            "matches": matches[:50] if isinstance(matches, list) else [],
            "vocabulary_revision": str(data.get("vocabulary_revision") or "")[:64],
        }
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            if self.history_path.exists() and self.history_path.stat().st_size >= 2 * 1024 * 1024:
                archived = self.history_path.with_suffix(".jsonl.1")
                archived.unlink(missing_ok=True)
                self.history_path.replace(archived)
            with self.history_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)

    def correction_history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        with self._lock:
            lines = self.history_path.read_text(encoding="utf-8").splitlines()
        result: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 500)) :]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result

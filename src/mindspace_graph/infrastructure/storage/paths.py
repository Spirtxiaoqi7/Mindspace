"""Deterministic path helpers shared by file-backed repositories."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol


class _SubstitutionPattern(Protocol):
    def sub(self, repl: str, string: str, count: int = 0) -> str: ...


def safe_json_stem(document_id: str, *, unsafe: _SubstitutionPattern, fallback: str) -> str:
    """Return the existing repository-safe stem for an external document id."""

    return unsafe.sub("-", document_id).strip(".-") or fallback


def hashed_json_document_path(
    root: Path,
    document_id: str,
    *,
    unsafe: _SubstitutionPattern,
    fallback: str,
    stem_limit: int,
) -> Path:
    """Build a bounded readable stem plus full SHA-256 identity path."""

    stem = safe_json_stem(document_id, unsafe=unsafe, fallback=fallback)[:stem_limit]
    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
    return root / f"{stem}-{digest}.json"


def legacy_json_document_path(
    root: Path,
    document_id: str,
    *,
    unsafe: _SubstitutionPattern,
    fallback: str,
) -> Path:
    """Build the unbounded legacy JSON path used during compatibility reads."""

    stem = safe_json_stem(document_id, unsafe=unsafe, fallback=fallback)
    return root / f"{stem}.json"


__all__ = [
    "hashed_json_document_path",
    "legacy_json_document_path",
    "safe_json_stem",
]

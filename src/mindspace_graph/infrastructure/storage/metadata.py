"""Metadata primitives shared by file-backed repositories."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    """Return the existing UTC ISO-8601 timestamp representation."""

    return datetime.now(UTC).isoformat()


__all__ = ["utc_now_iso"]

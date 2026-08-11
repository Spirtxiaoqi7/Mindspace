"""Single source of truth for packaged frontend resources."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
STATIC_APP_ROOT = PACKAGE_ROOT / "static" / "app"
BUILTIN_ART_ARCHIVE_ROOT = STATIC_APP_ROOT / "archive"
BUILTIN_ART_MANIFEST = BUILTIN_ART_ARCHIVE_ROOT / "manifest.json"


__all__ = ["STATIC_APP_ROOT", "BUILTIN_ART_ARCHIVE_ROOT", "BUILTIN_ART_MANIFEST"]

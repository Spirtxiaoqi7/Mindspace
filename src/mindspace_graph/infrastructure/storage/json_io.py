"""Low-level JSON file operations shared by storage adapters."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_json_write(path: Path, value: Any) -> None:
    """Write formatted JSON through an fsynced temporary file and atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_json(path: Path) -> Any:
    """Read a UTF-8 JSON document without changing its native exceptions."""

    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


__all__ = ["atomic_json_write", "read_json"]
